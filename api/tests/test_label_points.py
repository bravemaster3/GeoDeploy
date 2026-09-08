"""A polygon's label is drawn ONCE, not once per tile it touches.

REPORTED AS: "draw labels draw multiple times the same label in a big polygon in geodeploy web".

The cause is not a setting. MapLibre places a symbol per geometry AS THE TILE DELIVERS IT, and a
tile clips — so a polygon crossing four tiles is four geometries to the renderer, each of which
gets the name. There is no style property that means "these four pieces are one shape", and
MapLibre's cross-tile index matches symbols between ZOOM LEVELS (so labels do not flicker while
zooming), not between siblings at one zoom.

So the label is drawn from a POINT instead: `geodeploy.label_points` serves one
`ST_PointOnSurface` per feature, a point lies in exactly one tile, and it is therefore placed once
by construction. `label_per_part` is the option QGIS calls "label every part of multi-part
features" — off in both places — which dumps the parts and labels each.

What must NOT change is everything else: a point layer, a line labelled along its line, a
GeoParquet layer that Martin does not serve at all, and the rule/zoom/colour behaviour of every
label layer. Those are asserted here too, because this moves the source out from under every
polygon label on every existing portal.
"""
import pytest

# NOT `as pg`: the live-SQL half of this file defines a `pg` FIXTURE, and a module-level
# `def pg()` rebinds the name — so every call through it became "'function' object has no attribute".
from geodeploy.services import label_points, portal_generator as generator
from geodeploy.services import symbology as sym


class Poly:
    id = 12
    name = "Mapping extent"
    geometry_type = "MultiPolygon"
    storage_backend = "postgis"
    schema_name = "geodeploy_u1"
    table_name = "extent_abc"
    geometry_column = "geom"
    uid = "u12"
    s3_key = None
    pmtiles_key = None
    bbox = None
    default_style = {}
    tile_status = "ready"


class Point(Poly):
    id = 13
    geometry_type = "Point"
    table_name = "places"


class Line(Poly):
    id = 14
    geometry_type = "LineString"
    table_name = "contours"


class Parquet(Poly):
    id = 15
    storage_backend = "geoparquet"
    table_name = "big_polys"


LABELLED = {"color": "#aabbcc", "labels": {"enabled": True, "field": "name", "size": 12.0}}


def build(layer, style, sources=None):
    """The render layers for one layer, with the style's source map available to register into."""
    sources = {} if sources is None else sources
    cfg = {"layer_id": layer.id, "layer_type": "vector", "opacity": 1.0, "style": style}
    return generator._vector_layers("vector_%s" % layer.id, layer, cfg, sources), sources


def labels_of(built):
    return [ml for ml in built if ml.get("type") == "symbol"
            and "text-field" in (ml.get("layout") or {})]


class TestAPolygonIsLabelledOnce:
    def test_the_label_comes_from_the_point_source(self):
        built, sources = build(Poly(), LABELLED)
        label = labels_of(built)[0]
        assert label["source"] == "labelpts_12", label["source"]
        assert label["source-layer"] == label_points.SOURCE_LAYER

    def test_the_source_is_registered_with_the_layer_it_serves(self):
        _built, sources = build(Poly(), LABELLED)
        src = sources["labelpts_12"]
        assert src["type"] == "vector"
        url = src["tiles"][0]
        assert "/tiles/label_points/{z}/{x}/{y}?" in url, url
        assert "schema=geodeploy_u1" in url and "table=extent_abc" in url, url
        assert "per_part" not in url, "one label per FEATURE is the default"

    def test_the_geometry_still_draws_from_its_own_source(self):
        # The fill must not follow the labels onto a point source — that would draw nothing.
        built, _sources = build(Poly(), LABELLED)
        fills = [ml for ml in built if ml.get("type") == "fill"]
        assert fills and all(ml["source"] == "vector_12" for ml in fills)

    def test_per_part_asks_the_function_for_parts(self):
        style = {"color": "#aabbcc",
                 "labels": {"enabled": True, "field": "name", "label_per_part": True}}
        built, sources = build(Poly(), style)
        assert labels_of(built)[0]["source"] == "labelpts_12_parts"
        assert "per_part=1" in sources["labelpts_12_parts"]["tiles"][0]

    def test_the_two_modes_are_different_sources(self):
        # …or one portal's choice would silently apply to the other's layer through a shared id.
        _b1, sources = build(Poly(), LABELLED)
        _b2, sources = build(Poly(), {"color": "#aabbcc", "labels": {
            "enabled": True, "field": "name", "label_per_part": True}}, sources)
        assert set(sources) == {"labelpts_12", "labelpts_12_parts"}

    def test_a_rule_labelled_polygon_labels_from_points_too(self):
        style = {"color": "#aabbcc", "labels": {
            "enabled": True, "field": "name",
            "rules": [{"label": "big", "filter": [">", ["get", "area"], 10],
                       "labels": {"enabled": True, "field": "name", "color": "#111111"}},
                      {"label": "small", "filter": ["<=", ["get", "area"], 10],
                       "labels": {"enabled": True, "field": "name", "color": "#222222"}}]}}
        built, _sources = build(Poly(), style)
        labels = labels_of(built)
        assert len(labels) == 2
        assert all(ml["source"] == "labelpts_12" for ml in labels)
        # The filters still work: the point tile carries the feature's attributes.
        assert [ml.get("filter") for ml in labels] == [[">", ["get", "area"], 10],
                                                       ["<=", ["get", "area"], 10]]


