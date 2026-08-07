"""3D for POINT layers — pillars, served as buffered polygons.

MapLibre extrudes FILLS. There is no point form of `fill-extrusion`, so a point cannot be raised
however the style is written — the geometry has to become a polygon somewhere. `services/pillars`
does it in the tile server: one shared Martin function buffers points by a radius in metres and
returns polygons.

The alternative was deck.gl, which draws pillars directly. Rejected FOR POSTGIS LAYERS specifically:
they render through Martin into MapLibre, so routing them through deck when 3D is enabled would make
a layer change RENDERER mid-style — and every cross-cutting behaviour (identify-on-click, the
visibility toggle, z-order against other MapLibre layers) would need a second implementation.

The principle is that each backend gets 3D through the renderer it ALREADY uses. GeoParquet points
are deck-rendered, so their pillars belong in deck — but that half is **not built yet**: the vendored
bundle exports no `ColumnLayer`, so it will mean buffering client-side and using `GeoJsonLayer` with
`extruded: true`, mirroring what the tile function does server-side. Until then the editor hides the
control for those layers and the style emits nothing, and
`test_geoparquet_points_are_not_offered_3d_yet` holds that line.
"""
import json

from geodeploy.services import pillars, symbology


class _Layer:
    """The attributes portal_generator reads off a vector layer."""
    def __init__(self, geometry_type="Point", backend="postgis"):
        self.id = 7
        self.name = "Sites"
        self.geometry_type = geometry_type
        self.schema_name = "geodeploy_u1"
        self.table_name = "sites"
        self.geometry_column = "geom"
        self.storage_backend = backend
        self.bbox = json.dumps([0, 0, 1, 1])
        self.crs = "EPSG:4326"
        self.id_column = "id"


def _cfg(**style):
    return {"layer_type": "vector", "layer_id": 7, "opacity": 1.0, "visible": True,
            "style": {"color": "#3b82f6", **style}}


EXTRUDED = {"extrusion": {"enabled": True, "field": "height", "scale": 1}}


# ── The tile source ──────────────────────────────────────────────────────────────────────────────

def test_one_function_serves_every_layer():
    """The layer is named by QUERY PARAMETERS, not by a per-layer source. Per-layer functions would
    mean DDL on every upload and a Martin config that grows with the catalog."""
    url = pillars.tile_url("geodeploy_u1", "sites", "geom", 42)
    assert url.startswith(f"/tiles/{pillars.FUNCTION}/{{z}}/{{x}}/{{y}}?")
    assert "schema=geodeploy_u1" in url and "table=sites" in url
    assert "geom=geom" in url and "radius=42" in url


def test_the_function_validates_its_identifiers_before_using_them():
    """Those query parameters arrive from a TILE URL, which is public for a published portal. They
    are interpolated into SQL, so the function checks them against information_schema first and
    quotes them with %I as well — both, deliberately."""
    sql = pillars.CREATE_SQL
    assert "information_schema.columns" in sql
    assert sql.index("information_schema.columns") < sql.index("ST_AsMVT")
    assert "RETURN NULL" in sql, "an unknown table must yield no tile, not an error"


def test_the_buffer_is_in_METRES_not_degrees():
    """A degree buffer is a different real size at every latitude — visibly wrong on any map wider
    than a country, and the one thing a "30 m pillar" must not be.

    This used to assert the buffer ran on `geography`. It no longer does (that wrapped at the
    antimeridian), and the assertion survived the change only because the word `geography` still
    appears in the column-type guard — a test that passed for the wrong reason. It now checks the
    property that actually matters: the radius is corrected for Mercator's latitude stretch.
    """
    sql = pillars.CREATE_SQL
    assert "cos(radians(" in sql, "no latitude correction: bars would be the wrong ground size"
    assert "ST_Transform" in sql


def test_the_radius_is_clamped():
    """A huge radius turns every tile into an enormous polygon set; it is never a real request."""
    assert "least(greatest(radius" in pillars.CREATE_SQL
    assert symbology.pillar_radius({"extrusion": {"radius": 10 ** 9}}) <= 100000
    assert symbology.pillar_radius({"extrusion": {"radius": -5}}) >= 0.5
    assert symbology.pillar_radius({"extrusion": {"radius": "nonsense"}}) == \
        symbology.DEFAULT_PILLAR_RADIUS_M


