"""The two failures a real QGIS-packaged GeoPackage hit, ingested into real PostGIS.

Reported from `QGIS Packaged Layers (D+S Canal).gpkg` and `(OS Open Data).gpkg` — 14 spatial layers
between them, of which the browser ingested 13 and QGIS's push ingested none.

1. **A layer with NO attribute columns.** `Mapping extent` is one polygon with `fid` and `geom` and
   nothing else, so `coldefs` and `copycols` were empty strings and four statements interpolated
   `(, geom)` — a syntax error. It is the one layer that failed in the browser.

2. **A long layer name.** QGIS names a pushed layer `<file>__<layer>`, which for
   `…(D+S Canal)__Notable features (SVG Marker)` slugifies to 58 characters. Everything DERIVED
   from the table name then overflowed Postgres's 63-character identifier limit and truncated back
   to the table's own name — first the geometry index, then the staging table — so `CREATE TABLE`
   met a relation that already existed. That is why none of the 14 arrived from QGIS.

These are ingested for real rather than stubbed: both bugs are in SQL that only a server executes,
and the truncation that caused the second one happens inside Postgres without an error.

The reporter's own files live under `notes_temp/`, which is git-ignored — so the tests that use them
skip everywhere but that machine. `TestBuiltToMatch` rebuilds both shapes with Fiona so CI covers
them too: a layer with an empty property schema, and a name long enough to fill the identifier.
"""
import shutil
import uuid

import pytest
from sqlalchemy import text

from geodeploy.services import postgis

pytestmark = pytest.mark.asyncio

#: What QGIS's plugin calls the layer when it pushes it — the file name, then the layer name. This
#: is the exact string from the report, and its length is the whole point.
PUSHED_NAME = "QGIS_Packaged_Layers__D_S_Canal____Notable_features__SVG_Marker_"


def _gpkg(name):
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "notes_temp" / "delete" / name
    if not path.exists():
        pytest.skip("{0} is not in the tree (it is a user's file, kept out of git)".format(name))
    return path


