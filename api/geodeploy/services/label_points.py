"""ONE label per feature — a point source for labelling polygons (and unbent line labels).

## The bug this exists for

A label layer over a polygon source draws the name once per TILE the polygon touches. That is not a
setting anybody chose: MapLibre places a symbol per geometry *as the tile delivers it*, tiles clip,
and a polygon crossing four tiles is four geometries as far as the renderer can see. On a big
polygon the result is the same name repeated across it in a grid — reported exactly that way, and
impossible to switch off from the style, because there is no MapLibre property that means "this is
one feature".

Nor can the client fix it. MapLibre's cross-tile symbol index matches symbols between ZOOM LEVELS
so labels do not flicker while zooming; it does not merge two sibling tiles at the same zoom, and
there is no id it could merge them by unless the data carries one.

## The fix, which is where every vector-tile stack ends up

Label from a POINT rather than from the polygon. A point lies in exactly one tile, so it is
delivered once, placed once, drawn once — no deduplication needed anywhere. OpenMapTiles ships
separate `*_point` layers for this reason; this is the same idea generated on demand, so no upload
has to be re-processed and no table gains a column.

`ST_PointOnSurface` rather than `ST_Centroid`: the centroid of a C-shaped or ring-shaped polygon
falls OUTSIDE it, which puts a country's name in the sea. PointOnSurface is guaranteed to be within
the geometry, which is what a label wants and what QGIS's own "point on surface" placement means.

## per_part

QGIS has "label every part of multi-part features", and it is off by default there too. Off, a
multipolygon gets one label; on, each part gets its own — which is what you want for an archipelago
and not for a country with two islands. `per_part=1` dumps the parts and labels each; the default
labels the feature.

## The shape of this

A Martin FUNCTION source, like `pillars`: one function serves every layer, with the table named by
query parameters on the tile URL, so nothing here grows with the catalog and no DDL runs per upload.
The identifiers arrive from a URL that is public for a published portal, so they are validated
against `information_schema` before they are interpolated, and `%I`-quoted on top of that.
"""
from __future__ import annotations

SCHEMA = "geodeploy"
FUNCTION = "label_points"
QUALIFIED = f"{SCHEMA}.{FUNCTION}"

#: The MVT layer name inside the tile — what a style's `source-layer` must say.
SOURCE_LAYER = "labels"

#: Names the generated query uses for its own geometry, dropped from the attribute list so the row
#: handed to `ST_AsMVT` cannot end up with two columns of the same name.
_RESERVED_COLS = ("g", "lgeom", "mvtgeom")

CREATE_SQL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

CREATE OR REPLACE FUNCTION {QUALIFIED}(z integer, x integer, y integer, query json)
RETURNS bytea AS $$
DECLARE
  src_schema text := coalesce(query->>'schema', '');
  src_table  text := coalesce(query->>'table', '');
  geom_col   text := coalesce(query->>'geom', 'geom');
  per_part   boolean := coalesce((query->>'per_part') IN ('1', 'true', 't', 'yes'), false);
  env        geometry := ST_TileEnvelope(z, x, y);
  part_expr  text;
  cols_t     text;
  cols_s     text;
  srid       integer;
  mvt        bytea;
