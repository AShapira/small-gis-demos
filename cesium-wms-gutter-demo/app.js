import { createGutterWmsProvider } from "./adapter.js";
const C = window.Cesium;
const fixtures = await (await fetch("/fixtures.json")).json();
const cases = await (await fetch("/cases.json")).json();
const $ = (id) => document.getElementById(id);
const scheme = new C.WebMercatorTilingScheme();
const projection = scheme.projection;
const half = Math.PI * 6378137;
const GUTTERS = Object.freeze([0, 20, 64, 128, 256]);
const CONTROL_IDS = ["fixture", "preset", "gutter-left", "gutter-right", "reset", "split"];
let current, providers, mapLayers = [], selectedTile, lastBenchmark, revision = 0;

function tileAt(z, x, y) {
  const span = 2 * half / 2 ** z;
  return [z, Math.floor((x + half) / span), Math.floor((half - y) / span)];
}
function world(f, col, row) {
  return [f.gt[0] + f.gt[1] * col + f.gt[2] * row, f.gt[3] + f.gt[4] * col + f.gt[5] * row];
}
function footprint(f) {
  const points = [[0, 0], [f.size, 0], [0, f.size], [f.size, f.size]].map(([c, r]) => world(f, c, r));
  const sw = projection.unproject(new C.Cartesian3(Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1]))));
  const ne = projection.unproject(new C.Cartesian3(Math.max(...points.map(p => p[0])), Math.max(...points.map(p => p[1]))));
  return new C.Rectangle(sw.longitude, sw.latitude, ne.longitude, ne.latitude);
}
function makeProvider(f, gutter, onSample = () => {}) {
  return createGutterWmsProvider(C, { url: "/wms", layers: `affine_probe:${f.name}`, gutter,
    rectangle: footprint(f), onSample });
}
const viewer = new C.Viewer("map", {
  baseLayer: false, baseLayerPicker: false, geocoder: false, homeButton: false,
  animation: false, timeline: false, navigationHelpButton: false, fullscreenButton: false,
  sceneModePicker: false, selectionIndicator: false, infoBox: false,
  sceneMode: C.SceneMode.SCENE2D, mapProjection: new C.WebMercatorProjection(),
  terrainProvider: new C.EllipsoidTerrainProvider(), skyBox: false, skyAtmosphere: false,
  requestRenderMode: true, maximumRenderTimeChange: Infinity,
});
viewer.scene.globe.baseColor = C.Color.WHITE;
viewer.scene.globe.enableLighting = false;
viewer.scene.postProcessStages.fxaa.enabled = false;
viewer.scene.splitPosition = .5;
viewer.scene.screenSpaceCameraController.minimumZoomDistance = .1;
viewer.scene.globe.maximumScreenSpaceError = 1;

for (const f of fixtures) {
  const option = document.createElement("option"); option.value = f.name;
  option.textContent = `${f.shear ? "Sheared 30°" : `${f.angle}° rotation`} · ${f.overviews ? "nearest overviews" : "no overviews"}`;
  $("fixture").append(option);
}
$("fixture").value = "rot30_base";
for (const side of ["left", "right"]) {
  for (const gutter of GUTTERS) {
    const option = document.createElement("option");
    option.value = String(gutter); option.textContent = `${gutter} px`;
    $(`gutter-${side}`).append(option);
  }
  $(`gutter-${side}`).value = side === "left" ? "0" : "128";
}