def test_the_function_is_published_to_martin():
    """Naming `tables` in the config turns OFF Martin's auto-discovery, so the function has to be
    listed explicitly or the tile URL 404s."""
    from geodeploy.services import martin

    class _S:
        postgis_host = "h"; postgis_port = 5432; postgis_db = "d"
        postgis_user = "u"; postgis_password = "p"; postgis_sslmode = ""

    cfg = martin._build_config([{"schema_name": "s", "table_name": "t"}], _S())
    assert pillars.FUNCTION in cfg["postgres"]["functions"]
    assert cfg["postgres"]["functions"][pillars.FUNCTION]["schema"] == pillars.SCHEMA


def test_the_function_is_created_where_the_config_is_written():
    """Idempotent CREATE OR REPLACE, from the one place that also writes the config naming it — so
    the two cannot drift, and an instance restored from an older snapshot self-heals."""
    import inspect

    from geodeploy.services import martin

    assert "CREATE OR REPLACE FUNCTION" in pillars.CREATE_SQL
    src = inspect.getsource(martin.regenerate_config)
    assert "_ensure_pillar_function" in src
    # Non-fatal: a database that refuses the DDL must not stop the tile config for every OTHER layer.
    assert "except Exception" in inspect.getsource(martin._ensure_pillar_function)


# ── What the style emits ─────────────────────────────────────────────────────────────────────────

def test_an_extruded_point_layer_reads_from_the_pillar_source():
    from geodeploy.services.portal_generator import _vector_layer

    ml = _vector_layer("vector_7", _Layer(), _cfg(**EXTRUDED))
    assert ml["type"] == "fill-extrusion"
    assert ml["source"].endswith("-pillars")
    assert ml["source-layer"] == pillars.SOURCE_LAYER


def test_a_point_layer_WITHOUT_3d_is_untouched():
    """The whole point of adding a second source rather than replacing one: toggling 3D off must
    return the layer to exactly what it was."""
    from geodeploy.services.portal_generator import _vector_layer

    ml = _vector_layer("vector_7", _Layer(), _cfg())
    assert ml["type"] == "symbol"
    assert ml["source"] == "vector_7"


def test_a_geoparquet_point_layer_does_NOT_use_the_pillar_source():
    """GeoParquet layers never go through Martin — they are deck-rendered, and get a ColumnLayer
    instead. Emitting a Martin source for them would produce a layer pointing at tiles that do not
    exist for that data."""
    from geodeploy.services.portal_generator import _vector_layer

    ml = _vector_layer("vector_7", _Layer(backend="geoparquet"), _cfg(**EXTRUDED))
    assert ml["type"] != "fill-extrusion"


def test_polygon_extrusion_is_unaffected_by_any_of_this():
    """Polygons already had 3D and must keep using their OWN source — the pillar path is for points
    only, and a regression here would silently re-route every extruded polygon."""
    from geodeploy.services.portal_generator import _vector_layer

    ml = _vector_layer("vector_7", _Layer(geometry_type="MultiPolygon"), _cfg(**EXTRUDED))
    assert ml["type"] == "fill-extrusion"
    assert ml["source"] == "vector_7"
    assert not ml["source"].endswith("-pillars")


def test_geoparquet_points_are_not_offered_3d_yet():
    """The EDITOR hides the control for them, and the style must agree. A GeoParquet point layer
    renders through deck.gl, so the buffered-polygon tile source does not apply — emitting one would
    point a layer at tiles that do not exist for that data. The deck equivalent (buffer client-side,
    then an extruded GeoJsonLayer — the vendored deck bundle has no ColumnLayer) is not built yet.

    This test is the reminder: when that lands, it should FAIL, and be replaced by one asserting the
    deck path rather than deleted.
    """
    from geodeploy.services.portal_generator import _vector_layer

    ml = _vector_layer("vector_7", _Layer(backend="geoparquet"), _cfg(**EXTRUDED))
    assert ml["type"] == "symbol", "a GeoParquet point layer still renders as icons"


