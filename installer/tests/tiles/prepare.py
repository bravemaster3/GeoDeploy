"""Build what a real instance has, using GeoDeploy's OWN code for every step it owns.

Runs inside the API image, on the test network, with the Docker socket mounted:
  * PostGIS tables shaped like vector ingest makes them (schema geodeploy_u1, `id` primary key,
    `geom`), covering points, lines, polygons + multipolygons, a 3D table, a projected SRID and
    non-ASCII attribute values;
  * the shared tile functions installed by services/martin._ensure_tile_functions, and the Martin
    config built by _attach_properties + _build_config + _write_config;
  * COGs written by services/cog_converter.convert_to_cog and uploaded to SILO;
  * TiTiler's read-only key minted by services/minio._ensure_readonly_user, and TiTiler itself
    created by services/minio._start_titiler — the wizard's path, with TITILER_IMAGE as shipped.
"""
import asyncio
import json
import os
import sys
import tempfile
import time

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, "/repo/api")

from geodeploy.config import get_settings  # noqa: E402
from geodeploy.services import cog_converter, martin, minio  # noqa: E402

NET = os.environ["GDT_NETWORK"]
OUT = "/out"
SCHEMA = "geodeploy_u1"

# Every table the way GeoDeploy stores it: `id` SERIAL key, `geom`, attributes.
TABLES = {
    "cities": ("Geometry", 4326, "name text, pop integer, area double precision", [
        ("POINT(17.64 59.86)", "'Uppsala', 177074, 48.7"),
        ("POINT(11.97 57.71)", "'Göteborg – ök', 604829, 447.8"),
        ("POINT(18.07 59.33)", "'Stockholm', 984748, 188.0"),
        ("POINT(13.00 55.60)", "'Malmö', 357377, 158.4"),
    ]),
    "roads": ("Geometry", 4326, "name text, lanes integer", [
        ("LINESTRING(17.55 59.80, 17.64 59.86, 17.75 59.90)", "'E4', 4"),
        ("MULTILINESTRING((17.60 59.83, 17.70 59.87),(17.62 59.88, 17.66 59.84))", "'Ringen', 2"),
    ]),
    "parcels": ("Geometry", 4326, "owner text, ha double precision", [
        ("POLYGON((17.60 59.84, 17.68 59.84, 17.68 59.88, 17.60 59.88, 17.60 59.84))", "'Åsa', 12.5"),
        ("MULTIPOLYGON(((17.70 59.85, 17.72 59.85, 17.72 59.86, 17.70 59.85)),"
         "((17.73 59.85, 17.75 59.85, 17.75 59.87, 17.73 59.85)))", "'Örjan', 3.25"),
    ]),
    "buildings3d": ("GeometryZ", 4326, "height double precision", [
        ("POLYGON Z((17.630 59.855 10, 17.640 59.855 10, 17.640 59.860 10, 17.630 59.855 10))", "21.5"),
    ]),
    "sweref": ("Geometry", 3006, "label text", [
        ("POINT(648000 6638000)", "'sweref point'"),
        ("POLYGON((645000 6635000 , 652000 6635000, 652000 6641000, 645000 6635000))", "'sweref poly'"),
    ]),
}


async def vectors(settings) -> list[dict]:
    import asyncpg
    for _ in range(60):
        try:
            conn = await asyncpg.connect(martin._pg_sync_dsn(settings), timeout=5)
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("PostGIS never came up")
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    layers = []
    for table, (gtype, srid, cols, rows) in TABLES.items():
        await conn.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{table}"')
        await conn.execute(f'CREATE TABLE {SCHEMA}."{table}" (id SERIAL PRIMARY KEY, '
                           f'geom geometry({gtype},{srid}), {cols})')
        names = ", ".join(c.split()[0] for c in cols.split(", "))
        for wkt, values in rows:
            await conn.execute(f"INSERT INTO {SCHEMA}.\"{table}\" (geom, {names}) "
                               f"VALUES (ST_GeomFromText('{wkt}', {srid}), {values})")
        # As vector_ingest / csv_import do.
        await conn.execute(f'CREATE INDEX ON {SCHEMA}."{table}" USING GIST (geom)')
        layers.append({"schema_name": SCHEMA, "table_name": table, "geometry_column": "geom",
                       "id_column": "id", "crs": f"EPSG:{srid}"})
    await conn.close()

    installed = await martin._ensure_tile_functions(settings)
    print("tile functions installed/refreshed:", installed)
    enriched = await martin._attach_properties(layers, settings)
    config = martin._build_config(enriched, settings)
    martin._write_config(config, os.path.join(OUT, "martin-config.yaml"))
    return enriched


