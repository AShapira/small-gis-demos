# Completed GeoServer benchmark: scope and results

The local study completed on 2026-09-16 with 154 valid measurement cells at
10 human-paced users. This is a historical result summary, not a new benchmark
performed for the 0.1.0 publication. Full source imagery and raw request logs
remain in the original retained export/volumes and are not included here.

- 40 source-comparison, 12 cached-browsing, 24 core-capacity,
  48 extended-capacity, 6 delivery and 24 endurance cells.
- 41,203 views, 341,549 requests, two failed views overall and zero failures
  in two complete endurance cycles.
- 29 verified Copernicus archives; 5.134 GB of valid RGB samples; ten supported
  TIFF/COG configurations converted, pixel-validated and served.
- Smallest measured qualifying allocation: 2 vCPU / 4 GiB RAM / 2 GiB heap
  for uncompressed, PackBits and JPEG-80 COG at 10 users.
- ZSTD-9: 3.778 GB including overviews, 44.9% savings, lossless.
- JPEG-80: 0.412 GB, 94.0% savings, lossy.

Nominal TIFF outputs also reported COG layout, so these pairs do not isolate a
COG layout advantage. The full 100-user plan, dedicated cold-disk/first-access
tests and a long-lived single-JVM soak were not executed. Shared workstation
loads and two repeated runs limit generalization.

The pinned synthetic reader/renderer compatibility probes are in
[`evidence/`](../evidence/). They are separate from the 154 real-data cells.
Use `scripts/final_report.py` with a retained full study export to reconstruct
the final report; it rejects incomplete or extra measurement matrices.
