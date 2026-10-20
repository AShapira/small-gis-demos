# Blazor + OpenLayers

A hello-world **.NET 10 Blazor WebAssembly** app with an OpenLayers **WMTS**
basemap from GeoServer/GeoWebCache and six client-side graphics: two points, two
lines, and two polygons. Pan, zoom, and reset the view. Graphics are defined in
C# and passed to JavaScript as GeoJSON.

All JavaScript, CSS, fonts (system fonts), and the published .NET browser runtime
are local. Only your internal web server and GeoServer are needed at runtime.
Air-gapped means **no Internet**, not no connection to these internal services.
There is no service worker, external basemap, database, or server-side Blazor app.

## Quick start

Use the SDK pinned in `global.json` (**10.0.401**, runtime/packages **10.0.12**).
The project excludes the SDK's implicit workload-package feed so locked restores
use the configured NuGet sources consistently across machines.
From this directory on a connected development machine:

```powershell
dotnet restore --locked-mode
dotnet run --no-restore --urls http://localhost:5080
```

Open http://localhost:5080. Configure GeoServer as below. The default sample
service is intentionally local; if it is absent, the graphics still appear and
the status explains the basemap failure. No GeoServer is installed or changed by
this project. Node.js/npm and the `wasm-tools` workload are not needed to build.

## Configure your basemap

Edit `wwwroot/map-config.json` (or the published `map-config.json`, then reload):

```json
{
  "endpoint": "http://localhost:8080/geoserver/gwc/service/wmts",
  "layer": "topp:states",
  "matrixSet": "EPSG:900913",
  "format": "image/png",
  "center": [-98, 39],
  "zoom": 4
}
```

- `endpoint`: WMTS service URL reachable **from the browser**. `localhost`
  means the browser's own machine. A same-origin relative URL is also supported.
- `layer`: exact advertised identifier. `topp:states` requires GeoServer's sample
  data; production installations may omit it. An existing basemap layer group
  works too.
- `matrixSet`: exact advertised Web Mercator gridset identifier, commonly
  `EPSG:900913` or `EPSG:3857`. The map uses EPSG:3857; 900913 is its equivalent.
- `format`: advertised tile MIME type, normally `image/png`.
- `center`: **longitude, latitude** in degrees; `zoom`: initial map zoom.
  Edit `SampleGraphics.cs` to move the six graphics to your own area.

In GeoServer, enable tile caching for the selected layer/group and expose a Web
Mercator gridset and PNG format. Inspect this internal URL:

```text
http://YOUR-SERVER:8080/geoserver/gwc/service/wmts?SERVICE=WMTS&REQUEST=GetCapabilities&VERSION=1.0.0
```

The app reads the capabilities using OpenLayers, selects the advertised default
style, and derives resolutions and matrix identifiers. It does not assume that
matrix IDs are plain zoom numbers. Tile requests use KVP parameters at the
configured endpoint, even if GeoServer advertises a different hostname.

For predictable basemap latency, optionally seed only the required extent and
zoom levels in GeoWebCache. Uncached tiles can be generated on demand as long as
GeoServer's source data is available inside the isolated network. Seeding and
server configuration are administrator steps, not actions performed by this app.

### CORS and HTTPS

The browser fetches GetCapabilities and tiles anonymously. If GeoServer has a
different origin (including a different port), configure it or its reverse proxy
to return `Access-Control-Allow-Origin` for the app origin on **both** capabilities
and tiles. Alternatively route `/geoserver/` through the app's internal web server
and use a relative endpoint. This demo does not include a production proxy.

An HTTPS app needs an HTTPS GeoServer endpoint with a trusted internal certificate.
Do not disable browser security or certificate checks. A missing layer, gridset,
format, blocked CORS request, timeout, or tile error is shown in the status line;
the graphics layer remains usable. Authentication is outside this demo's scope.

## Prepare dependencies for an air-gapped Windows machine

Use **64-bit Windows PowerShell 5.1 or PowerShell 7 on Windows x64** on the
connected preparation machine. Run scripts in a child process as shown, so
temporary SDK/NuGet environment settings do not affect your terminal.
If your checkout is in WSL, copy the demo source to a local Windows directory
first: Windows may treat a `\\wsl.localhost\...` path as remote and require signed
scripts there. No execution-policy change is necessary for a local source copy
under the usual `RemoteSigned` policy.

```powershell
powershell.exe -NoProfile -File .\scripts\Prepare-Offline.ps1 -Destination .\offline-bundle
```

