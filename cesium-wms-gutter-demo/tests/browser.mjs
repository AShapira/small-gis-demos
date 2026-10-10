import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const evidence = resolve(root, "evidence");
await mkdir(evidence, { recursive: true });
await mkdir(resolve(root, ".artifacts/browser-images"), { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || undefined,
  headless: true,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({ viewport: { width: 1280, height: 1150 }, deviceScaleFactor: 1 });
const page = await context.newPage();
page.setDefaultTimeout(60000);
const pageErrors = [], responses = [], imageWrites = [];
let phase = "initial", sequence = 0;
page.on("pageerror", e => pageErrors.push({ phase, message: String(e) }));
page.on("response", response => {
  if (new URL(response.url()).pathname !== "/wms") return;
  const record = { phase, url: response.url(), status: response.status(), headers: response.headers() };
  responses.push(record);
  if (phase === "validation") {
    const index = sequence++;
    imageWrites.push(response.body().then(body => {
      record.image = `.artifacts/browser-images/${index}.${record.headers['content-type']?.includes('image/png') ? 'png' : 'xml'}`;
      return writeFile(resolve(root, record.image), body);
    }).catch(e => { record.bodyError = String(e); }));
  }
});

try {
  await page.goto(process.env.DEMO_URL || "http://127.0.0.1:18120/", { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.demo?.ready === true);
  await page.waitForFunction(() => window.demo.viewer.scene.globe.tilesLoaded);
  await page.screenshot({ path: resolve(evidence, "cesium-triangles.png"), fullPage: true });
  await page.evaluate(() => window.demo.select("rot30_base", "checker"));
  await page.waitForFunction(() => window.demo.viewer.scene.globe.tilesLoaded);
  await page.screenshot({ path: resolve(evidence, "cesium-checkerboard.png"), fullPage: true });
  await page.evaluate(() => window.demo.select("rot30_base", "footprint"));
  await page.waitForFunction(() => window.demo.viewer.scene.globe.tilesLoaded);
  await page.screenshot({ path: resolve(evidence, "cesium-footprint.png"), fullPage: true });
  console.log("Saved three Cesium browser screenshots");

  phase = "validation";
  const validation = await page.evaluate(async () => {
    const d = window.demo, records = [];
    d.pause();
    const providers = new Map(d.fixtures.map(f => [f.name, [0, 128].map(g => d.makeProvider(f, g))]));
    const referenceData = async id => {
      const image = new Image(); image.src = `/.artifacts/references/${id}.png`; await image.decode();
      const c = document.createElement("canvas"); c.width = c.height = 256;
      const ctx = c.getContext("2d", { willReadFrequently: true }); ctx.drawImage(image, 0, 0);
      return ctx.getImageData(0, 0, 256, 256).data;
    };
    const compare = (canvas, reference) => {
      const actual = canvas.getContext("2d").getImageData(0, 0, 256, 256).data;
      let valid = 0, missing = 0, exact = 0, error = 0, good = 0, intermediate = 0;
      const levels = new Set(); for (let i=0; i<reference.length; i+=4) if (reference[i+3]) levels.add(reference[i]);
      for (let i=0; i<actual.length; i+=4) {
        if (!reference[i+3]) continue;
        valid++;
        const blank = actual[i+3] === 0 || (actual[i]>=254 && actual[i+1]>=254 && actual[i+2]>=254)
          || (actual[i]<=1 && actual[i+1]<=1 && actual[i+2]<=1);
        if (blank) { missing++; continue; }
        good++;
        const delta = Math.abs(actual[i]-reference[i]); error += delta; if (delta===0) exact++;
        if (!levels.has(actual[i])) intermediate++;
      }
      return { validPixels: valid, missingPixels: missing, exactFraction: good ? exact/good : null,
        meanAbsoluteError: good ? error/good : null, intermediatePixels: intermediate };
    };
    await d.pool(d.cases.matrix, 6, async entry => {
      const reference = await referenceData(entry.id);
      for (const [index, gutter] of [0, 128].entries()) {
        try {
          const canvas = await d.imageFor(providers.get(entry.name)[index], entry.tile);
          records.push({ ...entry, gutter, ...compare(canvas, reference), error: false });
        } catch (error) { records.push({ ...entry, gutter, error: true, message: String(error) }); }
      }
    });
    const summary = [0,128].map(gutter => {
      const selected = records.filter(r => r.gutter===gutter), good = selected.filter(r => !r.error);
      return { gutter, requests: selected.length, errors: selected.filter(r=>r.error).length,
        missingPixels: good.reduce((s,r)=>s+r.missingPixels,0),
        interiorMissingPixels: good.filter(r=>r.fullyInside).reduce((s,r)=>s+r.missingPixels,0),
        interiorErrors: selected.filter(r=>r.fullyInside && r.error).length,
        northUpMissingPixels: good.filter(r=>r.name.startsWith("rot0_")).reduce((s,r)=>s+r.missingPixels,0) };
    });
    return { summary, records };
  });
  await Promise.all(imageWrites);
  await writeFile(resolve(evidence, "validation.json"), JSON.stringify(validation, null, 2));
  console.log(JSON.stringify(validation.summary, null, 2));

  phase = "benchmark";
  const performancePromise = page.evaluate(() => window.demo.runBenchmark());
  await page.waitForFunction(() => document.getElementById("benchmark").disabled);
  assert.ok(await page.evaluate(() => ["fixture", "preset", "gutter-left", "gutter-right", "reset", "split"].every(id => document.getElementById(id).disabled)));
  const performance = await performancePromise;
  await writeFile(resolve(evidence, "performance-all-gutters.json"), JSON.stringify(performance, null, 2));
  await page.evaluate(() => window.demo.select("rot30_base", "triangle"));
  await page.waitForFunction(() => window.demo.viewer.scene.globe.tilesLoaded);
  await page.screenshot({ path: resolve(evidence, "cesium-all-gutters-results.png"), fullPage: true });
  await writeFile(resolve(evidence, "browser.json"), JSON.stringify({ version: browser.version(),
    userAgent: await page.evaluate(() => navigator.userAgent), viewport: page.viewportSize(),
    webgl: await page.evaluate(() => {
      const gl = document.createElement("canvas").getContext("webgl2");
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      return { version: gl.getParameter(gl.VERSION), renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "unavailable" };
    }), pageErrors }, null, 2));
  console.log(JSON.stringify(performance.summary, null, 2));
  assert.equal(pageErrors.length, 0, "Unexpected browser script errors");
  assert.ok(validation.summary[0].interiorMissingPixels > 0, "Triangles should reproduce");
  assert.equal(validation.summary[1].interiorMissingPixels, 0, "Gutter 128 should remove interior gaps");
  assert.equal(validation.summary[1].interiorErrors, 0, "No interior requests should fail");
  assert.equal(validation.summary[1].northUpMissingPixels, 0, "North-up control must remain clean");
  const checker = validation.records.find(r => r.id === "rot30_base_24_10021003_6797199" && r.gutter === 128);
  assert.equal(checker.missingPixels, 0);
  assert.equal(checker.intermediatePixels, 0, "Cropping must not blur the checkerboard");
  assert.ok(checker.exactFraction > .9999, "Detect flipped or shifted imagery against the independent oracle");
  assert.equal(performance.summary.length, 10);
  for (const row of performance.summary) {
    assert.equal(row.count, 288);
    assert.equal(row.errors, performance.blocks.filter(b => b.gutter === row.gutter && b.concurrency === row.concurrency)
      .flatMap(b => b.samples).filter(s => s.error).length);
  }
  console.log("Browser validation and performance checks passed");
} finally {
  await writeFile(resolve(root, ".artifacts/responses.json"), JSON.stringify(responses, null, 2));
  await browser.close();
}
