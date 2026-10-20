// OpenLayers is the locally bundled global `ol`; this module is Blazor's bridge.
export function wmtsOptions(ol, capabilities, config) {
    const layer = capabilities.Contents?.Layer?.find(item => item.Identifier === config.layer);
    if (!layer) throw new Error(`Layer '${config.layer}' is not advertised.`);
    if (!layer.TileMatrixSetLink?.some(link => link.TileMatrixSet === config.matrixSet))
        throw new Error(`Matrix set '${config.matrixSet}' is not available for this layer.`);
    if (!layer.Format?.includes(config.format))
        throw new Error(`Format '${config.format}' is not available for this layer.`);
    const options = ol.source.WMTS.optionsFromCapabilities(capabilities, {
        layer: config.layer, matrixSet: config.matrixSet,
        format: config.format, requestEncoding: 'KVP'
    });
    if (!options || !ol.proj.equivalent(ol.proj.get(options.projection), ol.proj.get('EPSG:3857')))
        throw new Error('This demo requires a Web Mercator (EPSG:3857/900913) matrix set.');
    // Proxy deployments may advertise an unreachable server name. Use the explicit endpoint.
    return { ...options, urls: [config.endpoint], requestEncoding: 'KVP', crossOrigin: 'anonymous' };
}

export function createMap(element, config, graphics, receiver) {
    if (!element?.isConnected) throw new Error('The map container was removed.');
    const ol = globalThis.ol;
    if (!ol) throw new Error('Local OpenLayers assets could not be loaded.');
    if (!Array.isArray(config.center) || config.center.length !== 2 ||
        !config.center.every(Number.isFinite) || Math.abs(config.center[0]) > 180 ||
        Math.abs(config.center[1]) > 85 || !Number.isFinite(config.zoom))
        throw new Error('Configure a longitude/latitude center (latitude within ±85) and numeric zoom.');

    const styles = {
        Point: new ol.style.Style({ image: new ol.style.Circle({ radius: 7,
            fill: new ol.style.Fill({ color: '#d84924' }), stroke: new ol.style.Stroke({ color: '#fff', width: 2 }) }) }),
        LineString: new ol.style.Style({ stroke: new ol.style.Stroke({ color: '#2168c6', width: 3 }) }),
        Polygon: new ol.style.Style({ fill: new ol.style.Fill({ color: '#22865b33' }),
            stroke: new ol.style.Stroke({ color: '#22865b', width: 2 }) })
    };
    const vector = new ol.layer.Vector({
        source: new ol.source.Vector({ features: new ol.format.GeoJSON().readFeatures(graphics, {
            dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857'
        }) }),
        style: feature => styles[feature.getGeometry().getType()]
    });
    const center = ol.proj.fromLonLat(config.center);
    const map = new ol.Map({ target: element, layers: [vector],
        view: new ol.View({ center, zoom: config.zoom, projection: 'EPSG:3857' }) });
    const abort = new AbortController();
    let disposed = false;
    let tileFailure = false;
    const status = message => {
        if (!disposed) receiver.invokeMethodAsync('SetStatus', message).catch(() => {});
    };
    const resize = new ResizeObserver(() => map.updateSize());
    resize.observe(element);
    const observer = new MutationObserver(() => { if (!element.isConnected) dispose(); });
    observer.observe(document.body, { childList: true, subtree: true });

    function dispose() {
        if (disposed) return;
        disposed = true;
        abort.abort();
        resize.disconnect();
        observer.disconnect();
        map.setTarget(undefined);
        map.dispose();
    }

    async function loadBasemap() {
        status('Graphics ready. Connecting to GeoServer…');
        const timeout = setTimeout(() => abort.abort(), 15000);
        try {
            const endpoint = new URL(config.endpoint, document.baseURI);
            if (!['http:', 'https:'].includes(endpoint.protocol)) throw new Error('WMTS requires an HTTP or HTTPS endpoint.');
            // Remove conflicting operation parameters from pasted service URLs.
            for (const key of [...endpoint.searchParams.keys()]) {
                if (['service', 'request', 'version'].includes(key.toLowerCase())) endpoint.searchParams.delete(key);
            }
            config = { ...config, endpoint: endpoint.href };
            const url = new URL(endpoint);
            url.searchParams.set('SERVICE', 'WMTS');
            url.searchParams.set('REQUEST', 'GetCapabilities');
            url.searchParams.set('VERSION', '1.0.0');
            const response = await fetch(url, { signal: abort.signal, credentials: 'omit' });
            if (!response.ok) throw new Error(`GetCapabilities returned HTTP ${response.status}.`);
            const xml = await response.text();
            const capabilities = new ol.format.WMTSCapabilities().read(xml);
            const options = wmtsOptions(ol, capabilities, config);
            if (disposed) return;
            const source = new ol.source.WMTS(options);
            source.on('tileloaderror', () => {
                tileFailure = true;
                status('Some basemap tiles failed. Check the GeoServer URL, CORS, and tile cache settings. Graphics remain available.');
            });
            source.on('tileloadend', () => {
                if (!tileFailure) status(`Basemap: ${config.layer} · Six sample graphics`);
            });
            map.getLayers().insertAt(0, new ol.layer.Tile({ source }));
            status('GeoServer connected. Loading basemap tiles…');
        } catch (error) {
            status(`Basemap unavailable: ${error.name === 'AbortError' ? 'connection timed out.' : error.message} Graphics remain available.`);
        } finally {
            clearTimeout(timeout);
        }
    }
    void loadBasemap();
    return { resetView() { map.getView().setCenter(center); map.getView().setZoom(config.zoom); }, dispose };
}