class TestNothingElseMoves:
    @pytest.mark.parametrize("layer", [Point(), Line(), Parquet()])
    def test_only_polygons_are_labelled_from_points(self, layer):
        built, sources = build(layer, LABELLED)
        label = labels_of(built)[0]
        assert label["source"] == "vector_%s" % layer.id, label["source"]
        assert not sources, "no label-point source should have been registered"

    def test_a_line_labelled_along_its_line_keeps_its_line(self):
        # `symbol-placement: line` REPEATS down the line on purpose — that is what QGIS's curved
        # placement does, and MapLibre's own `symbol-spacing` governs it. One label at the middle
        # of a river would be a different map, not a fixed one.
        style = {"color": "#3388ff", "labels": {"enabled": True, "field": "name",
                                                "placement": "line"}}
        built, sources = build(Line(), style)
        label = labels_of(built)[0]
        assert label["layout"]["symbol-placement"] == "line"
        assert label["source"] == "vector_14"
        assert not sources

    def test_a_polygon_with_no_labels_registers_nothing(self):
        _built, sources = build(Poly(), {"color": "#aabbcc"})
        assert not sources

    def test_a_layer_with_no_table_is_left_alone(self):
        # A layer the instance cannot name cannot be served by the function; labelling it from a
        # source that would 404 loses the labels it has.
        class Nameless(Poly):
            schema_name = None
        built, sources = build(Nameless(), LABELLED)
        assert labels_of(built)[0]["source"] == "vector_12"
        assert not sources

    def test_the_label_keeps_its_paint_and_scope(self):
        style = {"color": "#aabbcc", "labels": {"enabled": True, "field": "name", "size": 9.0,
                                                "color": "#ff0000", "minzoom": 8}}
        built, _sources = build(Poly(), style)
        label = labels_of(built)[0]
        assert label["paint"]["text-color"] == "#ff0000"
        assert label["layout"]["text-size"] == 9.0
        assert label["minzoom"] == 8


class TestTheFunctionItself:
    def test_the_url_names_the_table_and_geometry_column(self):
        url = label_points.tile_url("s", "t", "the_geom")
        assert "schema=s" in url and "table=t" in url and "geom=the_geom" in url

    def test_per_part_is_only_sent_when_asked(self):
        assert "per_part" not in label_points.tile_url("s", "t")
        assert "per_part=1" in label_points.tile_url("s", "t", per_part=True)

    def test_the_sql_validates_its_identifiers_before_using_them(self):
        # The parameters arrive from a tile URL, which is PUBLIC for a published portal.
        sql = label_points.CREATE_SQL
        assert "information_schema.columns" in sql
        assert "udt_name IN ('geometry', 'geography')" in sql

    def test_the_sql_template_has_no_stray_percent(self):
        # The query template is a `format()` ARGUMENT, so a per-cent sign anywhere in it — inside a
        # comment included — is read as a format specifier and the function fails at RUNTIME, when
        # a tile is requested, rather than when it is created.
        start = label_points.CREATE_SQL.index("$f$")
        end = label_points.CREATE_SQL.index("$f$", start + 3)
        template = label_points.CREATE_SQL[start + 3:end]
        import re
        stray = [m.group(0) for m in re.finditer(r"%(?!\d+\$[sIL])", template)]
        assert not stray, stray

    def test_it_clips_with_no_buffer(self):
        # A clip margin would let a point just outside a tile be carried by its neighbour too —
        # which is the duplicate this whole function exists to remove.
        assert "4096, 0, true" in label_points.CREATE_SQL

    def test_it_uses_point_on_surface_not_centroid(self):
        # A centroid falls outside a C-shaped or ring-shaped polygon, which puts a country's name
        # in the sea.
        assert "ST_PointOnSurface" in label_points.CREATE_SQL