def _write_cog(name: str, data: np.ndarray, dtype: str, nodata=None) -> str:
    bands = data if data.ndim == 3 else data[None, ...]
    h, w = bands.shape[1:]
    # ~0.2° × 0.1° around Uppsala, in EPSG:4326 like most uploads.
    transform = from_origin(17.54, 59.92, 0.2 / w, 0.1 / h)
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, f"{name}.src.tif")
    with rasterio.open(src, "w", driver="GTiff", width=w, height=h, count=bands.shape[0],
                       dtype=dtype, crs="EPSG:4326", transform=transform, nodata=nodata) as ds:
        ds.write(bands.astype(dtype))
    dst = os.path.join(tmp, f"{name}.tif")
    cog_converter.convert_to_cog(src, dst)
    assert cog_converter.is_cog(dst), f"{name} is not a COG after convert_to_cog"
    return dst


def rasters(settings, root_access: str, root_secret: str) -> dict:
    h, w = 512, 1024
    yy, xx = np.mgrid[0:h, 0:w]
    dem = (300 + 120 * np.sin(xx / 90.0) * np.cos(yy / 70.0) + yy * 0.25).astype("float32")
    dem[:40, :40] = -9999                                     # a nodata corner
    classes = np.select([xx < 256, xx < 512, xx < 768], [1, 2, 3], 5).astype("uint8")
    rgb = np.stack([(xx % 256), (yy % 256), ((xx + yy) % 256)]).astype("uint8")
    multi = np.stack([(xx * 40) % 65535, (yy * 90) % 65535, ((xx + yy) * 30) % 65535,
                      (xx * yy) % 65535]).astype("uint16")
    files = {
        "rasters/dem.tif": _write_cog("dem", dem, "float32", nodata=-9999),
        "rasters/classes.tif": _write_cog("classes", classes, "uint8", nodata=0),
        "rasters/rgb.tif": _write_cog("rgb", rgb, "uint8"),
        "rasters/multi.tif": _write_cog("multi", multi, "uint16"),
    }
    s3 = minio._make_client("http://minio:9000", root_access, root_secret, "us-east-1")
    for _ in range(60):
        try:
            minio._ensure_bucket(s3, settings.storage_bucket)
            break
        except Exception:
            time.sleep(1)
    for key, path in files.items():
        s3.upload_file(path, settings.storage_bucket, key)
    return {"dem_range": [float(dem[dem != -9999].min()), float(dem[dem != -9999].max())]}


def titiler(root_access: str, root_secret: str) -> tuple:
    import docker
    minio.NETWORK = NET                                   # the test network, not a live instance's
    minio.TITILER_NAME = os.environ["GDT_TITILER_NAME"]
    if os.environ.get("GDT_TITILER_IMAGE"):
        minio.TITILER_IMAGE = os.environ["GDT_TITILER_IMAGE"]
    ro = minio._ensure_readonly_user("http://minio:9000", root_access, root_secret,
                                     get_settings().storage_bucket)
    if not ro:
        raise SystemExit("could not mint TiTiler's read-only key")
    client = docker.from_env()
    minio._start_titiler(client, client.networks.get(NET), ro[0], ro[1], "http://minio:9000")
    return ro, minio.TITILER_IMAGE


def main():
    settings = get_settings()
    root_access, root_secret = os.environ["GDT_ROOT_USER"], os.environ["GDT_ROOT_PASSWORD"]
    layers = asyncio.run(vectors(settings))
    raster_meta = rasters(settings, root_access, root_secret)
    ro, image = titiler(root_access, root_secret)
    with open(os.path.join(OUT, "state.json"), "w") as f:
        json.dump({"layers": layers, "rasters": raster_meta, "titiler_ro_user": ro[0],
                   "titiler_image": image}, f, indent=1, ensure_ascii=False)
    print("prepared:", len(layers), "tables,", "4 COGs, TiTiler on", image, "with key", ro[0])


if __name__ == "__main__":
    main()