async def _tables(db, schema):
    rows = (await db.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": schema})).scalars().all()
    return set(rows)


def _dsn():
    import os
    return ("host={0} port={1} dbname={2} user={3} password={4}".format(
        os.environ.get("POSTGIS_HOST", "127.0.0.1"), os.environ.get("POSTGIS_PORT", "55432"),
        os.environ.get("POSTGIS_DB", "geodeploy_test"),
        os.environ.get("POSTGIS_USER", "geodeploy"),
        os.environ.get("POSTGIS_PASSWORD", "test")))


async def _ingest(db, tmp_path, gpkg, layer, layer_name):
    """Run the real ingest for one layer of one file, into a throwaway schema."""
    from geodeploy.tasks.vector_ingest import _ingest_via_copy

    schema = "gd_gpkgtest_{0}".format(uuid.uuid4().hex[:8])
    table = postgis.unique_table_name(layer_name)
    src = tmp_path / gpkg.name
    shutil.copy(gpkg, src)
    _ingest_via_copy(_dsn(), schema, table, str(src), str(tmp_path), layer=layer)
    return schema, table


class TestALayerWithNoAttributes:
    async def test_mapping_extent_ingests(self, db, tmp_path):
        """`(, geom)` — the syntax error that made this the one layer the browser could not take."""
        gpkg = _gpkg("QGIS Packaged Layers (D+S Canal).gpkg")
        schema, table = await _ingest(db, tmp_path, gpkg, "Mapping extent", "Mapping extent")
        assert table in await _tables(db, schema)
        count = (await db.execute(text('SELECT count(*) FROM "{0}"."{1}"'
                                       .format(schema, table)))).scalar()
        assert count == 1
        # `id` and `geom`, and no invented attribute column.
        cols = (await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"), {"s": schema, "t": table})
        ).scalars().all()
        assert set(cols) == {"id", "geom"}, cols
        await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()

    async def test_the_staging_table_is_cleaned_up(self, db, tmp_path):
        gpkg = _gpkg("QGIS Packaged Layers (D+S Canal).gpkg")
        schema, table = await _ingest(db, tmp_path, gpkg, "Mapping extent", "Mapping extent")
        assert await _tables(db, schema) == {table}
        await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()


class TestALayerNameAtTheIdentifierLimit:
    async def test_a_pushed_layer_name_ingests(self, db, tmp_path):
        """The name QGIS pushes. Its table fills the identifier, so every derived name overflows."""
        assert len(postgis.unique_table_name(PUSHED_NAME)) == postgis.MAX_IDENTIFIER
        gpkg = _gpkg("QGIS Packaged Layers (D+S Canal).gpkg")
        schema, table = await _ingest(db, tmp_path, gpkg, "Notable features (SVG Marker)",
                                      PUSHED_NAME)
        assert table in await _tables(db, schema)
        # The staging table must be GONE, not still standing under the destination's own name —
        # that collision is what made `CREATE TABLE` fail.
        assert await _tables(db, schema) == {table}
        await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()

    async def test_it_can_be_ingested_twice_running(self, db, tmp_path):
        """Retrying is what the reporter did, repeatedly, and it never once helped."""
        gpkg = _gpkg("QGIS Packaged Layers (D+S Canal).gpkg")
        made = []
        for _ in range(2):
            schema, table = await _ingest(db, tmp_path, gpkg, "Notable features (SVG Marker)",
                                          PUSHED_NAME)
            made.append((schema, table))
        assert made[0][1] != made[1][1], "two ingests produced the same table name"
        for schema, _ in made:
            await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()

    def test_every_name_derived_from_that_table_still_fits_and_differs(self):
        table = postgis.unique_table_name(PUSHED_NAME)
        for suffix in ("stg", "geom_idx"):
            derived = postgis.derived_name(table, suffix)
            assert len(derived) <= postgis.MAX_IDENTIFIER, derived
            assert derived != table, derived
            assert derived.endswith("_" + suffix), derived


class TestEveryLayerInBothFiles:
    @pytest.mark.parametrize("filename", ["QGIS Packaged Layers (D+S Canal).gpkg",
                                          "QGIS Packaged Layers (OS Open Data).gpkg"])
    async def test_they_all_ingest(self, db, tmp_path, filename):
        """All 14, under the names QGIS pushes them with. 13 of 14 worked before; none did from QGIS."""
        from geodeploy.tasks.vector_ingest import _spatial_layers

        gpkg = _gpkg(filename)
        stem = filename.rsplit(".", 1)[0]
        layers = _spatial_layers(str(gpkg))
        assert layers, filename
        for layer in layers:
            pushed = "{0}__{1}".format(stem, layer)
            schema, table = await _ingest(db, tmp_path, gpkg, layer, pushed)
            assert table in await _tables(db, schema), "{0} :: {1}".format(filename, layer)
            await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()


class TestBuiltToMatch:
    """The same two shapes, built here — so CI covers them without the reporter's files."""

    @staticmethod
    def _build(path, layers):
        import fiona
        from fiona.crs import CRS
        square = [[(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]]
        for name, properties in layers:
            with fiona.open(path, "w", driver="GPKG", layer=name,
                            schema={"geometry": "Polygon", "properties": properties},
                            crs=CRS.from_epsg(4326)) as dst:
                dst.write({"geometry": {"type": "Polygon", "coordinates": square},
                           "properties": {k: "x" for k in properties}})
        return str(path)

    async def test_a_layer_with_an_empty_property_schema(self, db, tmp_path):
        from geodeploy.tasks.vector_ingest import _ingest_via_copy

        src = self._build(tmp_path / "bare.gpkg", [("extent", {}), ("normal", {"name": "str"})])
        schema = "gd_gpkgtest_{0}".format(uuid.uuid4().hex[:8])
        table = postgis.unique_table_name("extent")
        _ingest_via_copy(_dsn(), schema, table, src, str(tmp_path), layer="extent")
        cols = (await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"), {"s": schema, "t": table})
        ).scalars().all()
        assert set(cols) == {"id", "geom"}, cols
        await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()

    async def test_a_name_that_fills_the_identifier(self, db, tmp_path):
        from geodeploy.tasks.vector_ingest import _ingest_via_copy

        src = self._build(tmp_path / "long.gpkg", [("thing", {"name": "str"})])
        schema = "gd_gpkgtest_{0}".format(uuid.uuid4().hex[:8])
        # Long enough that the table name reaches the limit, so `_stg` and any index name
        # appended to it truncate straight back to the table itself.
        table = postgis.unique_table_name("z" * 80)
        assert len(table) == postgis.MAX_IDENTIFIER
        _ingest_via_copy(_dsn(), schema, table, src, str(tmp_path), layer="thing")
        assert await _tables(db, schema) == {table}
        await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
        await db.commit()

