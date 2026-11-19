"""GeoDeploy's own `__gd_*` columns reach the tiles and nothing else (issue #133).

The QGIS plugin writes a feature's geometry measures (`__gd_area` …) into its upload so a style that
says `$area > 4000000` can be evaluated by MapLibre. They must be in the TABLE — Martin publishes
every column, and that is how they reach the tiles — and in no user-facing list or output: the
layer's column list (every field picker, popup and classification choice), exports, OGC API
Features, the GeoLibre GeoJSON. Each edge is checked here against a real ingest into PostGIS.
"""
import json
import uuid

import pytest
from sqlalchemy import text

from geodeploy.services import internal_columns, postgis


def _dsn():
    import os
    return ("host={0} port={1} dbname={2} user={3} password={4}".format(
        os.environ.get("POSTGIS_HOST", "127.0.0.1"), os.environ.get("POSTGIS_PORT", "55432"),
        os.environ.get("POSTGIS_DB", "geodeploy_test"),
        os.environ.get("POSTGIS_USER", "geodeploy"),
        os.environ.get("POSTGIS_PASSWORD", "test")))


def _measured_gpkg(tmp_path):
    """What the plugin uploads: the data plus `__gd_area`."""
    import fiona
    from fiona.crs import CRS
    path = tmp_path / "landuse.gpkg"
    schema = {"geometry": "Polygon",
              "properties": {"name": "str", "fclass": "str", "__gd_area": "float"}}
    with fiona.open(path, "w", driver="GPKG", schema=schema, crs=CRS.from_epsg(4326),
                    layer="landuse") as dst:
        dst.write({"geometry": {"type": "Polygon",
                                "coordinates": [[(13.7, 51.0), (13.8, 51.0), (13.8, 51.1),
                                                 (13.7, 51.0)]]},
                   "properties": {"name": "Friedewald", "fclass": "forest",
                                  "__gd_area": 4123456.5}})
    return path


@pytest.fixture
async def ingested(db, tmp_path):
    from geodeploy.tasks.vector_ingest import _ingest_via_copy
    schema = "gd_internal_{0}".format(uuid.uuid4().hex[:8])
    table = postgis.unique_table_name("landuse")
    res = _ingest_via_copy(_dsn(), schema, table, str(_measured_gpkg(tmp_path)), str(tmp_path),
                           layer="landuse")
    yield schema, table, res
    await db.execute(text('DROP SCHEMA IF EXISTS "{0}" CASCADE'.format(schema)))
    await db.commit()


def test_the_rule_itself():
    assert internal_columns.is_internal("__gd_area")
    assert not internal_columns.is_internal("gd_area") and not internal_columns.is_internal("area")
    assert internal_columns.visible(["name", "__gd_area", "fclass"]) == ["name", "fclass"]


async def test_the_measure_reaches_the_table_martin_serves(db, ingested):
    schema, table, _res = ingested
    cols = set((await db.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t"), {"s": schema, "t": table})).scalars().all())
    assert "__gd_area" in cols, cols
    value = (await db.execute(text('SELECT "__gd_area" FROM "{0}"."{1}"'.format(schema, table)))
             ).scalar()
    assert value == pytest.approx(4123456.5)


async def test_but_not_the_layers_column_list(ingested):
    _schema, _table, res = ingested
    names = [c["name"] for c in res["columns"]]
    assert names == ["name", "fclass"], names


async def test_nor_an_export(ingested):
    import psycopg2
    from geodeploy.tasks import export
    schema, table, _res = ingested
    conn = psycopg2.connect(_dsn())
    try:
        with conn.cursor() as cur:
            geojson, n = export._vec_geojson(cur, schema, table, None, 4326, 4326)
            csv_text, _ = export._vec_csv(cur, schema, table, None, 4326, 4326)
    finally:
        conn.close()
    props = json.loads(geojson)["features"][0]["properties"]
    assert "__gd_area" not in props and props["name"] == "Friedewald"
    assert "__gd_area" not in csv_text and "Friedewald" in csv_text


async def test_nor_ogc_api_features(ingested):
    import asyncpg
    from geodeploy.routers import ogcapi
    schema, table, _res = ingested
    import os
    # The SAME settings `_dsn()` reads: CI's PostGIS is on 5432, a local throwaway on 55432.
    conn = await asyncpg.connect(
        host=os.environ.get("POSTGIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGIS_PORT", "55432")),
        user=os.environ.get("POSTGIS_USER", "geodeploy"),
        password=os.environ.get("POSTGIS_PASSWORD", "test"),
        database=os.environ.get("POSTGIS_DB", "geodeploy_test"))
    try:
        cols = await ogcapi._pg_columns(conn, schema, table, "geom")
    finally:
        await conn.close()
    assert "__gd_area" not in cols and "name" in cols