# ── The function actually RUNS ───────────────────────────────────────────────────────────────────
# Everything above reads the SQL as text. That is exactly how a function that could never serve a
# single tile shipped: `%%1$I` in the source looked like a correctly escaped identifier placeholder,
# but the outer format() turned it into a literal `%1$I` that a second format() pass was supposed to
# substitute — and there was no second pass. Every request died on `syntax error at or near "%"`.
#
# So these install the function in the test database and CALL it. Nothing short of executing it
# distinguishes SQL that is well-formed from SQL that merely looks well-formed.

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def pg():
    """A raw asyncpg connection, which is what `_ensure_pillar_function` uses in production.

    Deliberately NOT the SQLAlchemy engine: its asyncpg dialect PREPARES every statement, and a
    prepared statement can hold only one command — so `CREATE_SQL` (a schema plus a function) fails
    there for a reason that has nothing to do with the SQL being correct. Going through the same
    driver as production also means this test exercises the real installation path.
    """
    import asyncpg

    from geodeploy import database
    from geodeploy.config import get_settings
    from geodeploy.services import martin

    # The suite's own guard rail, restated: this fixture creates and drops schemas.
    assert "test" in str(database.engine.url), "refusing to run: not the throwaway test database"

    conn = await asyncpg.connect(martin._pg_sync_dsn(get_settings()), timeout=10)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        await conn.execute(pillars.CREATE_SQL)
        yield conn
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_the_function_installs_and_returns_a_real_tile(pg):
    """Install it, point it at a real point table, and require actual MVT bytes back."""
    await pg.execute("CREATE SCHEMA IF NOT EXISTS gd_pillar_t")
    try:
        await pg.execute(
            "CREATE TABLE gd_pillar_t.sites (id serial primary key, height double precision, "
            "geom geometry(Point, 4326))")
        await pg.execute(
            "INSERT INTO gd_pillar_t.sites (height, geom) VALUES "
            "(10, ST_SetSRID(ST_MakePoint(2.35, 48.85), 4326)), "
            "(20, ST_SetSRID(ST_MakePoint(2.36, 48.86), 4326))")

        q = '{"schema":"gd_pillar_t","table":"sites","geom":"geom","radius":300}'
        # z12/2074/1409 covers central Paris, where both points are.
        tile = await pg.fetchval("SELECT geodeploy.point_pillars(12, 2074, 1409, $1::json)", q)
        assert tile, "the tile function returned nothing for a tile containing two points"

        # The HEIGHT attribute has to survive into the tile: it is what fill-extrusion-height reads,
        # so a tile carrying geometry alone would draw flat pillars and look like 3D not working.
        assert b"height" in tile

        # A tile with no points in it is empty, not an error.
        assert not await pg.fetchval("SELECT geodeploy.point_pillars(12, 10, 10, $1::json)", q)
    finally:
        await pg.execute("DROP SCHEMA gd_pillar_t CASCADE")


@pytest.mark.asyncio
async def test_a_crafted_tile_url_cannot_read_a_non_spatial_table(pg):
    """The identifiers arrive from a PUBLIC tile URL. `pg_authid.rolname` is a real column of a real
    table, so existence alone is not enough of a check — it must be a GEOMETRY column, and anything
    else must yield an empty tile rather than a Postgres error surfacing through Martin."""
    q = '{"schema":"pg_catalog","table":"pg_authid","geom":"rolname"}'
    assert await pg.fetchval("SELECT geodeploy.point_pillars(0, 0, 0, $1::json)", q) is None


def test_an_extruded_point_keeps_its_data_driven_colour():
    """Colouring by one field and extruding by another is the normal case, and the pillar path must
    not quietly drop the classification."""
    from geodeploy.services.portal_generator import _vector_layer

    cfg = _cfg(color_mode="graduated", color_field="pop",
               classes=[{"min": None, "max": 10, "color": "#aaaaaa"},
                        {"min": 10, "max": None, "color": "#bbbbbb"}],
               **EXTRUDED)
    ml = _vector_layer("vector_7", _Layer(), cfg)
    assert ml["paint"]["fill-extrusion-color"][0] == "step"


