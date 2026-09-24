"""Shared configuration, durable metadata and measurements."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path


def utc():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def write_json(path, value):
    import tempfile
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix=p.name+'.',suffix='.tmp',dir=p.parent)
    temp=Path(name)
    try:
        with os.fdopen(descriptor,'w') as f:
            json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(p)
    finally:
        temp.unlink(missing_ok=True)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_config(path):
    import yaml
    c = yaml.safe_load(Path(path).read_text())
    if c.get('schema_version') != 1:
        raise ValueError('Expected schema_version: 1')
    for name in (c['study_id'], c['runtime']['prefix']):
        if not re.fullmatch('[a-z][a-z0-9-]{0,48}', name):
            raise ValueError('Study/prefix must be short lowercase identifiers')
    ids = [v['id'] for v in c['variants']]
    if len(set(ids)) != len(ids) or any(not re.fullmatch('[a-z][a-z0-9_]*', x) for x in ids):
        raise ValueError('Variant IDs must be unique safe identifiers')
    if c['workload']['max_users'] < 1 or c['workload']['parallel_tiles'] < 1:
        raise ValueError('User/concurrency limits must be positive')
    for p in c['study']['profiles']:
        if not 0 < p['heap_gib'] < p['memory_gib'] or p['cpus'] <= 0:
            raise ValueError('Heap must leave memory outside the JVM')
    for key in ('screening_users', 'capacity_users'):
        if any(n < 1 or n > c['workload']['max_users'] for n in c['study'][key]):
            raise ValueError(f'{key} exceeds max_users')
    if not 0 <= c['workload']['mixed_seed_fraction'] <= 1:
        raise ValueError('Invalid mixed seed fraction')
    return c


def percentile(values, q):
    if not values:
        return None
    a = sorted(values)
    pos = (len(a) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return a[lo] + (a[hi] - a[lo]) * (pos - lo)


@contextlib.contextmanager
def measured(path, metadata=None, temporary_paths=()):
    """Sample RSS, cgroup accounting, I/O, CPU and temporary space per operation."""
    import resource
    import threading
    import psutil
    start = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    proc = psutil.Process()
    peak = {'rss_bytes': 0, 'cgroup_bytes': 0, 'temporary_bytes':0}
    stop = threading.Event()
    def sample():
        while not stop.is_set():
            peak['rss_bytes'] = max(peak['rss_bytes'], proc.memory_info().rss)
            sizes=[]
            for entry in temporary_paths:
                try: sizes.append(Path(entry).stat().st_size)
                except FileNotFoundError: pass
            peak['temporary_bytes']=max(peak['temporary_bytes'],sum(sizes))
            try:
                peak['cgroup_bytes'] = max(peak['cgroup_bytes'], int(Path('/sys/fs/cgroup/memory.current').read_text()))
            except OSError:
                pass
            stop.wait(.2)
    t = threading.Thread(target=sample, daemon=True)
    t.start()
    result = {'schema_version': 1, 'started_at': utc(), **(metadata or {})}
    try:
        yield result
        result['status'] = 'complete'
    except BaseException as e:
        result.update(status='failed', error=f'{type(e).__name__}: {e}')
        raise
    finally:
        stop.set()
        t.join()
        after = resource.getrusage(resource.RUSAGE_SELF)
        result.update(wall_seconds=time.monotonic() - start,
                      cpu_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                      peak_rss_bytes=peak['rss_bytes'], peak_cgroup_bytes=peak['cgroup_bytes'],
                      peak_temporary_file_bytes=peak['temporary_bytes'],
                      block_input_bytes=(after.ru_inblock-before.ru_inblock)*512,
                      block_output_bytes=(after.ru_oublock-before.ru_oublock)*512,
                      finished_at=utc())
        write_json(path, result)