class TestTheVocabulary:
    def test_label_per_part_defaults_to_off(self):
        assert sym.label_per_part({}) is False
        assert sym.label_per_part({"enabled": True, "field": "name"}) is False

    def test_and_is_read_when_set(self):
        assert sym.label_per_part({"label_per_part": True}) is True


# ── Against a real PostGIS, because SQL that only looks right is not right ───────────────────────

import math

import pytest_asyncio


@pytest_asyncio.fixture
async def pg():
    """A raw asyncpg connection — the same driver the installer uses in production.

    Deliberately NOT the SQLAlchemy engine: its asyncpg dialect PREPARES every statement, and a
    prepared statement holds one command, so `CREATE_SQL` (a schema plus a function) fails there
    for reasons that have nothing to do with the SQL.
    """
    import asyncpg

    from geodeploy import database
    from geodeploy.config import get_settings
    from geodeploy.services import martin

    assert "test" in str(database.engine.url), "refusing to run: not the throwaway test database"

    conn = await asyncpg.connect(martin._pg_sync_dsn(get_settings()), timeout=10)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        await conn.execute(label_points.CREATE_SQL)
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def shapes(pg):
    """One BIG polygon spanning many tiles, one multipolygon of two parts, one ring."""
    await pg.execute("CREATE SCHEMA IF NOT EXISTS gd_label_t")
    await pg.execute("DROP TABLE IF EXISTS gd_label_t.areas")
    await pg.execute(
        "CREATE TABLE gd_label_t.areas (id serial primary key, name text, "
        "geom geometry(MultiPolygon, 4326))")
    await pg.execute(
        "INSERT INTO gd_label_t.areas (name, geom) VALUES "
        # A 4-degree-wide square over France: at z8 that is several tiles across.
        "('Big square', ST_Multi(ST_MakeEnvelope(0.5, 47.0, 4.5, 50.0, 4326))), "
        # Two separate islands, one feature.
        "('Two islands', ST_Union("
        "   ST_Multi(ST_MakeEnvelope(10.0, 47.0, 10.5, 47.5, 4326)),"
        "   ST_Multi(ST_MakeEnvelope(12.0, 47.0, 12.5, 47.5, 4326)))), "
        # A DONUT, whose centroid is in the hole - the reason this uses PointOnSurface.
        "('Ring', ST_Multi(ST_Difference("
        "   ST_MakeEnvelope(20.0, 47.0, 24.0, 50.0, 4326),"
        "   ST_MakeEnvelope(20.5, 47.5, 23.5, 49.5, 4326))))")
    yield pg
    await pg.execute("DROP SCHEMA IF EXISTS gd_label_t CASCADE")


def _q(per_part=False):
    body = '{"schema":"gd_label_t","table":"areas","geom":"geom"'
    return body + (',"per_part":"1"}' if per_part else "}")


def _tile_xy(lon, lat, z):
    """The tile a lon/lat falls in — the standard slippy-map formula."""
    n = 2 ** z
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    lat_rad = math.radians(lat)
    y = int(math.floor(
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n))
    return x, y


@pytest.mark.asyncio
async def test_the_function_installs_and_returns_a_real_tile(shapes):
    x, y = _tile_xy(2.5, 48.5, 8)
    tile = await shapes.fetchval(
        "SELECT geodeploy.label_points(8, $1, $2, $3::json)", x, y, _q())
    assert tile, "no tile for the zoom-8 tile at the middle of the big square"
    # The label TEXT is an attribute; a tile carrying geometry alone would label nothing.
    assert b"name" in tile and b"Big square" in tile


@pytest.mark.asyncio
async def test_the_big_polygon_is_labelled_in_exactly_one_tile(shapes):
    """THE BUG, MEASURED. The square spans many z8 tiles; only one may carry its label point."""
    lo_x, hi_y = _tile_xy(0.5, 47.0, 8)
    hi_x, lo_y = _tile_xy(4.5, 50.0, 8)
    hits = []
    for x in range(lo_x - 1, hi_x + 2):
        for y in range(lo_y - 1, hi_y + 2):
            tile = await shapes.fetchval(
                "SELECT geodeploy.label_points(8, $1, $2, $3::json)", x, y, _q())
            if tile and b"Big square" in tile:
                hits.append((x, y))
    assert len(hits) == 1, "the label point landed in {0} tiles: {1}".format(len(hits), hits)


