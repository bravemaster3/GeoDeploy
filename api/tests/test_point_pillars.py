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
    than a country, and the one thing a "30 m pillar" must not be."""
    sql = pillars.CREATE_SQL
    assert "::geography" in sql
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
