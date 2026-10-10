import http from "node:http";
import { readFile, appendFile, mkdir } from "node:fs/promises";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

const root = dirname(fileURLToPath(import.meta.url));
const upstream = new URL(process.env.GEOSERVER_WMS || "http://127.0.0.1:18085/geoserver/wms");
const port = Number(process.env.PORT || 18120);
const host = process.env.HOST || "127.0.0.1";
const types = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".json": "application/json", ".png": "image/png",
  ".svg": "image/svg+xml", ".wasm": "application/wasm", ".jpg": "image/jpeg" };
await mkdir(resolve(root, ".artifacts"), { recursive: true });

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");
    if (req.method !== "GET") { res.writeHead(405).end(); return; }
    if (url.pathname === "/wms") {
      // Fixed upstream and read-only GetMap proxy; no credentials or arbitrary destinations.
      const target = new URL(upstream);
      target.search = url.search;
      if (target.searchParams.get("request")?.toLowerCase() !== "getmap") {
        res.writeHead(400).end("Only GetMap is supported"); return;
      }
      const began = performance.now();
      const response = await fetch(target, { signal: AbortSignal.timeout(60000) });
      const bytes = Buffer.from(await response.arrayBuffer());
      const elapsed = performance.now() - began;
      const contentType = response.headers.get("content-type") || "application/octet-stream";
      await appendFile(resolve(root, ".artifacts/proxy.jsonl"), JSON.stringify({
        time: new Date().toISOString(), url: target.href, status: response.status,
        contentType, bytes: bytes.length, upstreamMs: elapsed,
      }) + "\n");
      res.writeHead(response.status, {
        "Content-Type": contentType, "Content-Length": bytes.length,
        "Cache-Control": "no-store", "Server-Timing": `upstream;dur=${elapsed.toFixed(3)}`,
      }).end(bytes);
      return;
    }
    const pathname = decodeURIComponent(url.pathname);
    const filename = resolve(root, "." + (pathname === "/" ? "/index.html" : pathname));
    const entryFile = pathname === "/" || ["/index.html", "/app.js", "/adapter.js", "/style.css", "/fixtures.json", "/cases.json"].includes(pathname);
    const assetFile = ["node_modules/cesium/Build/Cesium", ".artifacts/references"]
      .some(directory => filename.startsWith(resolve(root, directory) + sep));
    if ((!entryFile && !assetFile) || !filename.startsWith(root + sep)) { res.writeHead(404).end(); return; }
    const data = await readFile(filename);
    res.writeHead(200, { "Content-Type": types[extname(filename)] || "application/octet-stream", "Cache-Control": "no-store" }).end(data);
  } catch (error) {
    res.writeHead(error.code === "ENOENT" ? 404 : 502, { "Content-Type": "text/plain" }).end(String(error.message));
  }
});
server.listen(port, host, () => console.log(`Cesium demo: http://${host}:${port}/ → ${upstream.href}`));