The destination must not already exist. The script downloads the pinned Windows
SDK ZIP and checks its SHA512 against Microsoft's release metadata, copies the
demo source and vendored OpenLayers, restores locked dependencies to an isolated
cache, and publishes once to check dependency completeness. It collects all
`.nupkg` files (including runtime packs) into a local feed and writes
`checksums.json` for the transfer. No machine-wide SDK installation is performed.
The bundle, caches, and SDK are large; allow several GB of working disk space.
An optional `-SdkArchive C:\Downloads\dotnet-sdk-10.0.401-win-x64.zip` reuses
a previously downloaded archive; the same official SHA512 check still applies.

Transfer the **entire bundle directory** through your approved transfer process.
On the offline Windows machine:

```powershell
powershell.exe -NoProfile -File C:\Demos\offline-bundle\source\scripts\Build-Offline.ps1 -Bundle C:\Demos\offline-bundle
```

The script checks the transfer manifest, extracts the SDK locally, makes a fresh
source copy and package cache, restores **only** from the bundled feed in locked
mode, then publishes Release with `--no-restore`. Workload update checks, telemetry,
and online NuGet auditing are disabled for this offline workflow. NuGet uses
cached certificate revocation information (`NUGET_CERT_REVOCATION_MODE=offline`);
validate packages and current certificate trust on the connected machine first.
SDK extraction
and build outputs remain under the bundle's `.artifacts/`; each run uses a new
directory and prints the exact publish path and optional local run command.

Do not edit checksum-covered bundle source after preparing it. Configure the
**published** `map-config.json`; to change C# source, rebuild the bundle on the
connected machine. A missing package or SDK version mismatch fails explicitly.
PowerShell execution policy is managed by your organization; scripts do not
change it. Windows SDK prerequisites and trusted package-signing roots must
already be available on the target machine.

## Publish / IIS

```powershell
dotnet publish -c Release --no-restore -o .artifacts\publish
```

Serve the complete published **wwwroot** directory over HTTP(S); opening
`index.html` using `file://` does not work. Clients do not need .NET installed.
For IIS, use a static site pointing at this directory, enable Static Content,
and install IIS URL Rewrite if required by the generated `web.config`. Preserve
that configuration, including `.wasm` MIME handling. This is a static site and
does not require the ASP.NET Core Hosting Bundle.

For an IIS sub-application, change the source `<base href="/" />` to its path
(for example `/maps/`) before publishing. Configuration and module paths resolve
relative to that base. The deployment web server must serve `.js` as JavaScript,
`.json` as JSON, and `.wasm` as `application/wasm`.

## Code map

- `App.razor`: page and Blazor lifecycle; configuration loading and JS interop.
- `SampleGraphics.cs`: small C# GeoJSON feature collection in longitude/latitude.
- `wwwroot/js/map.js`: OpenLayers map, WMTS capabilities, styling, and cleanup.
- `wwwroot/vendor/openlayers/`: checked-in upstream browser build, license, and
  SHA256 checksums; no runtime CDN access.
- `scripts/`: Windows connected preparation and offline build.

The JavaScript module initializes after the map container is rendered. It creates
graphics immediately, then loads the basemap asynchronously. A ResizeObserver
keeps the map sized correctly; a MutationObserver releases the map and aborts
pending requests when Blazor removes its container.

## Validation

Build/publish and browser checks are separate from native Windows/offline and
real GeoServer validation. See `VALIDATION.md` for results recorded for this change.

Optional developer browser tests require Node.js 22+ and Playwright; they are not
part of the app's offline dependencies:

```powershell
npm install --prefix .artifacts/browser playwright@1.58.2
& .\.artifacts\browser\node_modules\.bin\playwright.cmd install chromium
node tests/browser.mjs
```

First publish to `.artifacts/publish`. The test serves those static files, blocks
all public browser requests, supplies a WMTS fixture, checks all geometry types,
projection, controls, resize, invalid configuration, unavailable service, tile
failures, and cleanup. Its fixture advertises an unreachable tile hostname to
verify that the configured endpoint is used instead.

To exercise a real service, set `WMTS_URL`, `WMTS_LAYER`, and `WMTS_MATRIX_SET`
before running the same test. The test server proxies that one service to the
app origin, so this verifies real capabilities and rendered tiles but **does not
validate your production CORS configuration**. It does not change GeoServer.
Screenshots and request records go to `.artifacts/browser-evidence/`.

Before deployment, repeat the offline build with public network access blocked,
open the published app in a fresh browser, verify the intended map area visually,
and check that only the internal app/GeoServer hosts receive requests.

References: [OpenLayers WMTS](https://openlayers.org/en/latest/examples/wmts-layer-from-capabilities.html),
[GeoWebCache](https://docs.geoserver.org/stable/en/user/geowebcache/using/),
[Blazor static hosting](https://learn.microsoft.com/en-us/aspnet/core/blazor/host-and-deploy/webassembly/?view=aspnetcore-10.0).
