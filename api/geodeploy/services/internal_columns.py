"""Columns that belong to GeoDeploy rather than to the data: named `__gd_*`.

The QGIS plugin writes a feature's geometry MEASURES into its upload — `__gd_area`,
`__gd_length`, `__gd_perimeter`, computed by QGIS with the project's ellipsoid and units — because
a style can depend on them (`$area > 4000000` labels only large forests) and a web map cannot
measure a clipped vector tile (issue #133). They have to reach the TILES, where the style's filters
read them, and nowhere else: not the layer's column list (field pickers, popup and classification
choices), not a popup or the attribute table, not an export, not OGC API Features.

One rule, one prefix, applied at each of those edges. The tiles get them because Martin publishes
every column of the table, which is exactly the one place they are wanted.
"""
from __future__ import annotations

PREFIX = "__gd_"

#: The measure columns the plugin writes. A fixed list so SQL can strip them without first asking
#: the table what it has: removing a key a row does not have is a no-op in jsonb.
MEASURE_COLUMNS = ("__gd_area", "__gd_length", "__gd_perimeter")

#: For `to_jsonb(t) - <this>`: a text[] literal of every internal column.
JSONB_STRIP = "'{" + ",".join(MEASURE_COLUMNS) + "}'::text[]"


def is_internal(name) -> bool:
    return str(name or "").startswith(PREFIX)


def visible(names):
    """`names` without the internal ones, order kept."""
    return [n for n in names if not is_internal(n)]