async function imageFor(provider, tile) {
  const [z, x, y] = tile;
  const image = provider.requestImage(x, y, z, new C.Request({ throttle: false, throttleByServer: false }));
  if (image === undefined) throw new Error("Unexpected throttling in explicit test request");
  return image;
}
async function selectView({ resetCamera = true } = {}) {
  const generation = ++revision;
  current = fixtures.find(f => f.name === $("fixture").value);
  const preset = $("preset").value;
  selectedTile = tileAt(preset === "footprint" ? 14 : 24, ...world(current,
    current.size * (preset === "triangle" ? .31 : .5),
    current.size * (preset === "triangle" ? .68 : preset === "checker" ? .125 : .5)));
  const gutters = ["left", "right"].map(side => Number($(`gutter-${side}`).value));
  providers = gutters.map(g => makeProvider(current, g));
  for (const layer of mapLayers) viewer.imageryLayers.remove(layer, true);
  mapLayers = providers.map((provider, i) => {
    const layer = new C.ImageryLayer(provider, { minificationFilter: C.TextureMinificationFilter.NEAREST,
      magnificationFilter: C.TextureMagnificationFilter.NEAREST });
    layer.splitDirection = i === 0 ? C.SplitDirection.LEFT : C.SplitDirection.RIGHT;
    viewer.imageryLayers.add(layer); return layer;
  });
  const [z, x, y] = selectedTile;
  const bounds = scheme.tileXYToNativeRectangle(x, y, z);
  const span = bounds.east - bounds.west;
  const center = projection.unproject(new C.Cartesian3((bounds.west + bounds.east) / 2, (bounds.north + bounds.south) / 2));
  const ratio = viewer.canvas.clientWidth / viewer.canvas.clientHeight;
  // A tight, top-down map view; the exact-tile inspector below does not depend on camera LOD.
  const height = preset === "footprint" ? 6500 : span * 1.7;
  const nativeCenter = projection.project(center);
  const sw = projection.unproject(new C.Cartesian3(nativeCenter.x - height * ratio / 2, nativeCenter.y - height / 2));
  const ne = projection.unproject(new C.Cartesian3(nativeCenter.x + height * ratio / 2, nativeCenter.y + height / 2));
  if (resetCamera) viewer.camera.setView({ destination: new C.Rectangle(sw.longitude, sw.latitude, ne.longitude, ne.latitude) });
  viewer.scene.requestRender();
  $("tile-id").textContent = `${current.name} · z${z} / ${x} / ${y}`;
  $("status").textContent = "Fetching comparison tiles…";
  $("status").className = "";
  for (const [i, side] of ["left", "right"].entries()) {
    const gutter = gutters[i], size = 256 + 2 * gutter;
    $(`map-${side}-title`).textContent = `GUTTER ${gutter}`;
    $(`map-${side}-size`).textContent = `${size} × ${size} → central 256 × 256`;
    $(`tile-${side}-title`).textContent = `Gutter ${gutter}`;
    $(`tile-${side}`).getContext("2d").clearRect(0, 0, 256, 256);
    $(`tile-${side}-caption`).textContent = "Loading tile…";
  }
  const outcomes = await Promise.allSettled(providers.map(async (p, i) => {
    const side = i ? "right" : "left";
    try {
      const start = performance.now(); const image = await imageFor(p, selectedTile);
      if (generation !== revision) return;
      $(`tile-${side}`).getContext("2d").drawImage(image, 0, 0);
      $(`tile-${side}-caption`).textContent = `${256 + 2 * gutters[i]}² request → 256² tile · ${(performance.now() - start).toFixed(1)} ms (single request)`;
    } catch (error) {
      if (generation === revision) $(`tile-${side}-caption`).textContent = "Tile request failed.";
      throw error;
    }
  }));
  if (generation !== revision) return;
  const errors = outcomes.filter(x => x.status === "rejected");
  $("status").textContent = errors.length ? `${errors.length} tile request(s) failed; see console` : "Nearest neighbour · original affine pixels";
  $("status").className = errors.length ? "error" : "";
  for (const error of errors) console.error(error.reason);
}
$("fixture").addEventListener("change", selectView);
$("preset").addEventListener("change", selectView);
$("reset").addEventListener("click", selectView);
for (const side of ["left", "right"]) {
  $(`gutter-${side}`).addEventListener("change", () => selectView({ resetCamera: false }));
}
$("split").addEventListener("input", () => {
  viewer.scene.splitPosition = Number($("split").value) / 100;
  $("divider").style.left = `${$("split").value}%`; viewer.scene.requestRender();
});

