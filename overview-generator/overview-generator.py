#!/usr/bin/env python3
"""Parallel external GeoTIFF overview generation; QGIS 3.28 / Python 3.7+.

QGIS console:
    import runpy
    og = runpy.run_path(r"C:\\GIS\\overview-generator.py")
    job = og["start"](r"D:\\rasters", storage="ssd")
    # Later: job.status(), job.cancel()

Existing external overviews are deleted. By default, embedded overviews are
also removed (this modifies the source TIFF). Recognized COGs are protected.
See README.md for dry-run, tuning, cancellation, and limitations.
"""

import argparse
import collections
import concurrent.futures
import contextlib
import datetime
import json
import logging
import math
import os
from pathlib import Path
import queue
import shutil
import socket
import sys
import threading
import time
import uuid

from osgeo import gdal

__version__ = "1.0.0"
_MIB = 1024 ** 2
_GIB = 1024 ** 3
_RESAMPLING = ("bilinear", "nearest", "average", "cubic", "cubicspline",
               "lanczos", "mode", "gauss", "rms")
_SIDECAR_SUFFIXES = (".ovr", ".ovr.aux.xml", ".ovr.msk", ".ovr.msk.aux.xml",
                     ".msk.ovr", ".msk.ovr.aux.xml")


class _Cancelled(Exception):
    pass


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("{} must be a positive integer".format(name))
    return value


def _duration(seconds):
    if seconds is None or not math.isfinite(seconds):
        return "calculating"
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return ("{}d ".format(days) if days else "") + "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)


@contextlib.contextmanager
def _gdal_options(options):
    # These are per-worker settings. Never change QGIS's global GDAL settings
    # (especially its process-wide block cache or exception mode).
    getter = getattr(gdal, "GetThreadLocalConfigOption", None)
    if getter is None:
        # Older Python bindings expose only the effective-value getter.
        # These contexts run on our own worker threads, which are discarded
        # after the run; restoring a local copy of an inherited value is safe.
        getter = gdal.GetConfigOption
    previous = {key: getter(key) for key in options}
    try:
        for key, value in options.items():
            gdal.SetThreadLocalConfigOption(key, str(value))
        yield
    finally:
        for key, value in previous.items():
            gdal.SetThreadLocalConfigOption(key, value)


def _open(path, update=False):
    flags = gdal.OF_RASTER | (gdal.OF_UPDATE if update else gdal.OF_READONLY)
    dataset = gdal.OpenEx(str(path), flags, allowed_drivers=["GTiff"])
    if dataset is None:
        raise RuntimeError(gdal.GetLastErrorMsg() or "Cannot open as a GeoTIFF")
    return dataset