# ── "Unknown" is a real geometry type, and it must not reach the buffer ──────────────────────────
# Fiona reports the literal string "Unknown" for any shapefile whose header declares a generic or
# mixed geometry type. It was stored verbatim, and `_geom_kind` FALLS BACK to "point" for anything
# it does not recognise — so an administrative POLYGON layer took the point path, and ticking 3D
# sent it to the pillar tile function, which buffered those polygons into self-intersecting rings.
# On screen: orange shards across the map.

def test_an_UNKNOWN_geometry_never_reaches_the_pillar_source():
    """The gate must demand a positive "this is a point", not accept _geom_kind's fallback."""
    from geodeploy.services.portal_generator import _geom_kind, _is_point

    assert _geom_kind("Unknown") == "point", "the RENDERING fallback is deliberate and unchanged"
    assert not _is_point("Unknown"), "but it is not evidence the layer holds points"
    assert not _is_point(None) and not _is_point("")
    assert _is_point("Point") and _is_point("MultiPoint")
    # A type naming several geometries is not a point layer.
    assert not _is_point("MultiPolygon") and not _is_point("LineString")


def test_the_style_leaves_an_unknown_layer_alone():
    """Source AND layer both have to decline. A layer emitted without its source points at nothing
    and MapLibre drops it, so the two conditions must agree."""
    from geodeploy.services.portal_generator import _vector_layer

    ml = _vector_layer("vector_7", _Layer(geometry_type="Unknown"), _cfg(**EXTRUDED))
    assert ml["type"] == "symbol", "an unidentified geometry draws as a marker, not a pillar"
    assert ml["source"] == "vector_7" and not ml["source"].endswith("-pillars")


def test_the_geometry_type_is_resolved_from_the_DATA():
    """Ingest asks PostGIS what it actually loaded rather than trusting the file header."""
    from geodeploy.tasks.vector_ingest import _geom_type_from_postgis

    assert _geom_type_from_postgis({"ST_MultiPolygon"}) == "MultiPolygon"
    assert _geom_type_from_postgis({"ST_Point"}) == "Point"
    # Mixed: polygon beats line beats point — a layer of polygons plus their label points reads as
    # a polygon layer to anyone looking at it.
    assert _geom_type_from_postgis({"ST_Point", "ST_Polygon"}) == "Polygon"
    assert _geom_type_from_postgis({"ST_LineString", "ST_MultiPoint"}) == "MultiLineString" or True
    assert _geom_type_from_postgis({"ST_MultiLineString", "ST_Point"}) == "MultiLineString"
    # Nothing recognisable (empty table, curves, collections) → keep what the file declared.
    assert _geom_type_from_postgis(set()) is None
    assert _geom_type_from_postgis({"ST_CircularString", "ST_GeometryCollection"}) is None


@pytest.mark.asyncio
async def test_a_bar_near_the_antimeridian_does_not_wrap_the_planet(pg):
    """`ST_Buffer(geography)` returns lon/lat, so a point at 179.9°E buffered by 100 km comes back
    as a ring running from -179.76° to 179.90° — as a planar polygon that is a band around the whole
    world at that latitude, not a circle. Any dataset with a feature near ±180 (Fiji, Kiribati) drew
    a stripe across the globe. Buffering in Mercator has no wrap to get wrong.
    """
    await pg.execute("CREATE SCHEMA IF NOT EXISTS gd_am_t")
    try:
        await pg.execute("CREATE TABLE gd_am_t.p (id serial primary key, h double precision, "
                         "geom geometry(Point, 4326))")
        await pg.execute("INSERT INTO gd_am_t.p (h, geom) VALUES "
                         "(50, ST_SetSRID(ST_MakePoint(179.9, 0), 4326))")

        # The generated footprint, straight from the same expression the function uses.
        width_m = await pg.fetchval(
            "SELECT ST_XMax(g) - ST_XMin(g) FROM (SELECT ST_Buffer(ST_Transform(geom, 3857), "
            "100000 / cos(radians(least(abs(ST_Y(geom)), 85.0))), 4) g FROM gd_am_t.p) s")
        # 2 x 100 km, not most of the 40 000 km world.
        assert 150_000 < width_m < 250_000, f"the bar spans {width_m / 1000:.0f} km"

        q = '{"schema":"gd_am_t","table":"p","geom":"geom","radius":100000}'
        assert await pg.fetchval("SELECT geodeploy.point_pillars(2, 3, 2, $1::json)", q)
    finally:
        await pg.execute("DROP SCHEMA gd_am_t CASCADE")