async function pool(jobs, concurrency, action) {
  let next = 0;
  await Promise.all(Array.from({ length: concurrency }, async () => {
    while (next < jobs.length) { const index = next++; await action(jobs[index], index); }
  }));
}
const percentile = (values, q) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a,b) => a-b);
  if (q === .5 && sorted.length % 2 === 0) return (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  return sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)];
};
function summarize(blocks) {
  const groups = [];
  for (const concurrency of [1, 6]) for (const gutter of GUTTERS) {
    const selected = blocks.filter(b => b.concurrency === concurrency && b.gutter === gutter);
    const all = selected.flatMap(b => b.samples); const good = all.filter(s => !s.error);
    groups.push({ gutter, concurrency, count: all.length, errors: all.length - good.length,
      medianMs: percentile(good.map(s => s.totalMs), .5), p95Ms: percentile(good.map(s => s.totalMs), .95),
      cropMedianMs: percentile(good.map(s => s.cropMs), .5),
      tilesPerSecond: good.length / (selected.reduce((sum,b) => sum+b.durationMs, 0) / 1000) });
  }
  return groups;
}
function showResults(summary) {
  const ms = value => value === null ? "—" : `${value.toFixed(2)} ms`;
  const table = document.createElement("table");
  const heading = table.createTHead().insertRow();
  for (const text of ["Gutter", "Request", "Concurrency", "Median", "p95", "Crop median", "Tiles/s", "Errors"]) {
    const th = document.createElement("th"); th.textContent = text; heading.append(th);
  }
  const body = table.createTBody();
  for (const row of summary) {
    const tr = body.insertRow();
    for (const value of [row.gutter, `${256 + 2 * row.gutter}²`, row.concurrency, ms(row.medianMs), ms(row.p95Ms),
      ms(row.cropMedianMs), row.tilesPerSecond.toFixed(1), row.errors]) tr.insertCell().textContent = value;
  }
  $("results").replaceChildren(table);
}
async function runBenchmark() {
  if ($("benchmark").disabled) throw new Error("A benchmark is already running");
  $("benchmark").disabled = true;
  for (const id of CONTROL_IDS) $(id).disabled = true;
  viewer.useDefaultRenderLoop = false;
  const blocks = [], fixture = fixtures.find(f => f.name === "rot30_base");
  try {
    for (const concurrency of [1, 6]) for (let repetition = 0; repetition < 3; repetition++) {
      const offset = (repetition + (concurrency === 6 ? 2 : 0)) % GUTTERS.length;
      const order = [...GUTTERS.slice(offset), ...GUTTERS.slice(0, offset)];
      if (repetition % 2) order.reverse();
      for (const gutter of order) {
        $("benchmark-status").textContent = `Running gutter ${gutter}, concurrency ${concurrency}, repetition ${repetition+1}/3…`;
        let records = [];
        const provider = makeProvider(fixture, gutter, s => records.push(s));
        await pool(cases.benchmark.slice(0, 8), concurrency, async t => { await imageFor(provider, t).catch(() => {}); });
        records = [];
        const began = performance.now();
        await pool(cases.benchmark, concurrency, async t => { await imageFor(provider, t).catch(() => {}); });
        blocks.push({ gutter, concurrency, repetition, durationMs: performance.now()-began, samples: records });
      }
    }
    lastBenchmark = { date: new Date().toISOString(), cesium: C.VERSION, userAgent: navigator.userAgent,
      gutters: [...GUTTERS], tilesPerBlock: cases.benchmark.length, repetitions: 3,
      warmups: Math.min(8, cases.benchmark.length), blocks, summary: summarize(blocks) };
    showResults(lastBenchmark.summary);
    $("benchmark-status").textContent = "Complete. Warm-server measurements through the local proxy; not a cold-disk or frame-rate benchmark.";
    $("download").hidden = false;
    return lastBenchmark;
  } finally {
    $("benchmark").disabled = false;
    for (const id of CONTROL_IDS) $(id).disabled = false;
    viewer.useDefaultRenderLoop = true; viewer.scene.requestRender();
  }
}
$("benchmark").addEventListener("click", () => runBenchmark().catch(e => { $("benchmark-status").textContent = String(e); }));
$("download").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(lastBenchmark, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = "cesium-gutter-benchmark.json"; link.click(); URL.revokeObjectURL(url);
});

// Public test hooks exercise the same providers as the interactive view.
window.demo = { viewer, fixtures, cases, imageFor, makeProvider, runBenchmark, pool, gutters: GUTTERS,
  pause: () => { viewer.useDefaultRenderLoop = false; },
  resume: () => { viewer.useDefaultRenderLoop = true; viewer.scene.requestRender(); },
  async select(name, preset = "triangle") { $("fixture").value = name; $("preset").value = preset; await selectView(); },
  get selectedTile() { return selectedTile; },
};
await selectView();
window.demo.ready = true;