def _levels(width, height, explicit, min_size):
    if explicit is None:
        levels, factor = [], 2
        while max(width, height) > min_size:
            levels.append(factor)
            if max((width + factor - 1) // factor,
                   (height + factor - 1) // factor) <= min_size:
                break
            factor *= 2
    else:
        levels = explicit
    # Different factors can collapse to the same size on very small rasters.
    result, seen = [], {(width, height)}
    for factor in levels:
        size = ((width + factor - 1) // factor, (height + factor - 1) // factor)
        if size not in seen:
            seen.add(size)
            result.append(factor)
    return result


def _sidecars(path):
    return [Path(str(path) + suffix) for suffix in _SIDECAR_SUFFIXES]


def _delete_sidecars(path):
    removed = []
    for sidecar in _sidecars(path):
        if sidecar.is_symlink():
            raise RuntimeError("Refusing to delete a symlink sidecar: {}".format(sidecar))
        try:
            sidecar.unlink()
            removed.append(str(sidecar))
        except FileNotFoundError:
            pass
    return removed


class OverviewJob:
    """An asynchronous job. Use start() in QGIS or run() in a terminal."""

    def __init__(self, directory, resampling="bilinear", workers=None,
                 storage="auto", threads_per_file=None, levels=None,
                 min_size=256, compression="DEFLATE", compression_level=1,
                 bigtiff="YES", remove_internal=True, dry_run=False,
                 progress_interval=5.0, log_dir=None, min_free_gb=1.0):
        self.directory = Path(directory).expanduser().resolve()
        if not self.directory.is_dir():
            raise ValueError("Not a directory: {}".format(self.directory))
        if int(gdal.VersionInfo("VERSION_NUM")) < 3040000:
            raise RuntimeError("GDAL 3.4+ is required; this build is {}".format(gdal.VersionInfo("RELEASE_NAME")))
        if not hasattr(gdal, "SetThreadLocalConfigOption"):
            raise RuntimeError("This GDAL build lacks thread-local configuration")
        # Driver initialization is done here, before launching worker threads.
        if gdal.GetDriverByName("GTiff") is None:
            raise RuntimeError("The GDAL GeoTIFF driver is unavailable")
        resampling = str(resampling).lower()
        compression, bigtiff = str(compression).upper(), str(bigtiff).upper()
        if resampling not in _RESAMPLING:
            raise ValueError("resampling must be one of {}".format(", ".join(_RESAMPLING)))
        if storage not in ("auto", "hdd", "network", "ssd", "nvme"):
            raise ValueError("storage must be auto, hdd, network, ssd, or nvme")
        if compression not in ("DEFLATE", "LZW", "NONE"):
            raise ValueError("compression must be DEFLATE, LZW, or NONE")
        _positive_int(compression_level, "compression_level")
        if compression_level > 9:
            raise ValueError("compression_level must be 1..9")
        if bigtiff not in ("YES", "IF_SAFER", "IF_NEEDED", "NO"):
            raise ValueError("Invalid bigtiff policy")
        _positive_int(min_size, "min_size")
        if not math.isfinite(progress_interval) or progress_interval < 0.5:
            raise ValueError("progress_interval must be finite and >= 0.5 seconds")
        if not math.isfinite(min_free_gb) or min_free_gb < 0:
            raise ValueError("min_free_gb must be finite and >= 0")
        if not isinstance(remove_internal, bool) or not isinstance(dry_run, bool):
            raise ValueError("remove_internal and dry_run must be bools")
        if levels is not None:
            levels = list(levels)
            if not levels or any(isinstance(n, bool) or not isinstance(n, int) or n < 2 or n > 2147483647 for n in levels):
                raise ValueError("levels must contain integer factors in 2..2147483647")
            levels = sorted(set(levels))
        cpu_budget = max(1, (os.cpu_count() or 2) - 1)
        default_workers = {"auto": 4, "hdd": 1, "network": 2, "ssd": 4, "nvme": 8}[storage]
        workers = min(default_workers, cpu_budget) if workers is None else _positive_int(workers, "workers")
        threads_per_file = min(4, max(1, cpu_budget // workers)) if threads_per_file is None else _positive_int(threads_per_file, "threads_per_file")
        self.options = dict(resampling=resampling, workers=workers, storage=storage,
                            threads_per_file=threads_per_file, levels=levels,
                            min_size=min_size, compression=compression,
                            compression_level=compression_level, bigtiff=bigtiff,
                            remove_internal=remove_internal, dry_run=dry_run,
                            progress_interval=float(progress_interval), min_free_gb=float(min_free_gb))
        self.log_dir = Path(log_dir).expanduser().resolve() if log_dir else self.directory / "overview-generator-logs"
        self.run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.log_path = self.log_dir / (self.run_id + ".log")
        self.results_path = self.log_dir / (self.run_id + ".results.jsonl")
        self.summary_path = self.log_dir / (self.run_id + ".summary.json")
        self._stop = threading.Event()
        self._done = threading.Event()
        self._mutex = threading.RLock()
        self._disk_mutex = threading.Lock()
        self._reserved = 0
        self._active = {}
        self._counts = collections.Counter()
        self._total = self._total_bytes = self._settled_bytes = self._successful_bytes = 0
        self._estimated_output_bytes = self._output_bytes = 0
        self._phase = "created"
        self._fatal = None
        self._started = self._processing_started = self._ended = None
        self._messages = queue.Queue(maxsize=200)
        self._thread = self._timer = self._app = None
        self._logger = None

    @property
    def done(self):
        return self._done.is_set()

    def cancel(self):
        """Request cooperative cancellation; do not kill a GDAL write midway."""
        self._stop.set()

    def _check_cancel(self):
        if self._stop.is_set():
            raise _Cancelled("Cancellation requested")

    def _say(self, message, level=logging.INFO):
        if self._logger:
            self._logger.log(level, message)
        try:
            self._messages.put_nowait(message)
        except queue.Full:
            # The complete history remains in the log if the GUI is busy.
            pass

    def _drain(self):
        # Only the QGIS main thread (or the blocking CLI caller) prints.
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            print("[overview-generator] " + message, flush=True)
        if self.done and self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
            try:
                self._app.aboutToQuit.disconnect(self.cancel)
            except (TypeError, RuntimeError):
                pass
            getattr(sys, "_overview_generator_live_jobs", []).remove(self)

    def status(self):
        """Return a thread-safe progress snapshot without blocking QGIS."""
        with self._mutex:
            now = self._ended or time.monotonic()
            elapsed = now - self._started if self._started else 0.0
            active_bytes = sum(v["bytes"] * v["progress"] for v in self._active.values())
            accounted = min(self._total_bytes, self._settled_bytes + active_bytes)
            fraction = accounted / self._total_bytes if self._total_bytes else 0.0
            processing = now - self._processing_started if self._processing_started else 0.0
            useful = self._successful_bytes + active_bytes
            rate = useful / processing if processing >= 5 and useful > 0 else None
            eta = max(0.0, self._total_bytes - accounted) / rate if rate else None
            if self._phase in ("finished", "finished_with_errors"):
                eta = 0.0
            if self._phase in ("cancelled", "failed"):
                eta = None
            return dict(phase=self._phase, total=self._total, completed=sum(self._counts.values()),
                        counts=dict(self._counts), active=len(self._active),
                        active_files={p: round(v["progress"] * 100, 1) for p, v in self._active.items()},
                        total_source_bytes=self._total_bytes, progress_percent=round(fraction * 100, 2),
                        estimated_output_bytes=self._estimated_output_bytes,
                        output_bytes=self._output_bytes,
                        elapsed_seconds=round(elapsed, 2), eta_seconds=eta,
                        source_equivalent_mib_per_second=rate / _MIB if rate else None,
                        cancel_requested=self._stop.is_set(), fatal_error=self._fatal,
                        log_path=str(self.log_path), results_path=str(self.results_path),
                        summary_path=str(self.summary_path), options=dict(self.options))

    def _report(self):
        s = self.status()
        c = s["counts"]
        eta = s["eta_seconds"]
        try:
            finish = (datetime.datetime.now() + datetime.timedelta(seconds=eta)).strftime("%Y-%m-%d %H:%M:%S") if eta is not None else "calculating"
        except (OverflowError, ValueError):
            finish = "unknown"
        self._say("{} | {}/{} files | {:.1f}% by source bytes | active {} | OK {} / failed {} / skipped {} / planned {} / cancelled {} | elapsed {} | ETA {} | finish ~{} (local)".format(
            s["phase"], s["completed"], s["total"], s["progress_percent"], s["active"],
            c.get("ok", 0), c.get("failed", 0), c.get("skipped", 0), c.get("planned", 0), c.get("cancelled", 0),
            _duration(s["elapsed_seconds"]), _duration(eta), finish))

    def _discover(self):
        files, identities = [], set()
        with os.scandir(str(self.directory)) as entries:
            for entry in entries:
                self._check_cancel()
                if Path(entry.name).suffix.lower() not in (".tif", ".tiff", ".geotiff"):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    self._logger.warning("Ignored non-regular file or symlink: %s", entry.path)
                    continue
                stat = entry.stat(follow_symlinks=False)
                identity = (stat.st_dev, stat.st_ino)
                # Hard links have distinct sidecars but share TIFF internals.
                # Exclude all multiply-linked TIFFs to prevent shared mutations.
                if stat.st_nlink > 1 or (stat.st_ino and identity in identities):
                    self._logger.warning("Ignored hard-linked TIFF: %s", entry.path)
                    continue
                identities.add(identity)
                files.append((Path(entry.path), stat.st_size))
                if len(files) % 1000 == 0:
                    self._say("Scanning: {} GeoTIFF candidates found".format(len(files)))
        # Start large rasters early so one huge raster is less likely to be last.
        return sorted(files, key=lambda item: (-item[1], str(item[0]).casefold()))

    @contextlib.contextmanager
    def _reserve_space(self, amount):
        with self._disk_mutex:
            free = shutil.disk_usage(str(self.directory)).free
            required = self._reserved + amount + self.options["min_free_gb"] * _GIB
            if free < required:
                raise RuntimeError("Insufficient free space: {:.2f} GiB free; {:.2f} GiB required including in-flight reservations and headroom".format(free / _GIB, required / _GIB))
            self._reserved += amount
        try:
            yield
        finally:
            with self._disk_mutex:
                self._reserved -= amount

    def _process(self, path, source_bytes):
        began = time.monotonic()
        result = dict(path=str(path), source_bytes=source_bytes, status="failed",
                      levels=[], internal_overviews_removed=False, deleted_sidecars=[],
                      overview_bytes=0, error=None, warnings=[])
        errors = collections.deque(maxlen=30)
        ds = None
        building = False

        def error_handler(error_class, error_number, message):
            if error_class >= gdal.CE_Warning:
                errors.append((error_class, message))

        last_callback = [0.0]

        def progress(fraction, message, data):
            if self._stop.is_set():
                return 0
            now = time.monotonic()
            if now - last_callback[0] >= 0.2 or fraction >= 1:
                with self._mutex:
                    if str(path) in self._active:
                        # Reserve the last 1% for closing and verifying output.
                        self._active[str(path)]["progress"] = max(0.0, min(0.99, float(fraction) * 0.99))
                last_callback[0] = now
            return 1

        opts = dict(GDAL_NUM_THREADS=self.options["threads_per_file"],
                    GDAL_DISABLE_READDIR_ON_OPEN="TRUE", USE_RRD="NO",
                    TIFF_USE_OVR="NO", COMPRESS_OVERVIEW=self.options["compression"],
                    BIGTIFF_OVERVIEW=self.options["bigtiff"],
                    ZLEVEL_OVERVIEW=self.options["compression_level"],
                    GDAL_TIFF_OVR_BLOCKSIZE="256", INTERLEAVE_OVERVIEW="BAND")
        with _gdal_options(opts):
            gdal.PushErrorHandler(error_handler)
            try:
                self._check_cancel()
                ds = _open(path)
                width, height, bands = ds.RasterXSize, ds.RasterYSize, ds.RasterCount
                if not bands or not width or not height:
                    raise RuntimeError("Raster has no usable pixels or bands")
                types = [ds.GetRasterBand(b).DataType for b in range(1, bands + 1)]
                if self.options["resampling"] != "nearest" and any(ds.GetRasterBand(b).GetColorTable() is not None for b in range(1, bands + 1)):
                    raise RuntimeError("Palette-indexed TIFF requires resampling='nearest'; expand to RGB for interpolated overviews")
                levels = _levels(width, height, self.options["levels"], self.options["min_size"])
                result.update(width=width, height=height, bands=bands, levels=levels)
                ds = None
                if not levels:
                    result.update(status="skipped", reason="Raster is at or below min_size; existing overviews retained")
                    return result
                # Hide sidecars ONLY for this metadata probe, so internal levels
                # are distinguishable from .ovr/RRD/PAM overviews.
                with _gdal_options({"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_PAM_ENABLED": "NO", "TIFF_USE_OVR": "NO"}):
                    ds = _open(path)
                    internal = any(ds.GetRasterBand(b).GetOverviewCount() for b in range(1, bands + 1))
                    cog = ds.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE") == "COG"
                    ds = None
                result["had_internal_overviews"] = bool(internal)
                if cog:
                    result.update(status="skipped", reason="Recognized Cloud Optimized GeoTIFF is protected; convert a copy to ordinary GTiff first")
                    return result
                if internal and not self.options["remove_internal"]:
                    result.update(status="skipped", reason="Embedded overviews present and remove_internal=False")
                    return result
                for sidecar in _sidecars(path):
                    if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
                        raise RuntimeError("Unsafe sidecar path: {}".format(sidecar))
                # Tiled output estimate, including edge padding, TIFF structures,
                # and an allowance for incompressible data and masks.
                sample_bytes = sum((gdal.GetDataTypeSize(t) + 7) // 8 for t in types)
                tiled_pixels = sum((((width + n - 1) // n + 255) // 256) * (((height + n - 1) // n + 255) // 256) * 256 * 256 for n in levels)
                estimated = int(tiled_pixels * (sample_bytes + bands) * 1.15) + 16 * _MIB
                result["estimated_output_bytes"] = estimated
                result["existing_sidecars"] = [str(p) for p in _sidecars(path) if p.exists()]
                if self.options["dry_run"]:
                    result.update(status="planned", would_remove_internal=bool(internal),
                                  reason="Would delete existing overviews and build external .ovr")
                    return result
                with self._reserve_space(estimated):
                    self._check_cancel()
                    result["deleted_sidecars"] = _delete_sidecars(path)
                    if internal:
                        self._logger.warning("Removing embedded overviews (source TIFF is modified): %s", path)
                        ds = _open(path, update=True)
                        rc = ds.BuildOverviews("NONE", [])
                        ds = None
                        if rc not in (None, 0):
                            raise RuntimeError("GDAL failed to remove embedded overviews")
                        result["internal_overviews_removed"] = True
                    self._check_cancel()
                    # Read-only is essential: GTiff otherwise writes internally.
                    ds = _open(path)
                    if any(ds.GetRasterBand(b).GetOverviewCount() for b in range(1, bands + 1)):
                        raise RuntimeError("Overviews remain after cleanup (possibly RRD/.aux or PAM references); resolve these manually")
                    predictor = 3 if all(gdal.GetDataTypeName(t) in ("Float32", "Float64") for t in types) else 2
                    if any(gdal.DataTypeIsComplex(t) for t in types) or len(set(types)) > 1:
                        predictor = 1
                    if self.options["compression"] == "NONE":
                        predictor = 1
                    building = True
                    with _gdal_options({"PREDICTOR_OVERVIEW": predictor}):
                        rc = ds.BuildOverviews(self.options["resampling"].upper(), levels, callback=progress)
                        ds.FlushCache()
                        ds = None  # GDAL 3.4/3.6: destruction closes and flushes.
                    self._check_cancel()
                    if rc not in (None, 0) or any(c >= gdal.CE_Failure for c, _ in errors):
                        raise RuntimeError("GDAL overview build/flush failed: " + "; ".join(m for _, m in errors))
                    ovr = Path(str(path) + ".ovr")
                    if not ovr.is_file() or not ovr.stat().st_size:
                        raise RuntimeError("GDAL did not create a nonempty external .ovr")
                    expected = {((width + n - 1) // n, (height + n - 1) // n) for n in levels}
                    ds = _open(path)
                    for b in range(1, bands + 1):
                        band = ds.GetRasterBand(b)
                        actual = {(band.GetOverview(i).XSize, band.GetOverview(i).YSize) for i in range(band.GetOverviewCount())}
                        band = None
                        if actual != expected:
                            raise RuntimeError("Overview dimensions do not match requested levels on band {}: {} != {}".format(b, actual, expected))
                    ds = None
                    result.update(status="ok", overview_bytes=ovr.stat().st_size)
                    building = False
            except Exception as exc:
                ds = None
                result.update(status="cancelled" if isinstance(exc, _Cancelled) or self._stop.is_set() else "failed", error=str(exc))
                if building:
                    try:
                        result["partial_output_deleted"] = _delete_sidecars(path)
                    except (OSError, RuntimeError) as cleanup_error:
                        result["error"] += "; partial output could not be removed: " + str(cleanup_error)
            finally:
                ds = None
                gdal.PopErrorHandler()
                result["warnings"] = [m for c, m in errors if c == gdal.CE_Warning]
                result["seconds"] = round(time.monotonic() - began, 3)
        return result

    @contextlib.contextmanager
    def _pool(self):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.options["workers"], thread_name_prefix="overview")
        try:
            yield pool
        except BaseException:
            # Ask active GDAL operations to stop before shutdown waits for them.
            self.cancel()
            raise
        finally:
            pool.shutdown(wait=True)

    def _coordinate(self):
        lock_path = self.directory / ".overview-generator.lock"
        owns_lock = False
        handler = None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._logger = logging.getLogger("overview-generator." + self.run_id)
            self._logger.setLevel(logging.INFO)
            self._logger.propagate = False
            handler = logging.FileHandler(str(self.log_path), encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
            self._logger.addHandler(handler)
            try:
                lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raise RuntimeError("Directory lock exists: {}. Another run may be active; inspect this file and remove it only after confirming the previous process has stopped.".format(lock_path))
            owns_lock = True
            with os.fdopen(lock_fd, "w", encoding="utf-8") as f:
                json.dump(dict(pid=os.getpid(), host=socket.gethostname(), run_id=self.run_id,
                               started=datetime.datetime.now().isoformat()), f)
            with self._mutex:
                self._phase = "scanning"
            self._say("Version {} | GDAL {} | {}".format(__version__, gdal.VersionInfo("RELEASE_NAME"), self.directory))
            self._say("Options: " + json.dumps(self.options, sort_keys=True))
            self._say("Log: {} | Results: {}".format(self.log_path, self.results_path))
            if not self.options["dry_run"]:
                self._say("Existing external overviews will be deleted; embedded overview removal is {}.".format("ENABLED (modifies source TIFFs)" if self.options["remove_internal"] else "disabled"))
            if self.options["workers"] * self.options["threads_per_file"] > (os.cpu_count() or 1):
                self._say("Configured CPU concurrency exceeds logical CPU count; reduce workers or threads_per_file if throughput falls.", logging.WARNING)
            files = self._discover()
            with self._mutex:
                self._total = len(files)
                self._total_bytes = sum(size for _, size in files)
                self._phase = "processing"
                self._processing_started = time.monotonic()
            self._say("Found {} files, {:.2f} GiB of source data; up to {} concurrent files.".format(len(files), self._total_bytes / _GIB, self.options["workers"]))
            pending, next_index = {}, 0
            last_report = 0.0
            with self.results_path.open("w", encoding="utf-8") as results_file:
                with self._pool() as pool:
                    while next_index < len(files) or pending:
                        while not self._stop.is_set() and next_index < len(files) and len(pending) < self.options["workers"]:
                            path, size = files[next_index]
                            next_index += 1
                            with self._mutex:
                                self._active[str(path)] = {"bytes": size, "progress": 0.0}
                            pending[pool.submit(self._process, path, size)] = (path, size)
                        if not pending:
                            break
                        finished, _ = concurrent.futures.wait(pending, timeout=0.25, return_when=concurrent.futures.FIRST_COMPLETED)
                        for future in finished:
                            path, size = pending.pop(future)
                            try:
                                result = future.result()
                            except Exception as exc:
                                result = dict(path=str(path), source_bytes=size, status="failed", error=repr(exc))
                            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                            results_file.flush()
                            status = result["status"]
                            self._logger.log(logging.ERROR if status == "failed" else logging.INFO, "%s %s", status.upper(), json.dumps(result, ensure_ascii=False))
                            with self._mutex:
                                self._active.pop(str(path), None)
                                self._counts[status] += 1
                                self._estimated_output_bytes += result.get("estimated_output_bytes", 0)
                                self._output_bytes += result.get("overview_bytes", 0)
                                if status != "cancelled":
                                    self._settled_bytes += size
                                if status == "ok":
                                    self._successful_bytes += size
                            if status == "failed" and self._counts["failed"] <= 10:
                                self._say("FAILED {}: {}".format(path.name, result.get("error")), logging.ERROR)
                        if time.monotonic() - last_report >= self.options["progress_interval"]:
                            self._report()
                            last_report = time.monotonic()
                    # Record every unstarted file as cancelled, without opening it.
                    for path, size in files[next_index:]:
                        results_file.write(json.dumps(dict(path=str(path), source_bytes=size,
                                                          status="cancelled", error="Not started: cancellation requested"), ensure_ascii=False) + "\n")
                        with self._mutex:
                            self._counts["cancelled"] += 1
            with self._mutex:
                self._phase = "cancelled" if self._stop.is_set() else ("finished_with_errors" if self._counts["failed"] else "finished")
        except _Cancelled:
            with self._mutex:
                self._phase = "cancelled"
        except Exception as exc:
            self._stop.set()
            with self._mutex:
                self._fatal, self._phase = str(exc), "failed"
            self._say("FATAL: " + str(exc), logging.ERROR)
        finally:
            self._ended = time.monotonic()
            # Executor shutdown has joined active workers even on a fatal error.
            with self._mutex:
                self._active.clear()
            if owns_lock:
                try:
                    lock_path.unlink()
                except OSError as exc:
                    self._say("Could not remove directory lock: " + str(exc), logging.WARNING)
            summary_written = False
            try:
                summary = self.status()
                summary.update(version=__version__, gdal=gdal.VersionInfo("RELEASE_NAME"), directory=str(self.directory), run_id=self.run_id)
                self.summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
                summary_written = True
            except OSError as exc:
                self._say("Could not write summary: " + str(exc), logging.ERROR)
            self._report()
            if summary_written:
                self._say("Summary: " + str(self.summary_path))
            if handler:
                self._logger.removeHandler(handler)
                handler.close()
            self._done.set()

    def _launch(self):
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._coordinate, name="overview-coordinator", daemon=False)
        self._thread.start()
        return self


def start(directory, **options):
    """Start in QGIS, return immediately, and print updates via a Qt timer.

    Call this on QGIS's main thread. Keep the returned job for status/cancel.
    Parameter descriptions and defaults are in README.md / OverviewJob.__init__.
    """
    from qgis.PyQt.QtCore import QCoreApplication, QThread, QTimer
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise RuntimeError("start() must run on the QGIS GUI thread; use run() outside QGIS")
    job = OverviewJob(directory, **options)
    if not job.options["dry_run"]:
        from qgis.core import QgsProject, QgsRasterLayer
        loaded = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsRasterLayer) and layer.providerType() == "gdal":
                source = Path(layer.source().split("|", 1)[0]).resolve()
                if source.parent == job.directory and source.suffix.lower() in (".tif", ".tiff", ".geotiff"):
                    loaded.append(source.name)
        if loaded:
            raise RuntimeError("Remove target TIFF layers from the QGIS project before rebuilding their overviews ({} loaded; e.g. {}). dry_run=True is allowed.".format(len(loaded), ", ".join(loaded[:3])))
    job._app = app
    job._timer = QTimer(app)
    job._timer.setInterval(250)
    job._timer.timeout.connect(job._drain)
    app.aboutToQuit.connect(job.cancel)
    if not hasattr(sys, "_overview_generator_live_jobs"):
        sys._overview_generator_live_jobs = []
    sys._overview_generator_live_jobs.append(job)
    job._timer.start()
    return job._launch()


def run(directory, **options):
    """Blocking terminal API. In the QGIS console use start() instead."""
    job = OverviewJob(directory, **options)._launch()
    while not job.done:
        try:
            job._drain()
            job._done.wait(0.2)
        except KeyboardInterrupt:
            job.cancel()
            print("Cancellation requested; waiting for GDAL to close its files.", flush=True)
    job._drain()
    return job.status()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="Flat directory of .tif/.tiff/.geotiff files")
    parser.add_argument("--resampling", default="bilinear", choices=_RESAMPLING)
    parser.add_argument("--storage", default="auto", choices=("auto", "hdd", "network", "ssd", "nvme"))
    parser.add_argument("--workers", type=int)
    parser.add_argument("--threads-per-file", type=int)
    parser.add_argument("--levels", type=int, nargs="+")
    parser.add_argument("--min-size", type=int, default=256)
    parser.add_argument("--compression", choices=("DEFLATE", "LZW", "NONE"), default="DEFLATE")
    parser.add_argument("--compression-level", type=int, default=1)
    parser.add_argument("--bigtiff", choices=("YES", "IF_SAFER", "IF_NEEDED", "NO"), default="YES")
    parser.add_argument("--keep-internal", action="store_true", help="Skip TIFFs with embedded overviews instead of removing them")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress-interval", type=float, default=5.0)
    parser.add_argument("--log-dir")
    parser.add_argument("--min-free-gb", type=float, default=1.0)
    args = vars(parser.parse_args(argv))
    args["remove_internal"] = not args.pop("keep_internal")
    try:
        result = run(**args)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if result["phase"] == "cancelled":
        return 130
    return 1 if result["phase"] in ("failed", "finished_with_errors") else 0


if __name__ == "__main__":
    sys.exit(main())