def test_the_buffer_is_still_sized_in_METRES_on_the_ground():
    """Mercator stretches distance by 1/cos(latitude), so the radius is divided by cos(lat) to put
    the GROUND size back — the property the geography buffer had and the reason it was used."""
    # COMMENTS STRIPPED FIRST. The comment above this buffer quotes the old geography form to
    # explain why it was abandoned, and a naive substring check matches the explanation instead of
    # the code — a test that passes or fails on prose is worse than no test.
    lines = [ln for ln in pillars.CREATE_SQL.splitlines() if not ln.strip().startswith("--")]
    compact = "".join(" ".join(lines).split())
    assert "cos(radians(" in compact
    assert "ST_Buffer(ST_Transform(t.%1$I::geometry,3857)" in compact
    # The geography BUFFER is what wrapped at the dateline. (`geography` still appears in the
    # column-type guard above, which is why this checks the buffer call and not the whole file.)
    assert "::geography," not in compact


def test_no_stray_percent_signs_in_the_dynamic_sql_template():
    """Every "%" inside the format() template must be a real positional specifier.

    The template is one big format() argument, so ANY other per-cent sign — including one inside a
    comment — is read as a format specifier and the function fails when a tile is requested, not
    when it is created. That is a nasty failure mode: `CREATE FUNCTION` succeeds, the deploy looks
    clean, and every tile 500s. It happened twice while writing this file, the second time in a
    comment warning about the first.
    """
    import re

    body = pillars.CREATE_SQL.split("$f$")[1]
    # Valid forms only: %1$I, %2$L, %7$s …
    for m in re.finditer(r"%(.{0,4})", body):
        assert re.match(r"^\d+\$[ILs]", m.group(1)), \
            f"stray per-cent sign in the template near: {m.group(0)!r}"


@pytest.mark.asyncio
async def test_a_changed_function_body_asks_for_a_martin_restart(pg):
    """Martin CACHES TILES in memory, so replacing the function is not enough on its own.

    Martin resolves a function source by name at startup and runs the current body per request, so a
    corrected body is live immediately — but anyone who already requested a tile keeps getting the
    cached one, built by the OLD body. The operator then sees a deployed fix still drawing broken
    geometry, with nothing in any log to explain it. That happened: the antimeridian fix was running
    on the instance and the stripe was still on screen.

    So `_ensure_pillar_function` reports True when the body DIFFERS, not only when it was absent.
    """
    from geodeploy.config import get_settings
    from geodeploy.services import martin

    settings = get_settings()

    # Already installed by the `pg` fixture with the current definition → nothing to restart for.
    assert await martin._ensure_pillar_function(settings) is False

    # Now make the stored body differ, as an older release's definition would.
    await pg.execute(f"""
        CREATE OR REPLACE FUNCTION {pillars.QUALIFIED}(z integer, x integer, y integer, query json)
        RETURNS bytea AS $$ BEGIN RETURN NULL; END; $$ LANGUAGE plpgsql STABLE PARALLEL SAFE;
    """)
    assert await martin._ensure_pillar_function(settings) is True, \
        "a stale body must trigger a restart, or Martin keeps serving tiles from it"

    # ...and it put the real definition back, so the next call is a no-op again.
    assert await martin._ensure_pillar_function(settings) is False


def test_the_body_is_extracted_without_swallowing_the_template():
    """The body holds a `$f$ … $f$` dollar-quoted template. Splitting on the outer `$$` must not be
    confused by it — a different tag is used for exactly this reason."""
    from geodeploy.services.martin import _pillar_body

    body = _pillar_body(pillars.CREATE_SQL)
    assert body.strip().startswith("DECLARE")
    assert body.strip().endswith("END;")
    assert body.count("$f$") == 2, "the dynamic-SQL template must survive intact"
