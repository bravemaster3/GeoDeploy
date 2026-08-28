"""Native-CRS vector storage: the ingest paths keep a resolvable non-4326 EPSG (no reprojection) and
record it; the read round-trip recovers it from the GeoParquet footer; the download path builds a
CRS-correct clip envelope and can output native. Pure-logic units (no DB / fiona / storage)."""
import json

import pytest

from geodeploy.services.duckdb_engine import _crs_to_epsg, _st_transform_4326
from geodeploy.tasks.export import _env_sql, _geom_out
from geodeploy.tasks.vector_ingest import _write_geo_footer, _srid_of


# ── CRS round-trip through the GeoParquet footer (_write_geo_footer → _crs_to_epsg) ─────────────────

class _CapWriter:
    """Captures add_key_value_metadata so we can read back the `geo` footer a real ParquetWriter writes."""
    def __init__(self):
        self.kv = {}
    def add_key_value_metadata(self, d):
        self.kv.update(d)


def test_geo_footer_omits_crs_for_4326():
    w = _CapWriter()
    _write_geo_footer(w, {"Polygon"}, [0, 0, 1, 1], covering_col="bbox", crs_projjson=None)
    geo = json.loads(w.kv["geo"])
    assert "crs" not in geo["columns"]["geometry"]           # absent → reader assumes OGC:CRS84 (4326)
    assert _crs_to_epsg(None) == "EPSG:4326"


def test_geo_footer_writes_native_crs_and_roundtrips():
    from pyproj import CRS
    projjson = CRS.from_epsg(32633).to_json()                # UTM 33N
    w = _CapWriter()
    _write_geo_footer(w, {"Point"}, [0, 0, 1, 1], covering_col="bbox", crs_projjson=projjson)
    col = json.loads(w.kv["geo"])["columns"]["geometry"]
    assert "crs" in col
    assert _crs_to_epsg(col["crs"]) == "EPSG:32633"          # the read path recovers the native EPSG


# ── Export clip envelope + output geometry are CRS-correct ──────────────────────────────────────────

def test_env_sql_transforms_bbox_into_table_srid():
    # bbox is always 4326; for a native table the envelope must be transformed INTO the table SRID.
    assert _env_sql(4326) == "ST_MakeEnvelope(%s,%s,%s,%s,4326)"
    assert _env_sql(32633) == "ST_Transform(ST_MakeEnvelope(%s,%s,%s,%s,4326), 32633)"


def test_geom_out_only_transforms_when_srids_differ():
    assert _geom_out(32633, 32633) == "geom"                 # native download — untouched
    assert _geom_out(32633, 4326) == "ST_Transform(geom, 4326)"
    assert _geom_out(4326, 4326) == "geom"


# ── SRID detection fallback (drives the store-native-vs-4326 decision) ──────────────────────────────

def test_srid_of_none_for_missing_or_unresolvable():
    assert _srid_of(None) is None
    assert _srid_of("") is None


# ── DuckDB reprojection must be LON/LAT, not the authority's axis order ─────────────────────────────
# Regression guard for the bug that made every projected GeoParquet layer tile to the wrong place:
# DuckDB's ST_Transform defaults `always_xy` to FALSE, and EPSG:4326 declares LAT/LON, so the
# three-argument form returned every coordinate swapped. The tiles carried it, nothing errored, and
# the layer's own bbox stayed right (pyproj, always_xy=True) — so the portal fitted to the country
# and drew an empty map. See notes_temp/notes_for_future.md.

def test_st_transform_4326_pins_always_xy():
    """The 4th argument is the whole point — losing it is silent and total."""
    assert _st_transform_4326('"geom"', "EPSG:2056") == (
        """ST_Transform("geom", 'EPSG:2056', 'EPSG:4326', true)""")


def test_st_transform_4326_really_returns_lon_lat():
    """Round-trip through the real extension: LV95 Bern must come back as lon 7.4, lat 46.9 —
    NOT lat 46.9, lon 7.4. Skipped where `spatial` cannot be loaded (it is baked into the api
    image; a bare checkout without it simply does not exercise this)."""
    duckdb = pytest.importorskip("duckdb")
    from geodeploy.services.duckdb_engine import _load_extension
    conn = duckdb.connect(":memory:")
    try:
        try:
            _load_extension(conn, "spatial")
        except Exception:
            pytest.skip("duckdb spatial extension unavailable")
        sql = _st_transform_4326("ST_Point(2600000, 1200000)", "EPSG:2056")
        lon, lat = json.loads(conn.execute(f"SELECT ST_AsGeoJSON({sql})").fetchone()[0])["coordinates"]
    finally:
        conn.close()
    assert 7.4 < lon < 7.5, f"longitude in the longitude slot, got {lon}"
    assert 46.9 < lat < 47.0, f"latitude in the latitude slot, got {lat}"