@pytest.mark.asyncio
async def test_a_multipart_feature_is_one_label_by_default(shapes):
    """Two islands, one feature, one name - until `per_part` says otherwise."""
    lo_x, y = _tile_xy(9.5, 47.25, 8)
    hi_x, _ = _tile_xy(13.0, 47.25, 8)
    span = range(lo_x, hi_x + 1)

    found = [x for x in span
             if (await shapes.fetchval(
                 "SELECT geodeploy.label_points(8, $1, $2, $3::json)", x, y, _q()) or b""
                 ).find(b"Two islands") >= 0]
    assert len(found) == 1, "one feature, one label: got {0}".format(found)

    per_part = [x for x in span
                if (await shapes.fetchval(
                    "SELECT geodeploy.label_points(8, $1, $2, $3::json)", x, y, _q(True)) or b""
                    ).find(b"Two islands") >= 0]
    assert len(per_part) == 2, "per_part labels each island: got {0}".format(per_part)


@pytest.mark.asyncio
async def test_the_label_point_is_inside_a_ring_shaped_polygon(shapes):
    """A centroid falls in the HOLE of a donut. The label has to be ON the polygon."""
    inside = await shapes.fetchval(
        "SELECT ST_Contains(geom, ST_PointOnSurface(geom)) FROM gd_label_t.areas "
        "WHERE name = 'Ring'")
    assert inside, "the fixture is not actually ring-shaped"
    outside = await shapes.fetchval(
        "SELECT ST_Contains(geom, ST_Centroid(geom)) FROM gd_label_t.areas WHERE name = 'Ring'")
    assert not outside, "...and its centroid must fall outside it, or this proves nothing"


@pytest.mark.asyncio
async def test_a_url_that_names_something_that_is_not_a_layer_gets_nothing(pg):
    """The parameters come from a tile URL, which is public for a published portal."""
    for query in ('{"schema":"pg_catalog","table":"pg_authid","geom":"rolname"}',
                  '{"schema":"gd_label_t","table":"nope","geom":"geom"}',
                  '{"schema":"","table":"","geom":""}'):
        assert await pg.fetchval(
            "SELECT geodeploy.label_points(8, 129, 87, $1::json)", query) is None, query


@pytest.mark.asyncio
async def test_an_empty_or_null_geometry_is_skipped_rather_than_raising(pg):
    """A row somebody has already stored must not 500 the tile for every other row."""
    await pg.execute("CREATE SCHEMA IF NOT EXISTS gd_label_e")
    try:
        await pg.execute(
            "CREATE TABLE gd_label_e.a (id serial primary key, name text, "
            "geom geometry(Geometry, 4326))")
        await pg.execute(
            "INSERT INTO gd_label_e.a (name, geom) VALUES "
            "('empty', ST_GeomFromText('POLYGON EMPTY', 4326)), "
            "('null', NULL), "
            "('collection', ST_Collect(ARRAY["
            "   ST_SetSRID(ST_MakePoint(2.35, 48.85), 4326)::geometry,"
            "   ST_MakeEnvelope(2.31, 48.81, 2.34, 48.84, 4326)::geometry])), "
            "('good', ST_MakeEnvelope(2.30, 48.80, 2.40, 48.90, 4326))")
        q = '{"schema":"gd_label_e","table":"a","geom":"geom"}'
        x, y = _tile_xy(2.35, 48.85, 12)
        tile = await pg.fetchval(
            "SELECT geodeploy.label_points(12, $1, $2, $3::json)", x, y, q)
        assert tile and b"good" in tile
        # A GEOMETRYCOLLECTION is the one thing PointOnSurface refuses; it falls back to a centroid
        # rather than taking the whole tile down with it.
        assert b"collection" in tile
    finally:
        await pg.execute("DROP SCHEMA IF EXISTS gd_label_e CASCADE")


@pytest.mark.asyncio
async def test_a_layer_in_a_native_crs_lands_in_the_right_place(pg):
    """Stored in British National Grid, drawn on a Web Mercator tile."""
    await pg.execute("CREATE SCHEMA IF NOT EXISTS gd_label_b")
    try:
        await pg.execute(
            "CREATE TABLE gd_label_b.a (id serial primary key, name text, "
            "geom geometry(Polygon, 27700))")
        # A square around Bath, in metres.
        await pg.execute(
            "INSERT INTO gd_label_b.a (name, geom) VALUES "
            "('Bath', ST_MakeEnvelope(374000, 163000, 376000, 165000, 27700))")
        q = '{"schema":"gd_label_b","table":"a","geom":"geom"}'
        x, y = _tile_xy(-2.36, 51.38, 12)
        assert await pg.fetchval(
            "SELECT geodeploy.label_points(12, $1, $2, $3::json)", x, y, q), \
            "a layer stored in EPSG:27700 produced no label point on the tile covering it"
    finally:
        await pg.execute("DROP SCHEMA IF EXISTS gd_label_b CASCADE")
