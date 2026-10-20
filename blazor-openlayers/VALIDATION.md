# Validation

## Checks recorded on 2026-10-13

- .NET SDK **10.0.401**, runtime/packages **10.0.12**: restore and Release publish
  on Linux, followed by a fresh source/package-cache restore from a local feed
  containing **32 NuGet packages** and publish with `--no-restore`.
- Native **Windows x64 / PowerShell 5.1**: parsed all scripts, ran connected
  `Prepare-Offline.ps1`, verified the official SDK SHA512, and prepared the
  source/SDK/feed transfer bundle with its SHA256 manifest.
- Native `Build-Offline.ps1`: verified that bundle, extracted its SDK, restored
  into a new empty cache using only the local feed in locked mode, and published
  Release. HTTP/HTTPS/ALL_PROXY pointed to an unavailable loopback port for this
  process. Machine network settings and execution policy were not changed.
- Native manifest checks accepted intact files and rejected a modified payload.
- The browser fixture suite also passed against the files produced by the
  native Windows offline build, verifying that its published app boots and works.
- Chromium / Playwright **1.58.2** against the Linux-published app: fixture suite
  passed with **58 WMTS requests**, **zero external browser requests attempted**,
  and **zero page script errors**.
- The same browser suite passed against an existing GeoServer/GeoWebCache
  `omt:world` layer using its `EPSG:3857` matrix set: **57 WMTS requests**, zero
  external browser requests attempted, zero page script errors. The final
  screenshot was inspected after OpenLayers reported rendering complete.
- Browser checks covered both points, both lines, both polygons, longitude/latitude
  projection, WMTS matrix identifiers and configured endpoint override, zoom,
  keyboard pan, Blazor reset, resizing, invalid layer/gridset/format, HTTP 503,
  failed tiles, malformed capabilities (fixture only), and map cleanup.
- Vendored OpenLayers SHA256 checksums and `git diff --check` passed.

## Boundaries

- The real GeoServer check used a **test-only same-origin proxy**, because the
  existing server did not advertise CORS headers. Production CORS, authentication,
  internal PKI, and IIS deployment were not validated by that check.
- The Windows offline test used local-only sources and failing process-local
  HTTP proxies. It did not disconnect the workstation's network adapters or
  alter its firewall. Repeat acceptance on the actual isolated network.
- The generic `topp:states` configuration is an editable example; the live test
  used an existing basemap instead of installing GeoServer sample data.
- Browser automation ran in Linux Chromium, not a native Windows browser.
- The Windows CI job builds and runs fixture browser tests, including a nested
  offline-bundle source fixture. Live GeoServer and offline-transfer checks are
  separate from that job.

Local screenshots and request records are retained under
`.artifacts/browser-evidence/` (ignored by Git). Prepared SDKs, caches, test
builds, and transfer bundles are also ignored.

## Publication checks on 2026-10-20

- Repeated the locked restore using the retained local NuGet feed and published
  Release with SDK 10.0.401, runtime/packages 10.0.12. The Linux SDK host used
  process-local invariant globalization because ICU is unavailable on that host.
- The first build exposed duplicate compilation of source inside the default
  `offline-bundle/` directory. The project now excludes that directory and
  `.artifacts/` from default items. Publishing passed with the existing prepared
  bundle still present. Windows CI also creates nested source files before
  publishing to cover this regression.
- The freshly published app passed the Playwright 1.58.2 Chromium fixture suite:
  58 WMTS requests, zero external browser requests and zero page script errors.
  Graphics, projection, controls, resize, error paths and cleanup passed.
- Vendored OpenLayers checksums and JavaScript syntax checks passed.
- The committed lock file also passed restore from NuGet.org in a fresh source
  copy and empty package/HTTP caches. CI uses job-local NuGet caches and
  `--locked-mode --no-http-cache` to avoid relying on the runner's preloaded cache.
  The project also disables the SDK's implicit workload-library feed, which can
  otherwise provide a different package with the same ID/version than the
  explicitly configured feed. Package versions and lock-file hashes are retained.
- `RunAOTCompilation` is left unset (AOT is opt-in) while `WasmBuildNative=false`
  selects the prebuilt runtime. With .NET 10.0.12's WebAssembly workload installed,
  explicitly setting AOT to `false` differs from the runtime pack's empty default
  and incorrectly requests native relinking. Removing that redundant setting
  avoids the conflict without requiring the workload for this demo.
