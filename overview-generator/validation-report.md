# Native GDAL validation — 2026-09-24

**12/12 CLI checks, 7/7 standalone QGIS console checks and 3/3 Qt API checks
passed on Windows 11 with QGIS 4.2.0, Python 3.12.13 and GDAL 3.13.1.**
QGIS 3.28 was not found and remains untested.

Only generated data were used. The implementation remains byte-identical to
the submitted 1.0.0 script, SHA-256
`e8b340ad1fb0026c143b4eff327defd92c3d7bedcbc6a7ec2fe322268b67b5b6`.
No implementation defect was demonstrated. The original raw workstation
records remain local; published JSON replaces absolute test paths with
`<TEST_ROOT>`. This report summarizes that observed run, not a new benchmark.

## Command-line checks

| Check | Observed outcome |
| --- | --- |
| Bilinear dry run | Nine planned files; SHA-256, size and modification time unchanged for 12 source/sidecar files. |
| Bilinear matrix | All nine combinations of Byte / Float32 with -9999 NoData / three-band Byte and no / external / embedded overviews passed. |
| Average dry run | Same nine-way preservation and embedded-overview detection checks passed. |
| Average matrix | All nine files produced external overviews with correct dimensions, samples and NoData. |
| Rebuild | Nine bilinear pyramids replaced by average; all output hashes changed and samples matched references. |
| Independent pixels | Linear plane `0.5*x + 0.25*y`: exact expected interior 2x samples for both methods; 784 checked NoData cells retained per method. |
| Odd/narrow sizes | 1001×777 and 1×1025; explicit unordered/duplicate factors normalized to 2, 4, 8; every pixel matched external GDAL references. |
| Preservation options | Embedded file with `--keep-internal` and small file below automatic threshold were skipped without modification. |
| Corrupt input | Generated invalid TIFF failed, healthy sibling succeeded; exit 1, no partial corrupt output or leftover lock. |
| Parallelism | Three 4096×4096 three-band files succeeded; console observed `active 2`, one GDAL thread per file. |
| Cancellation | Native isolated-console Ctrl+C after two nonempty outputs appeared; exit 130; two partial outputs removed, two unstarted files recorded cancelled, source hashes unchanged. |
| Invalid arguments | `--workers 0` returned exit 2. |

The main matrices compared 7,372,800 overview samples. Rebuild compared another
3,686,400; odd/narrow cases compared 257,081. Separate direct-GDAL references
supplement the independent mathematical assertions. This is not independent
validation of the GDAL library itself.

Base pixels, types, NoData, geotransform and CRS were preserved. Source-file
hashes were unchanged except where removal of embedded overviews deliberately
changed TIFF structure. Reopening with sidecars hidden confirmed embedded
overviews were gone.

| Source | Factors | Dimensions |
| --- | --- | --- |
| 1024×768 | 2, 4 | 512×384; 256×192 |
| 1001×777 | 2, 4, 8 | 501×389; 251×195; 126×98 |
| 1×1025 | 2, 4, 8 | 1×513; 1×257; 1×129 |

## Test-reference correction

The first suite passed 11 checks and failed the narrow Float32 reference
comparison: the external 4x output differed from an internal-GDAL reference by
up to 0.00056458. A direct external-GDAL comparison matched every pixel at all
three levels for both odd/narrow images. The reference was corrected to use
external overviews. The affected check and full CLI suite passed on rerun.
No script change was justified by this finding.

## Separate start() and console coverage

The full QGIS desktop could not load `qgis_app.dll`: Windows Application
Control reported error 4551. No policy or installation was changed.

A real standalone `QgsApplication` hosting the installed QGIS console widget
passed loaded-layer rejection, immediate `start()` return, loaded-layer dry
run, two-file build with constant pixel verification, actual console updates
while a heartbeat timer advanced, preservation of GDAL global settings and
live-job cleanup, and immediate cancellation/timer cleanup. Optional QGIS 3D
imports also encountered an Application Control block; these tests still ran.

Three additional real Qt event-loop API checks passed: wrong-thread rejection,
successful build with queued printing only on the main thread, and immediate
cancellation/cleanup. Active-write cancellation was separately covered by CLI.

## Limits and reproduction

QGIS 3.28, Python 3.7 execution, the full desktop console, interactive map
rendering, production rasters, network shares, files over 4 GiB, masks/alpha,
palette/COG protection, alternate pyramids, other data types/resamplers,
resource exhaustion and power-loss recovery remain outside this validation.
Python 3.7 syntax parsing passed; it does not establish runtime compatibility.

See [tests/README.md](tests/README.md) to reproduce with detached local Windows
processes. Public evidence: [CLI](validation/cli-results.json),
[console](validation/console-results.json), [API](validation/api-results.json).
The original run ended with zero active test processes and zero leftover locks.

## Repository packaging check

After relocation into `overview-generator/`, the portable `run-windows.ps1`
launcher was exercised from a fresh Windows test directory on the same date.
All 12 CLI, seven console and three API checks passed again. The source script
hash remained unchanged. The collection's benchmark unit suite also passed
25 tests in a separate Linux virtual environment. No live benchmark services
or full container-image build were started for publication.
