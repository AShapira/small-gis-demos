import { chromium } from "playwright";
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const values = [0, 20, 64, 128, 256];
const base = process.env.DEMO_URL || "http://127.0.0.1:18120/";
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 1150 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(60000);
const errors = [], previews = [], requests = [], transportFailures = [];
let phase = "selectors";
page.on("pageerror", error => errors.push(String(error)));
page.on("request", request => {
  const url = new URL(request.url());
  if (url.pathname === "/wms") requests.push(url.searchParams.get("width"));
});
page.on("requestfailed", request => {
  if (new URL(request.url()).pathname === "/wms") transportFailures.push({ phase, url: request.url(), failure: request.failure() });
});
page.on("response", response => {
  if (phase === "benchmark" && new URL(response.url()).pathname === "/wms"
    && (response.status() !== 200 || !response.headers()["content-type"]?.startsWith("image/png"))) {
    transportFailures.push({ phase, url: response.url(), status: response.status(), contentType: response.headers()["content-type"] });
  }
});
const evidence = name => new URL(`../evidence/${name}`, import.meta.url);
const controls = ["fixture", "preset", "gutter-left", "gutter-right", "reset", "split"];
async function waitForMap() {
  await page.evaluate(async () => {
    const viewer = window.demo.viewer;
    window.demo.resume();
    await new Promise(resolve => {
      let frames = 0;
      const remove = viewer.scene.postRender.addEventListener(() => {
        if (++frames >= 3 && viewer.scene.globe.tilesLoaded) { remove(); resolve(); }
        else viewer.scene.requestRender();
      });
      viewer.scene.requestRender();
    });
  });
}
try {
  await page.goto(base);
  await page.waitForFunction(() => window.demo?.ready === true);
  await waitForMap();
  await page.evaluate(() => {
    window.demo.pause();
    window.demo.viewer.camera.moveRight(.15);
  });
  const cameraBefore = await page.evaluate(() => {
    const c = window.demo.viewer.camera;
    return [c.position.x, c.position.y, c.position.z, c.frustum.left, c.frustum.right];
  });
  for (const side of ["left", "right"]) {
    assert.deepEqual(await page.locator(`#gutter-${side} option`).evaluateAll(options => options.map(o => Number(o.value))), values);
    for (const gutter of values) {
      requests.length = 0;
      // Fire change even for the initial value so each option exercises a new real request.
      await page.locator(`#gutter-${side}`).selectOption(String(gutter));
      await page.waitForFunction(({ side, gutter }) =>
        document.getElementById(`tile-${side}-title`).textContent === `Gutter ${gutter}` &&
        document.getElementById(`tile-${side}-caption`).textContent.includes("single request"), { side, gutter });
      const size = 256 + 2 * gutter;
      assert.ok(requests.includes(String(size)), `${side} gutter ${gutter}: missing ${size}² WMS request`);
      assert.equal(await page.locator(`#map-${side}-title`).textContent(), `GUTTER ${gutter}`);
      assert.ok((await page.locator(`#map-${side}-size`).textContent()).startsWith(`${size} × ${size}`));
      const result = await page.locator(`#tile-${side}`).evaluate(canvas => {
        const data = canvas.getContext("2d").getImageData(0, 0, 256, 256).data;
        let blankPixels = 0;
        // This particular reproduction tile lies entirely inside the source footprint.
        for (let i = 0; i < data.length; i += 4) {
          if (!data[i + 3] || (data[i] >= 254 && data[i+1] >= 254 && data[i+2] >= 254)
            || (data[i] <= 1 && data[i+1] <= 1 && data[i+2] <= 1)) blankPixels++;
        }
        return { width: canvas.width, height: canvas.height, blankPixels, png: canvas.toDataURL() };
      });
      assert.equal(result.width, 256); assert.equal(result.height, 256);
      if (side === "right") await writeFile(evidence(`gutter-${gutter}.png`), Buffer.from(result.png.split(",")[1], "base64"));
      previews.push({ side, gutter, requestSize: size, missingPixels: result.blankPixels });
    }
  }
  const cameraAfter = await page.evaluate(() => {
    const c = window.demo.viewer.camera;
    return [c.position.x, c.position.y, c.position.z, c.frustum.left, c.frustum.right];
  });
  assert.deepEqual(cameraAfter, cameraBefore, "Changing gutters must preserve the inspected map view");
  assert.ok(previews.find(r => r.side === "right" && r.gutter === 0).missingPixels > 0);
  for (const gutter of [128, 256]) assert.equal(previews.find(r => r.side === "right" && r.gutter === gutter).missingPixels, 0);
  await page.locator("#gutter-left").selectOption("20");
  await page.locator("#gutter-right").selectOption("64");
  await page.waitForFunction(() => document.getElementById("status").textContent.startsWith("Nearest"));
  await waitForMap();
  await page.screenshot({ path: fileURLToPath(evidence("cesium-gutter-selectors.png")), fullPage: true });
  console.log("All five gutters verified on both sides; camera preserved", JSON.stringify(previews));

  phase = "benchmark";
  const pending = page.evaluate(() => window.demo.runBenchmark());
  await page.waitForFunction(() => document.getElementById("benchmark").disabled);
  assert.ok(await page.evaluate(ids => ids.every(id => document.getElementById(id).disabled), controls));
  const performance = await pending;
  await writeFile(evidence("performance-all-gutters.json"), JSON.stringify(performance, null, 2));
  assert.ok(await page.evaluate(ids => ids.every(id => !document.getElementById(id).disabled), controls));
  assert.deepEqual(performance.gutters, values);
  assert.equal(performance.blocks.length, 30);
  assert.equal(performance.summary.length, 10);
  for (const row of performance.summary) {
    assert.equal(row.count, 288);
    // Rendering/transport errors are benchmark outcomes, not successful low-latency tiles.
    assert.equal(row.errors, performance.blocks.filter(b => b.gutter === row.gutter && b.concurrency === row.concurrency)
      .flatMap(b => b.samples).filter(s => s.error).length);
  }
  assert.equal(await page.locator("#results tbody tr").count(), 10);
  await waitForMap();
  await page.screenshot({ path: fileURLToPath(evidence("cesium-all-gutters-results.png")), fullPage: true });
  // Verify the user-facing download contains all sizes and complete measured samples.
  const [download] = await Promise.all([page.waitForEvent("download"), page.locator("#download").click()]);
  const stream = await download.createReadStream(); const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const exported = JSON.parse(Buffer.concat(chunks).toString());
  assert.deepEqual(exported.gutters, values); assert.equal(exported.blocks.length, 30);
  await page.setViewportSize({ width: 800, height: 1000 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 800));
  assert.deepEqual(errors, []);
  await writeFile(evidence("gutter-selectors.json"), JSON.stringify({ checkedAt: new Date().toISOString(),
    previews, cameraPreserved: true, benchmarkControls: true, exportedAllSizes: true, narrowLayout: true,
    errors, transportFailures }, null, 2));
  console.log(JSON.stringify(performance.summary, null, 2));
  console.log("Five-size selector and full benchmark checks passed");
} finally { await browser.close(); }
