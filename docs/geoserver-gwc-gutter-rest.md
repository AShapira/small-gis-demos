# GeoServer: setting a 20-pixel gutter through REST

## Context

A GeoTIFF with rotated or sheared pixels is published through a GeoTIFF store in GeoServer, around version 2.28. Tiled rendering produces blank triangles near tile borders. Setting the GeoWebCache gutter to 20 pixels resolved the visible issue in the user's test, while retaining the source GeoTIFF's affine grid.

## What is a gutter?

A gutter is an extra border rendered around a tile or metatile. GeoWebCache requests a slightly larger map area at the same pixel resolution, then crops away that border before returning and caching the normal tiles.

For a 256 x 256 tile and a gutter of 20:

```text
Rendered image: 296 x 296 pixels (256 + 20 + 20)
Discarded border: 20 pixels on each side
Delivered tile: 256 x 256 pixels
```

With 4 x 4 metatiling and 256-pixel tiles, GeoWebCache renders a 1024 x 1024 central area plus the gutter: 1064 x 1064 pixels. It removes the outer gutter and divides the central area into sixteen normal tiles.

This can keep edge-rendering artifacts outside the delivered image and provide additional surrounding image data for rendering. A successful gutter workaround does not, by itself, identify the exact underlying renderer defect. Validate the chosen value across the required zoom levels and metatile boundaries.

The gutter does not rewrite the GeoTIFF, change its affine transform, or change the delivered tile dimensions or resolution. It adds rendering work. It applies when requests are handled by GeoWebCache, including GeoServer WMS requests handled through GWC direct integration. An uncached WMS request does not automatically receive this GWC gutter.

Reference: [GeoServer 2.28 caching defaults](https://docs-archive.geoserver.org/2.28.x/en/user/geowebcache/webadmin/defaults.html).

## Configure a layer after publishing it through REST

After creating and publishing the GeoTIFF coverage using GeoServer REST, update its GeoWebCache layer configuration at:

```text
/geoserver/gwc/rest/layers/workspace:layer.xml
```

The XML property is:

```xml
<gutter>20</gutter>
```

It belongs directly inside the existing `<GeoServerLayer>` element. This is a GeoWebCache configuration property, separate from the GeoServer coverage/store creation payload.

Use GET, modify the XML, then PUT the complete configuration back. Retain the other settings, including the layer name and ID, gridsets, formats, metatiling, and parameter filters. GeoServer recommends XML for this API because its JSON representation has issues with some multi-valued properties.

### 1. Download the current configuration

Replace the example URL, workspace, layer name, and administrator username:

```bash
GS_USER='admin'
GWC_LAYER_URL='http://localhost:8080/geoserver/gwc/rest/layers/myworkspace:myraster.xml'

# curl prompts for the password.
curl --fail --silent --show-error --user "$GS_USER" \
  "$GWC_LAYER_URL" --output layer.xml
```

Automatic GWC layer configuration must be enabled for the corresponding tile layer to appear automatically when publishing the GeoTIFF. If the GET returns 404, check the qualified layer name and whether its GWC tile layer exists.

### 2. Set the gutter

Edit `layer.xml`: replace the existing `<gutter>` value with `20`, or add `<gutter>20</gutter>` as a direct child of `<GeoServerLayer>` if absent.

Keep the rest of the downloaded XML. Do not submit only the gutter element or a stripped-down replacement.

### 3. Upload the complete modified configuration

```bash
curl --fail --silent --show-error --user "$GS_USER" \
  --request PUT \
  --header 'Content-Type: application/xml' \
  --data-binary @layer.xml \
  "$GWC_LAYER_URL"
```

### 4. Verify the saved value

```bash
curl --fail --silent --show-error --user "$GS_USER" \
  "$GWC_LAYER_URL"
```

Confirm that the response contains `<gutter>20</gutter>`.

For automated provisioning, use the same sequence: publish the GeoTIFF, GET the GWC layer XML, change the gutter with an XML parser, PUT the complete document, and GET it again to verify.

Reference: [GeoServer 2.28 GeoWebCache REST layer management](https://docs-archive.geoserver.org/2.28.x/en/user/geowebcache/rest/layers.html).

## Set the default for future layers

In the GeoServer administration interface, open:

**Tile Caching -> Caching Defaults -> Default gutter size**

Set the value to **20** and save. With **Automatically configure a GeoWebCache layer for each new layer or layer group** enabled, newly published layers, including layers created through REST, receive the configured default unless explicitly overridden.

Changing this default does not update existing layer configurations. Update those individually through the layer endpoint above.

## Existing cached tiles

Previously generated tiles may still contain the blank triangles. After changing a layer's gutter, truncate the affected layer/gridset/format/zoom cache entries and let GeoWebCache regenerate them, or reseed as needed. Truncation removes generated cache images; it does not modify the source GeoTIFF. Initial requests can be slower while the cache is rebuilt.

For a newly created layer, set its gutter before seeding or serving tiles.

Reference: [GeoServer GeoWebCache seeding and truncating](https://docs.geoserver.org/main/en/user/geowebcache/rest/seed/).
