"""A world layer must not 500 every tile because of Antarctica.

Martin tiles in EPSG:3857, undefined beyond ±85.0511°. Geometry reaching the pole makes PostGIS
raise `transform: tolerance condition error (-20)` inside Martin's own ST_Transform, so Martin
answers 500 for EVERY tile at EVERY zoom and the layer never draws.

What makes this expensive to diagnose: the ingest looks completely healthy. The layer is `ready`,
its TileJSON is valid, and the only evidence is in the tile server's log — so the visible symptom is
"a normal PostGIS multipolygon doesn't render", with nothing wrong anywhere you would think to look.

`csv_import` has clamped since 2026-06-04; notes_for_future §0g recorded that `vector_ingest` had
not and named the fix. These tests are that note, made executable.
"""
from geodeploy.tasks.vector_ingest import MERCATOR_MAX_LAT, _store_geom_sql


def test_lonlat_storage_is_clipped_to_the_mercator_band():
    sql = _store_geom_sql(4326)
    assert "ST_Intersection" in sql
    assert "ST_MakeEnvelope(-180, -85.05112878, 180, 85.05112878, 4326)" in sql


def test_the_clip_is_conditional_not_unconditional():
    """ST_Intersection rewrites geometry and is expensive. Data already inside the band must pass
    through untouched — otherwise every import silently rebuilds every polygon it loads."""
    sql = _store_geom_sql(4326)
    assert sql.startswith("CASE WHEN")
    assert f"ST_YMax(geom) > {MERCATOR_MAX_LAT}" in sql
    assert f"ST_YMin(geom) < -{MERCATOR_MAX_LAT}" in sql
    assert "ELSE ST_SetSRID(geom, 4326) END" in sql


def test_projected_storage_is_left_alone():
    """A projected SRID has no lat/lon pole to clamp, and clipping it against a 4326 envelope would
    be nonsense — it would delete the layer. Native-CRS storage is a deliberate feature (lossless
    download, Martin reprojects per tile), so this path must stay a plain ST_SetSRID."""
    for srid in (3006, 3857, 25832, 5070):
        sql = _store_geom_sql(srid)
        assert sql == f"ST_SetSRID(geom, {srid})"
        assert "ST_Intersection" not in sql


def test_the_limit_matches_web_mercator():
    """Not a round 85: the value is where EPSG:3857 actually ends. A looser bound still hands PROJ
    coordinates it refuses."""
    assert MERCATOR_MAX_LAT == 85.05112878
