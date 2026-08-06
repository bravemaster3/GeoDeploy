"""3D pillars for POINT layers — served as buffered polygons through Martin.

## Why points need this at all

MapLibre extrudes FILLS. `fill-extrusion` has no point form, so a point cannot be raised however the
style is written. What people mean by "3D points" is a **column standing at each location**, its
height read from an attribute — GeoLibre and kepler.gl both draw these, and the usual name for them
is a column or pillar layer.

## Why buffered polygons rather than deck.gl

deck.gl has a `ColumnLayer` that does this directly, and we already run deck for GeoParquet layers.
For PostGIS layers we deliberately do NOT use it: those render through Martin tiles into MapLibre,
and routing them through deck when 3D is enabled would mean a layer CHANGES RENDERER mid-style. Every
cross-cutting behaviour then needs a second implementation — identify-on-click
(`queryRenderedFeatures` versus deck picking), the visibility toggle, z-order against the other
MapLibre layers, the legend swatch. Divergence between parallel surfaces is where this codebase's
bugs come from; one more pair is not worth sharing a pillar implementation.

So: give MapLibre what it already knows how to extrude. A point buffered by `radius` metres IS a
polygon, `fill-extrusion` raises it, and the layer stays exactly where it was — same renderer, same
source type, same everything else.

## One function, every layer

Martin function sources receive `(z, x, y, query json)`, so ONE function serves every point layer:
the schema, table, geometry column and radius arrive as query parameters on the tile URL. Per-layer
functions would mean DDL on every upload and a Martin config that grows without bound.

The identifiers from `query` are interpolated into SQL, so they are validated against
`information_schema` first — the parameters reach this function from a tile URL, which is public for
a published portal. `format('%I', …)` quotes them as identifiers on top of that.
"""
from __future__ import annotations

SCHEMA = "geodeploy"
FUNCTION = "point_pillars"
QUALIFIED = f"{SCHEMA}.{FUNCTION}"

#: How round a pillar looks. 4 quadrant segments = a 16-gon, which reads as a cylinder at any zoom a
#: 3D map is useful at, for a quarter of the vertices of the PostGIS default (8 → 32-gon). Tile size
#: matters more than roundness here: these polygons are generated per request, per tile.
QUAD_SEGS = 4

#: Metres. A pillar is a SYMBOL, not a footprint — it marks a location rather than covering ground,
#: so the default is deliberately small and the operator sets it against their own extent.
DEFAULT_RADIUS_M = 30.0

CREATE_SQL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

CREATE OR REPLACE FUNCTION {QUALIFIED}(z integer, x integer, y integer, query json)
RETURNS bytea AS $$
DECLARE
  src_schema text := coalesce(query->>'schema', '');
  src_table  text := coalesce(query->>'table', '');
  geom_col   text := coalesce(query->>'geom', 'geom');
  radius     double precision := coalesce((query->>'radius')::double precision, {DEFAULT_RADIUS_M});
  ok         boolean;
  mvt        bytea;
BEGIN
  -- The caller is a tile URL, which is PUBLIC for a published portal. Confirm the identifiers name
  -- a real column of a real table before they reach the query, so a crafted URL cannot point this
  -- at pg_authid. format('%I') quotes them as well; both, deliberately.
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = src_schema AND table_name = src_table AND column_name = geom_col
  ) INTO ok;
  IF NOT ok THEN
    RETURN NULL;
  END IF;

  -- Clamp: a huge radius turns every tile into an enormous polygon set and is never a real request.
  radius := least(greatest(radius, 0.5), 100000);

  EXECUTE format($f$
    WITH bounds AS (SELECT ST_TileEnvelope(%s, %s, %s) AS env),
    src AS (
      SELECT
        -- Buffer in METRES via geography, then back to Web Mercator for the tile. A degree buffer
        -- would be a different real size at every latitude — visibly wrong on any map wider than a
        -- country, and the one thing a "30 m pillar" must not be.
        ST_Transform(
          ST_Buffer(ST_Transform(t.%%1$I, 4326)::geography, %%2$L, %s)::geometry, 3857) AS geom,
        t.*
      FROM %%3$I.%%4$I t, bounds
      WHERE t.%%1$I IS NOT NULL
        AND ST_Transform(t.%%1$I, 3857) && bounds.env
    ),
    tile AS (
      SELECT ST_AsMVTGeom(src.geom, bounds.env, 4096, 64, true) AS geom, src.*
      FROM src, bounds
      WHERE ST_AsMVTGeom(src.geom, bounds.env, 4096, 64, true) IS NOT NULL
    )
    SELECT ST_AsMVT(tile, 'pillars', 4096, 'geom') FROM tile
  $f$, z, x, y, {QUAD_SEGS})
  USING geom_col, radius, src_schema, src_table
  INTO mvt;

  RETURN mvt;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
"""


def tile_url(schema: str, table: str, geom_column: str = "geom",
             radius: float = DEFAULT_RADIUS_M) -> str:
    """The browser-facing tile URL for a layer's pillars, through nginx's `/tiles/` proxy.

    The layer is identified by QUERY PARAMETERS rather than by a per-layer source, because one
    Martin function serves them all — see the module docstring.
    """
    from urllib.parse import urlencode
    qs = urlencode({"schema": schema, "table": table, "geom": geom_column,
                    "radius": round(float(radius or DEFAULT_RADIUS_M), 2)})
    return f"/tiles/{FUNCTION}/{{z}}/{{x}}/{{y}}?{qs}"


#: The MVT layer name emitted by ST_AsMVT above — what MapLibre needs as `source-layer`.
SOURCE_LAYER = "pillars"