BEGIN
  -- The caller is a tile URL, PUBLIC for a published portal. Confirm the identifiers name a real
  -- GEOMETRY column of a real table before they reach the query, so a crafted URL cannot point
  -- this at pg_authid: "a column that exists" is satisfied by pg_authid.rolname, and the query
  -- built from it would then fail deep inside with a 500 out of Martin rather than with the empty
  -- tile a request for something that is not a map layer should get.
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = src_schema AND table_name = src_table AND column_name = geom_col
      AND udt_name IN ('geometry', 'geography')
  ) THEN
    RETURN NULL;
  END IF;

  -- The ATTRIBUTES, listed explicitly rather than as `t.*`: the label TEXT is an attribute, a rule
  -- filters on attributes, and `t.*` would also put the source polygon in the tile beside the
  -- label point, handing ST_AsMVT two geometry columns.
  SELECT string_agg(format('t.%I', column_name), ', ' ORDER BY ordinal_position),
         string_agg(format('%I',   column_name), ', ' ORDER BY ordinal_position)
    INTO cols_t, cols_s
  FROM information_schema.columns
  WHERE table_schema = src_schema AND table_name = src_table
    AND column_name <> geom_col
    AND column_name <> ALL (ARRAY[{", ".join(f"'{c}'" for c in _RESERVED_COLS)}]);
  cols_t := CASE WHEN cols_t IS NULL THEN '' ELSE ', ' || cols_t END;
  cols_s := CASE WHEN cols_s IS NULL THEN '' ELSE ', ' || cols_s END;

  -- ONE ROW PER FEATURE, or one per PART when the style asked for it. Built here rather than as a
  -- CASE inside the query because `ST_Dump` is set-returning and Postgres rejects a set-returning
  -- function inside CASE — the choice has to be made while the SQL is still text.
  part_expr := CASE WHEN per_part
                    THEN format('(ST_Dump(t.%I::geometry)).geom', geom_col)
                    ELSE format('t.%I::geometry', geom_col) END;

  -- Read the SRID from the data so the tile envelope can be transformed INTO it below: comparing
  -- `ST_Transform(t.geom, 3857) && env` instead would transform every row on every tile request
  -- and could not use the spatial index.
  EXECUTE format('SELECT ST_SRID(%I) FROM %I.%I WHERE %I IS NOT NULL LIMIT 1',
                 geom_col, src_schema, src_table, geom_col) INTO srid;
  IF srid IS NULL OR srid = 0 THEN
    RETURN NULL;   -- empty table, or geometry with no CRS: nothing that can be placed on a tile
  END IF;

  -- NOTE: no per-cent signs anywhere in this template, comments included. The whole string is a
  -- format() argument, so a stray one is read as a format specifier and the function fails at
  -- RUNTIME rather than at creation.
  EXECUTE format($f$
    WITH parts AS (
      SELECT %9$s AS g%5$s
      FROM %3$I.%4$I t
      WHERE t.%1$I IS NOT NULL
        AND NOT ST_IsEmpty(t.%1$I::geometry)
        AND t.%1$I::geometry && ST_Transform((%7$L)::geometry, %8$s)
    ),
    src AS (
      -- PointOnSurface is inside the shape; a centroid is not, for anything C-shaped or ringed.
      -- A GeometryCollection is the one thing PointOnSurface refuses, and a centroid is a better
      -- answer than an error for a row somebody has already stored.
      SELECT CASE WHEN GeometryType(g) = 'GEOMETRYCOLLECTION'
                  THEN ST_Centroid(g) ELSE ST_PointOnSurface(g) END AS lgeom%6$s
      FROM parts
      WHERE g IS NOT NULL AND NOT ST_IsEmpty(g)
    ),
    tile AS (
      -- NO CLIP BUFFER, deliberately, and it is the whole point of this function: a buffer would
      -- let a point just outside a tile be carried by its neighbour too, which is the duplicate
      -- this exists to remove. With no margin every point belongs to exactly one tile.
      SELECT ST_AsMVTGeom(ST_Transform(lgeom, 3857), (%7$L)::geometry, 4096, 0, true)
             AS mvtgeom%6$s
      FROM src
    )
    SELECT ST_AsMVT(tile, %10$L, 4096, 'mvtgeom') FROM tile WHERE mvtgeom IS NOT NULL
  $f$, geom_col, '', src_schema, src_table, cols_t, cols_s, env, srid, part_expr, '{SOURCE_LAYER}')
  INTO mvt;

  RETURN mvt;
END;
$$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
"""


def tile_url(schema: str, table: str, geom_column: str = "geom", per_part: bool = False) -> str:
    """The browser-facing tile URL for a layer's label points, through nginx's `/tiles/` proxy.

    The layer is named by QUERY PARAMETERS rather than by a per-layer source, for the same reason
    the pillars function does it: one function serves the whole catalog.
    """
    from urllib.parse import urlencode
    query = {"schema": schema, "table": table, "geom": geom_column or "geom"}
    if per_part:
        query["per_part"] = "1"
    return "/tiles/{0}/{{z}}/{{x}}/{{y}}?{1}".format(FUNCTION, urlencode(query))
