import test from "node:test";
import assert from "node:assert/strict";
import { expandedRequest, createGutterWmsProvider } from "../adapter.js";

test("expanded request preserves every central pixel's map coordinates", () => {
  const bounds = { west: -17, south: 90, east: 27, north: 123 };
  for (const gutter of [0, 1, 20, 64, 128, 256]) {
    const result = expandedRequest(bounds, 256, gutter);
    for (const pixel of [0, 1, 127, 255]) {
      const x = result.bbox[0] + (gutter + pixel + .5) * (result.bbox[2] - result.bbox[0]) / result.width;
      const y = result.bbox[3] - (gutter + pixel + .5) * (result.bbox[3] - result.bbox[1]) / result.height;
      assert.ok(Math.abs(x - (bounds.west + (pixel + .5) * 44 / 256)) < 1e-12);
      assert.ok(Math.abs(y - (bounds.north - (pixel + .5) * 33 / 256)) < 1e-12);
    }
  }
});

test("invalid gutters fail early", () => {
  for (const gutter of [-1, .5, NaN]) assert.throws(() => expandedRequest({}, 256, gutter), RangeError);
});

test("request throttling remains synchronous and the scheduling object is forwarded", () => {
  let seen;
  const C = {
    WebMercatorTilingScheme: class { tileXYToNativeRectangle() { return { west: 0, south: 0, east: 1, north: 1 }; } },
    WebMapServiceImageryProvider: class {},
    Resource: class { getDerivedResource(options) { seen = options; return { fetchImage: () => undefined }; } },
  };
  const provider = createGutterWmsProvider(C, { url: "/wms", layers: "test:layer" });
  const request = { marker: "scheduler" };
  assert.equal(provider.requestImage(0, 0, 0, request), undefined);
  assert.equal(seen.request, request);
  assert.equal(seen.queryParameters.width, 512);
});
