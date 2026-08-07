"""3D bars for POINT layers — served as buffered polygons through Martin.

NAMING: the UI calls these **bars** — the word people actually use — while the code says "pillars"
throughout and the Martin function is `point_pillars`. Deliberately not renamed to match: that
function name is baked into the tile URL of every PUBLISHED portal bundle, so changing it would 404
every existing 3D point layer until each portal was re-published. A cosmetic rename is not worth
breaking published maps for.

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

#: Names the generated query uses for its own geometry. A source column called one of these would
#: collide with it in the attribute list, so they are dropped from the attributes — losing an oddly
#: named attribute is survivable, two columns of the same name in the row handed to `ST_AsMVT` is not.
_RESERVED_COLS = ("pgeom", "mvtgeom")

CREATE_SQL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

CREATE OR REPLACE FUNCTION {QUALIFIED}(z integer, x integer, y integer, query json)
RETURNS bytea AS $$
DECLARE
  src_schema text := coalesce(query->>'schema', '');
  src_table  text := coalesce(query->>'table', '');
  geom_col   text := coalesce(query->>'geom', 'geom');
  radius     double precision := coalesce((query->>'radius')::double precision, {DEFAULT_RADIUS_M});
  env        geometry := ST_TileEnvelope(z, x, y);
  cols_t     text;
  cols_s     text;
  srid       integer;
  mvt        bytea;
BEGIN
  -- The caller is a tile URL, which is PUBLIC for a published portal. Confirm the identifiers name
  -- a real GEOMETRY column of a real table before they reach the query, so a crafted URL cannot
  -- point this at pg_authid. format('%I') quotes them as well; both, deliberately.
  --
  -- The type check is not decoration: "a column that exists" is satisfied by pg_authid.rolname, and
  -- the query built from it then fails deep inside with a Postgres error (a 500 out of Martin)
  -- rather than the empty tile that a request for something that is not a map layer should get.
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = src_schema AND table_name = src_table AND column_name = geom_col
      AND udt_name IN ('geometry', 'geography')
  ) THEN
    RETURN NULL;
  END IF;

  -- The ATTRIBUTES to carry into the tile, listed explicitly rather than as `t.*`. The height a
  -- pillar is drawn at is an attribute, so this is not optional decoration — and `t.*` would put
  -- the source POINT geometry in the tile beside the buffered polygon, giving the row handed to
  -- ST_AsMVT two geometry columns.
  SELECT string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal_position),
         string_agg(format('%I',   column_name), ', ' ORDER BY ordinal_position)
    INTO cols_t, cols_s
  FROM information_schema.columns
  WHERE table_schema = src_schema AND table_name = src_table
    AND column_name <> geom_col
    AND column_name <> ALL (ARRAY[{", ".join(f"'{c}'" for c in _RESERVED_COLS)}]);
  -- A table whose only column is the geometry yields NULL, not ''.
  cols_t := CASE WHEN cols_t IS NULL THEN '' ELSE ', ' || cols_t END;
  cols_s := CASE WHEN cols_s IS NULL THEN '' ELSE ', ' || cols_s END;

  -- Clamp: a huge radius turns every tile into an enormous polygon set and is never a real request.
  radius := least(greatest(radius, 0.5), 100000);

  -- Read the SRID from the data so the tile envelope can be transformed INTO it below. Comparing
  -- `ST_Transform(t.geom, 3857) && env` instead would transform every row in the table on every
  -- tile request and could not use the spatial index; this way the index does the work.
  EXECUTE format('SELECT ST_SRID(%I) FROM %I.%I WHERE %I IS NOT NULL LIMIT 1',
                 geom_col, src_schema, src_table, geom_col) INTO srid;
  IF srid IS NULL OR srid = 0 THEN
    RETURN NULL;   -- empty table, or geometry with no CRS: nothing that can be placed on a tile
  END IF;

  EXECUTE format($f$
    WITH src AS (
      SELECT
        -- Buffer in WEB MERCATOR, with the radius divided by cos(latitude).
        --
        -- The obvious implementation — `ST_Buffer(geom::geography, radius)` — is correct everywhere
        -- except across the antimeridian, and there it fails spectacularly. A geography buffer
        -- returns lon/lat, so a point at 179.9°E buffered by 100 km comes back as a ring running
        -- from -179.76° to 179.90°: as a planar polygon that is not a circle at the dateline, it is
        -- a band wrapping the ENTIRE planet at that latitude. Any dataset with a feature near ±180
        -- (Fiji, Kiribati, NZ's outer islands) drew a stripe right around the globe.
        --
        -- In Mercator there is no wrap to get wrong: the buffer is planar, coordinates simply run
        -- past the world edge, and ST_AsMVTGeom clips them to the tile. The worst case becomes a bar
        -- cut off at the edge instead of a stripe around the world.
        --
        -- Mercator distances are stretched by 1/cos(latitude), so dividing by cos(lat) puts the
        -- GROUND size back to `radius` metres — the same real-world size the geography buffer gave,
        -- which is the property that matters (a degree buffer would be a different size at every
        -- latitude). Clamped at 85° because cos → 0 at the poles, where Mercator has no meaning
        -- anyway.
        ST_Buffer(
          ST_Transform(t.%1$I::geometry, 3857),
          (%2$L)::double precision
            / cos(radians(least(abs(ST_Y(ST_Transform(t.%1$I::geometry, 4326))), 85.0))),
          {QUAD_SEGS}) AS pgeom%5$s
      FROM %3$I.%4$I t
      WHERE t.%1$I IS NOT NULL
        AND t.%1$I::geometry && ST_Transform((%7$L)::geometry, %8$s)
    ),
    tile AS (
      -- A 256-unit clip margin (of 4096), not the usual 64. A bar is generated at a fixed size in
      -- METRES, so at low zoom it can be a large fraction of a tile — and one straddling a tile
      -- boundary gets cut, leaving a bar with its side sliced open. 64 units is about a sixtieth of
      -- a tile, smaller than the symbol itself once you are zoomed out; 256 gives it room. Tiles
      -- grow slightly, and only where geometry actually crosses an edge.
      --
      -- NOTE: no per-cent signs anywhere in this template, comments included. The whole string is a
      -- format() argument, so a stray one is read as a format specifier and the function fails at
      -- RUNTIME, not at creation — see the module docstring.
      SELECT ST_AsMVTGeom(pgeom, (%7$L)::geometry, 4096, 256, true) AS mvtgeom%6$s
      FROM src
    )
    SELECT ST_AsMVT(tile, 'pillars', 4096, 'mvtgeom') FROM tile WHERE mvtgeom IS NOT NULL
  $f$, geom_col, radius, src_schema, src_table, cols_t, cols_s, env, srid)
  INTO mvt;

  RETURN mvt;
END;
$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
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
