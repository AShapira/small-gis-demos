# VRT/JPEG overview validation

Validated on **2026-10-08** with the installed Windows QGIS 4.2.0 runtime:

- Python 3.12.13, 64-bit.
- GDAL 3.13.1, with its bundled GeoTIFF/JPEG support.
- **20 tests passed; zero failures, errors, or skips.**
- Persistent validation-run elapsed time: **19.359 seconds**. This is a test-suite
  measurement, not a throughput estimate for production imagery.

All inputs were generated specifically for the tests. No production rasters,
GeoServer instances, services, or security settings were modified.

## Coverage

| Area | Verified behavior |
| --- | --- |
| Full-resolution mosaic | Exact valid source samples, extent/grid, grayscale and RGB, deterministic overlap precedence, unchanged source hashes. |
| JPEG pyramid | Grayscale and RGB contents, automatic and explicit levels, non-consecutive factors, odd image dimensions, JPEG codec, tiled BigTIFF at every level. |
| Validity | Valid black pixels, gaps, explicit masks, scalar NoData, RGB NoData tuples, independent band masks, transparent overlap fall-through, lossless mask pyramid at every requested level. |
| Discovery | Recursive folders, duplicate canonical paths, input-folder precedence, uppercase extensions, spaces and Hebrew filenames. |
| Portability | Relative VRT references remain usable after relocating both inputs and outputs. |
| Scale | One mosaic from 2,000 generated 8 × 8 GeoTIFFs, spanning more sources than the configured 128-dataset pool. |
| Invalid input | Unreadable TIFFs, palettes, non-Byte types, alpha/mismatched bands, missing/different CRS, rotated/misaligned/different-resolution grids, empty input, invalid levels. |
| Output protection | Dry run writes nothing; existing artifacts/locks are retained; a source change prevents publication. |
| Failure handling | Cancellation during JPEG creation, initial masks, and lower mask levels; injected validation/publication failures; cleanup and subsequent reuse of the output name. |
| Native CLI | Subprocess success, dry-run and failure exit codes; native Windows Ctrl+Break cancellation exits with code 130 and releases output files. |

The initial test run exposed an incorrectly constructed palette fixture and
inconsistent photometric tags in three-band mask overviews. The fixture was
corrected and mask TIFF tags now remain consistent across levels. The final run
passed with no GDAL warnings in its output.

## Evidence and limits

The reusable runner is `tests/run_validation.py`. It creates a new directory
containing `results.json`, `unittest.log`, all generated fixtures and outputs,
and logs from native CLI invocations. The completed Windows run was copied to
the ignored repository directory `.validation/windows-20261008-072752/` beneath
this demo, retaining evidence outside the Windows temporary directory.
Machine-specific paths and generated rasters are not committed.

Tests exercise GDAL's readers and the standalone CLI. They do not constitute
QGIS desktop rendering or GeoServer publication tests. Other GDAL versions,
cross-drive/UNC deployment, multi-terabyte datasets, disk exhaustion, abrupt
power loss, and production throughput have not been validated. Runtime output
checks are structural and sampled; small synthetic tests additionally compare
complete arrays and validity masks.

The repository CI configuration now invokes this suite with Linux GDAL/NumPy.
That workflow has not been run remotely for these uncommitted changes; its
Windows-only Ctrl+Break test will be skipped on Linux.
