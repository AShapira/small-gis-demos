/** Direct WMS 1.1.1 / EPSG:3857 only. No server changes or raster resampling. */
export function expandedRequest(bounds, tileSize = 256, gutter = 128) {
  if (!Number.isInteger(gutter) || gutter < 0 || !Number.isInteger(tileSize) || tileSize < 1) {
    throw new RangeError("Tile size must be positive; gutter must be a nonnegative integer");
  }
  const dx = (bounds.east - bounds.west) / tileSize;
  const dy = (bounds.north - bounds.south) / tileSize;
  return {
    bbox: [bounds.west - gutter * dx, bounds.south - gutter * dy,
      bounds.east + gutter * dx, bounds.north + gutter * dy],
    width: tileSize + 2 * gutter,
    height: tileSize + 2 * gutter,
  };
}

export function createGutterWmsProvider(Cesium, {
  url, layers, gutter = 128, tileSize = 256, rectangle, maximumLevel = 24,
  parameters = {}, onSample = () => {},
}) {
  expandedRequest({ west: 0, south: 0, east: 1, north: 1 }, tileSize, gutter);
  const tilingScheme = new Cesium.WebMercatorTilingScheme();
  const fixed = {
    ...parameters,
    service: "WMS", request: "GetMap", version: "1.1.1", layers,
    styles: parameters.styles ?? "", format: "image/png", transparent: false,
    bgcolor: "0xFFFFFF", srs: "EPSG:3857",
    interpolations: "nearest neighbor", format_options: "antialias:off",
  };
  const endpoint = new Cesium.Resource({ url });
  const provider = new Cesium.WebMapServiceImageryProvider({
    url, layers, parameters: fixed, srs: "EPSG:3857", tilingScheme,
    tileWidth: tileSize, tileHeight: tileSize, maximumLevel, rectangle,
    enablePickFeatures: false,
  });

  // Do not make this async: undefined is Cesium's immediate throttle signal.
  provider.requestImage = function (x, y, level, request) {
    const bounds = tilingScheme.tileXYToNativeRectangle(x, y, level);
    const expanded = expandedRequest(bounds, tileSize, gutter);
    const resource = endpoint.getDerivedResource({ request, queryParameters: {
      ...fixed, bbox: expanded.bbox.join(","), width: expanded.width, height: expanded.height,
    } });
    const started = performance.now();
    const pending = resource.fetchImage({ preferImageBitmap: false, flipY: false });
    if (pending === undefined) return undefined;
    return pending.then((image) => {
      const decoded = performance.now();
      if (image.width !== expanded.width || image.height !== expanded.height) {
        throw new Error(`WMS returned ${image.width}×${image.height}; expected ${expanded.width}×${expanded.height}`);
      }
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = tileSize;
      const context = canvas.getContext("2d");
      context.imageSmoothingEnabled = false;
      context.drawImage(image, gutter, gutter, tileSize, tileSize, 0, 0, tileSize, tileSize);
      onSample({ x, y, level, gutter, url: resource.url,
        fetchDecodeMs: decoded - started, cropMs: performance.now() - decoded,
        totalMs: performance.now() - started, error: false });
      return canvas;
    }).catch((error) => {
      onSample({ x, y, level, gutter, url: resource.url,
        totalMs: performance.now() - started, error: true, message: String(error) });
      throw error;
    });
  };
  return provider;
}
