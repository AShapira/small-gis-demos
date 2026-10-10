import { chromium } from "playwright";
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
const base = process.env.DEMO_URL || "http://127.0.0.1:18120/";
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = [], externalRequests = [];
page.on("pageerror", e => errors.push(String(e)));
page.on("request", request => {
  if (request.url().startsWith("http") && new URL(request.url()).origin !== new URL(base).origin) externalRequests.push(request.url());
});
try {
  await page.goto(base);
  await page.waitForFunction(() => window.demo?.ready === true);
  await page.locator("#split").focus();
  await page.locator("#split").press("End");
  assert.equal(await page.evaluate(() => window.demo.viewer.scene.splitPosition), 1);
  await page.locator("#split").press("Home");
  assert.equal(await page.evaluate(() => window.demo.viewer.scene.splitPosition), 0);
  await page.locator("#fixture").selectOption("rot0_base");
  await page.waitForFunction(() => document.getElementById("tile-id").textContent.startsWith("rot0_base")
    && document.getElementById("status").textContent.startsWith("Nearest"));
  const northUpEqual = await page.evaluate(() => document.getElementById("tile-left").toDataURL() === document.getElementById("tile-right").toDataURL());
  assert.ok(northUpEqual, "North-up tile should be identical with both gutters");
  await page.locator("#preset").selectOption("footprint");
  await page.waitForFunction(() => document.getElementById("tile-id").textContent.includes("z14"));
  // Shorten only this disposable smoke-test page's sample list; the full benchmark is separate.
  await page.evaluate(() => { window.demo.cases.benchmark.splice(1); });
  const measured = page.evaluate(() => window.demo.runBenchmark());
  await page.waitForFunction(() => document.getElementById("benchmark").disabled);
  assert.ok(await page.evaluate(() => ["fixture", "preset", "gutter-left", "gutter-right", "reset", "split"].every(id => document.getElementById(id).disabled)));
  await measured;
  assert.ok(await page.evaluate(() => ["fixture", "preset", "gutter-left", "gutter-right", "reset", "split"].every(id => !document.getElementById(id).disabled)));
  await page.setViewportSize({ width: 800, height: 1000 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 800));
  assert.equal((await fetch(new URL("/.artifacts/responses.json", base))).status, 404);
  assert.equal((await fetch(new URL("/node_modules/cesium/Build/Cesium/%2e%2e%2f%2e%2e%2fpackage.json", base))).status, 404);
  assert.equal((await fetch(new URL("/wms?request=GetCapabilities", base))).status, 400);
  assert.equal((await fetch(base, { method: "POST" })).status, 405);
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  await writeFile(new URL("../evidence/smoke.json", import.meta.url), JSON.stringify({
    checkedAt: new Date().toISOString(), slider: true, selectors: true, northUpEqual,
    benchmarkControls: true, narrowLayout: true, staticRouteRestrictions: true,
    errors, externalRequests,
  }, null, 2));
  console.log("UI controls, north-up pixel equality, benchmark lock, narrow layout, local-only requests and proxy restrictions passed");
} finally { await browser.close(); }
