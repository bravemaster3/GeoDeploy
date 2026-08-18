# GeoDeploy for QGIS

Browse a [GeoDeploy](https://github.com/bravemaster3/GeoDeploy) instance from inside QGIS, add its
layers, restyle them, and publish back — without exporting anything.

**Full documentation: <https://docs-geodeploy.kndev.org/qgis/>**

## What it does

- **Browse an instance** by pasting its URL. No account is needed for public data; a token adds
  whatever else you can see.
- **Add a layer** from the fastest source it offers — vector tiles, OGC API - Features, a COG or a
  PMTiles archive — chosen per layer, with a picker that says what each one costs.
- **Open a portal as a QGIS group**, in its own order, folders and styling. Either as the portal
  draws it, or with every layer opened from its data so all of QGIS's symbology applies.
- **Restyle and publish back.** Symbology travels both ways: single symbol, graduated and
  categorized; colour, marker, size, line width and dash, fill opacity, outline colour and width;
  size from a field; and for rasters the colour ramp, stretch, band, classes, hillshade and
  contours.
- **Upload** a QGIS layer, including multi-gigabyte files, which go straight to object storage.

## Requirements

QGIS **3.28** or newer. Nothing to install: the Python client is vendored, so the plugin has no
dependencies.

## Known limitations

- 3D extrusion is carried safely — a round trip cannot delete it — but is **not drawn** in QGIS's
  3D view yet.
- Symbology QGIS has and GeoDeploy does not (inverted polygons, 2.5D, hatch and gradient fills,
  rule-based rendering, labels) is simplified on the way in and not carried back.

Both are tracked on the [roadmap](https://docs-geodeploy.kndev.org/roadmap/).

## Licence and issues

Apache-2.0. Issues and source: <https://github.com/bravemaster3/GeoDeploy>
