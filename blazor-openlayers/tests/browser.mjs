// Optional developer check; Playwright is not an app/build dependency.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../.artifacts/browser/node_modules/playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const published = resolve(root, process.env.PUBLISH_DIR || '.artifacts/publish/wwwroot');
const evidence = resolve(root, '.artifacts/browser-evidence');
await mkdir(evidence, { recursive: true });
const upstream = process.env.WMTS_URL; // Optional live service; proxied without modifying GeoServer.
const layer = upstream ? (process.env.WMTS_LAYER || 'topp:states') : 'demo:states';
const matrixSet = upstream ? (process.env.WMTS_MATRIX_SET || 'EPSG:900913') : 'EPSG:900913';
const requests = [], blocked = [], errors = [];
let scenario = 'success';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
const matrices = Array.from({ length: 9 }, (_, z) => `<TileMatrix><ows:Identifier>EPSG:900913:${z}</ows:Identifier><ScaleDenominator>${559082264.0287178 / 2 ** z}</ScaleDenominator><TopLeftCorner>-20037508.342789244 20037508.342789244</TopLeftCorner><TileWidth>256</TileWidth><TileHeight>256</TileHeight><MatrixWidth>${2 ** z}</MatrixWidth><MatrixHeight>${2 ** z}</MatrixHeight></TileMatrix>`).join('');
const capabilities = `<?xml version="1.0"?><Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.0.0"><ows:OperationsMetadata><ows:Operation name="GetTile"><ows:DCP><ows:HTTP><ows:Get xlink:href="https://unreachable.example/wmts"><ows:Constraint name="GetEncoding"><ows:AllowedValues><ows:Value>KVP</ows:Value></ows:AllowedValues></ows:Constraint></ows:Get></ows:HTTP></ows:DCP></ows:Operation></ows:OperationsMetadata><Contents><Layer><ows:Identifier>demo:states</ows:Identifier><Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style><Format>image/png</Format><TileMatrixSetLink><TileMatrixSet>EPSG:900913</TileMatrixSet></TileMatrixSetLink></Layer><TileMatrixSet><ows:Identifier>EPSG:900913</ows:Identifier><ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>${matrices}</TileMatrixSet></Contents></Capabilities>`;
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm', '.dat': 'application/octet-stream' };
const server = createServer(async (req, res) => {
    try {
        const url = new URL(req.url, 'http://localhost');
        if (url.pathname === '/map-config.json') {
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ endpoint: '/wmts', layer: scenario === 'bad-layer' ? 'missing' : layer,
                matrixSet: scenario === 'bad-matrix' ? 'missing' : matrixSet,
                format: scenario === 'bad-format' ? 'image/nope' : 'image/png', center: [-98, 39], zoom: 4 }));
            return;
        }
        if (url.pathname === '/wmts') {
            const params = Object.fromEntries([...url.searchParams].map(([key, value]) => [key.toUpperCase(), value]));
            const operation = params.REQUEST;
            requests.push({ scenario, operation, matrix: params.TILEMATRIX, url: url.href });
            if (scenario === 'unavailable' || (scenario === 'tile-error' && operation === 'GetTile')) {
                res.writeHead(503); res.end('Unavailable'); return;
            }
            if (upstream) {
                const remote = new URL(upstream);
                remote.search = url.search;
                const response = await fetch(remote, { signal: AbortSignal.timeout(30000) });
                res.writeHead(response.status, { 'Content-Type': response.headers.get('content-type') || 'application/octet-stream' });
                res.end(Buffer.from(await response.arrayBuffer()));
            } else if (operation === 'GetCapabilities') {
                res.setHeader('Content-Type', 'application/xml');
                res.end(scenario === 'malformed' ? '<html>Not capabilities</html>' : capabilities);
            } else {
                assert.equal(operation, 'GetTile');
                assert.match(params.TILEMATRIX, /^EPSG:900913:\d+$/);
                assert.equal(params.STYLE, 'default');
                res.setHeader('Content-Type', 'image/png'); res.end(png);
            }
            return;
        }
        const path = resolve(published, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname));
        if (!path.startsWith(published + sep)) { res.writeHead(403); res.end(); return; }
        let body = await readFile(path);
        // Observe the real OpenLayers map without adding debug API to the product.
        if (url.pathname.endsWith('/vendor/openlayers/ol.js')) body = Buffer.concat([body, Buffer.from('\nwindow.__maps=[];const BaseMap=ol.Map;ol.Map=class extends BaseMap{constructor(o){super(o);window.__maps.push(this);}};')]);
        res.setHeader('Content-Type', mime[extname(path)] || 'application/octet-stream'); res.end(body);
    } catch (error) { res.writeHead(500); res.end(String(error)); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined });
    const context = await browser.newContext({ viewport: { width: 1280, height: 950 }, serviceWorkers: 'block' });
    await context.route('**/*', async route => {
        if (new URL(route.request().url()).origin !== origin) {
            blocked.push(route.request().url()); await route.abort();
        } else await route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    page.setDefaultTimeout(45000);
    async function load(expected) {
        await page.goto(origin);
        try {
            await page.waitForFunction(text => document.querySelector('[role=status]')?.textContent.includes(text), expected);
        } catch (error) {
            console.error('Page text:', await page.locator('body').innerText());
            console.error('Page errors:', errors);
            await page.screenshot({ path: resolve(evidence, 'failure.png'), fullPage: true });
            throw error;
        }
        assert.equal(await page.evaluate(() => window.__maps[0].getLayers().getArray().find(l => l instanceof ol.layer.Vector).getSource().getFeatures().length), 6);
    }
    await load(`Basemap: ${layer}`);
    const geometry = await page.evaluate(() => {
        const map = window.__maps[0];
        const features = map.getLayers().item(1).getSource().getFeatures();
        return { types: features.map(f => f.getGeometry().getType()),
            denver: ol.proj.toLonLat(features[0].getGeometry().getCoordinates()), center: ol.proj.toLonLat(map.getView().getCenter()) };
    });
    assert.deepEqual(geometry.types, ['Point', 'Point', 'LineString', 'LineString', 'Polygon', 'Polygon']);
    assert.ok(Math.abs(geometry.denver[0] + 104.99) < 1e-6 && Math.abs(geometry.denver[1] - 39.74) < 1e-6);
    assert.ok(requests.some(r => r.operation === 'GetTile'));
    await page.evaluate(() => new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Map rendering did not finish')), 30000);
        const map = window.__maps[0];
        map.once('rendercomplete', () => { clearTimeout(timer); resolve(); });
        map.render();
    }));
    await page.screenshot({ path: resolve(evidence, upstream ? 'live-geoserver.png' : 'fixture.png'), fullPage: true });
    await page.locator('.ol-zoom-in').click();
    await page.waitForFunction(() => window.__maps[0].getView().getZoom() > 4.9);
    await page.locator('.map').focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => Math.abs(ol.proj.toLonLat(window.__maps[0].getView().getCenter())[0] + 98) > .1);
    await page.getByRole('button', { name: 'Reset view' }).click();
    await page.waitForFunction(() => Math.abs(window.__maps[0].getView().getZoom() - 4) < .01 && Math.abs(ol.proj.toLonLat(window.__maps[0].getView().getCenter())[0] + 98) < .001);
    await page.setViewportSize({ width: 600, height: 800 });
    await page.waitForFunction(() => window.__maps[0].getSize()[0] < 600);
    for (const [name, message] of [['bad-layer', 'not advertised'], ['bad-matrix', 'not available'], ['bad-format', 'not available'], ['unavailable', 'HTTP 503'], ['tile-error', 'tiles failed']]) {
        scenario = name; await load(message);
        assert.equal(await page.getByRole('button', { name: 'Reset view' }).isEnabled(), true);
    }
    if (!upstream) { scenario = 'malformed'; await load('Basemap unavailable'); }
    // Removal must disconnect map, observers, and pending capabilities work.
    await page.evaluate(() => document.querySelector('.map').remove());
    await page.waitForFunction(() => window.__maps[0].getTarget() == null);
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, [], 'App attempted external requests');
    console.log(`PASS: ${upstream ? 'live GeoServer through test proxy' : 'WMTS fixture'}, graphics, controls, resize, failure paths, cleanup, zero external requests.`);
} finally {
    await writeFile(resolve(evidence, upstream ? 'live-results.json' : 'fixture-results.json'), JSON.stringify({ upstream: upstream || null, requests, blocked, errors }, null, 2));
    await browser?.close();
    await new Promise(done => server.close(done));
}
