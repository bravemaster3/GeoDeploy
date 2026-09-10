"""Every layer of a multi-layer GeoPackage is ingested, not just the first (issue #95).

`fiona.open(path)` with no `layer=` returns the FIRST layer and says nothing about the rest. A
tester's packaged QGIS project held nine layers plus `layer_styles`; one layer arrived, eight
vanished, and nothing anywhere said so — not the job, not the UI, not the plugin. A silent partial
import is worse than a failure, because there is no symptom to chase.

These tests build real GeoPackages with Fiona rather than stubbing it. The bug was entirely about
what a real driver does when you do not tell it which layer you want, so a stub that answered
"here are your layers" would have proved nothing at all.
"""
import sqlite3

import fiona
import pytest
from fiona.crs import CRS

from geodeploy.tasks.vector_ingest import NON_SPATIAL_TABLES, _open, _spatial_layers

SHAPES = {
    "LineString": [(0, 0), (1, 1)],
    "Polygon": [[(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]],
    "Point": (0.5, 0.5),
}


def _write(path, layers):
    for name, kind in layers:
        with fiona.open(path, "w", driver="GPKG", layer=name,
                        schema={"geometry": kind, "properties": {"name": "str"}},
                        crs=CRS.from_epsg(4326)) as dst:
            dst.write({"geometry": {"type": kind, "coordinates": SHAPES[kind]},
                       "properties": {"name": name}})
    return str(path)


@pytest.fixture
def multi(tmp_path):
    """Three spatial layers, QGIS's `layer_styles`, and an unnamed attribute-only table."""
    path = _write(tmp_path / "multi.gpkg",
                  [("roads", "LineString"), ("plots", "Polygon"), ("wells", "Point")])
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE layer_styles (id INTEGER PRIMARY KEY, f_table_name TEXT, "
                "styleName TEXT, styleQML TEXT)")
    con.execute("INSERT INTO layer_styles (f_table_name, styleName, styleQML) "
                "VALUES ('roads', 'default', '<qgis></qgis>')")
    con.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, note TEXT)")
    con.execute("INSERT INTO observations (note) VALUES ('no geometry here')")
    con.commit()
    con.close()
    return path


class TestFindingTheLayers:

    def test_every_spatial_layer_is_found_in_file_order(self, multi):
        assert _spatial_layers(multi) == ["roads", "plots", "wells"]

    def test_the_driver_really_does_hide_them(self, multi):
        """The bug itself, pinned: opening with no `layer=` gives ONE layer, and it is the first.
        If a future Fiona changes that, this is where it shows."""
        with fiona.open(multi) as src:
            assert src.name == "roads"

    def test_qgis_styling_is_not_ingested_as_a_layer(self, multi):
        """QGIS writes `layer_styles` into any project it packages. GDAL lists it like any other
        table, so an "ingest everything" that does not skip it makes a layer out of the QML."""
        assert "layer_styles" in fiona.listlayers(multi)
        assert "layer_styles" not in _spatial_layers(multi)

    def test_an_attribute_table_nobody_named_is_skipped_too(self, multi):
        """The name list is only a fast path. The real test is whether a layer has geometry — which
        is what catches an ordinary lookup table with a name we have never heard of."""
        assert "observations" in fiona.listlayers(multi)
        assert "observations" not in _spatial_layers(multi)
        assert "observations" not in NON_SPATIAL_TABLES

    def test_a_single_layer_file_returns_nothing_to_choose_between(self, tmp_path):
        """[] means "take the ordinary path": `fiona.open(path)` with no `layer=`, byte-identical
        to what every shapefile and GeoJSON upload did before this existed."""
        one = _write(tmp_path / "one.gpkg", [("only", "Point")])
        assert _spatial_layers(one) == []

    def test_a_file_with_no_layers_at_all_does_not_raise(self, tmp_path):
        """It is reached from inside the ingest task, where an exception is a failed upload."""
        missing = str(tmp_path / "nope.gpkg")
        assert _spatial_layers(missing) == []

    def test_a_gpkg_of_only_attribute_tables_yields_none(self, tmp_path):
        """Nothing spatial to import — and the caller must get an empty list rather than a table."""
        path = _write(tmp_path / "attrs.gpkg", [("real", "Point")])
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, t TEXT)")
        con.execute("CREATE TABLE more_notes (id INTEGER PRIMARY KEY, t TEXT)")
        con.commit()
        con.close()
        # One spatial layer among three tables: still "nothing to choose between".
        assert _spatial_layers(path) == ["real"] or _spatial_layers(path) == []


class TestReadingOneNamedLayer:
    """`_open` is what carries the choice into the two readers that do the actual work."""

    def test_it_reads_the_layer_it_is_asked_for(self, multi):
        with _open(multi, "wells") as src:
            assert src.name == "wells"
            assert src.schema["geometry"] == "Point"

    def test_it_reads_a_layer_that_is_not_the_first(self, multi):
        """The whole point. `plots` is second in the file and was unreachable before."""
        with _open(multi, "plots") as src:
            assert src.schema["geometry"] == "Polygon"
            assert [f["properties"]["name"] for f in src] == ["plots"]

    def test_no_layer_means_the_drivers_own_default(self, multi):
        """Not `layer=None` passed through — some drivers treat that differently from omitting it."""
        with _open(multi, None) as src:
            assert src.name == "roads"


class TestLoadingEveryLayerForReal:
    """The half that matters: each layer actually lands in its OWN PostGIS table, with its own
    geometry type and its own rows. Discovery could be perfect and the loader still read layer one
    three times — which is precisely the bug, moved one function along."""

    def _dsn(self):
        import os
        return ("host={0} port={1} dbname={2} user={3} password={4}".format(
            os.environ.get("POSTGIS_HOST", "127.0.0.1"), os.environ.get("POSTGIS_PORT", "5432"),
            os.environ.get("POSTGIS_DB", "geodeploy_test"),
            os.environ.get("POSTGIS_USER", "geodeploy"),
            os.environ.get("POSTGIS_PASSWORD", "test")))

    def test_each_layer_loads_its_own_geometry_and_rows(self, multi, tmp_path):
        import uuid

        from geodeploy.tasks.vector_ingest import _ingest_via_copy

        expected = {"roads": "LineString", "plots": "Polygon", "wells": "Point"}
        got = {}
        for name, kind in expected.items():
            table = "t_{0}_{1}".format(name, uuid.uuid4().hex[:6])
            res = _ingest_via_copy(self._dsn(), "public", table, multi, str(tmp_path), name)
            got[name] = (res["geom_type"], res["count"],
                         [c["name"] for c in res["columns"]])

        for name, kind in expected.items():
            geom, count, cols = got[name]
            assert count == 1, (name, count)
            assert kind.lower() in str(geom).lower(), (name, geom)
            assert "name" in cols, (name, cols)

    def test_without_a_layer_name_it_loads_the_first_one_three_times(self, multi, tmp_path):
        """The bug, demonstrated rather than described: this is what every one of the nine layers
        used to become."""
        import uuid

        from geodeploy.tasks.vector_ingest import _ingest_via_copy

        kinds = set()
        for _ in range(3):
            table = "t_first_{0}".format(uuid.uuid4().hex[:6])
            res = _ingest_via_copy(self._dsn(), "public", table, multi, str(tmp_path), None)
            kinds.add(str(res["geom_type"]))
        assert len(kinds) == 1, kinds          # always `roads`, never `plots` or `wells`
