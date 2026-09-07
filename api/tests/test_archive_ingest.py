"""What is inside the ZIP, and whether the ingest can find it.

A ZIP is accepted because a SHAPEFILE is several files — that is the whole reason the extension is
on the list. But the reader that unpacked it looked for `.shp` in the archive's TOP LEVEL only, and
nothing else at all, so two ordinary uploads were refused with a message that was not true:

  * **a shapefile inside a folder** — which is what QGIS's "package layers", ArcGIS's export and
    every "download this dataset" button in the world produce — reported *"ZIP file contains no
    .shp file"*, naming exactly the thing the archive did contain;
  * **a zipped GeoPackage, GeoJSON or FlatGeobuf** — zipped for size or because it was emailed —
    reported the same thing, about a format that has no `.shp` in it by definition.

These build real archives and run the real `_resolve_source`, because the bug was entirely about
what `os.listdir` does to a nested path: a stub that answered "here is your shapefile" would have
tested nothing.
"""
import os
import zipfile

import fiona
import pytest
from fiona.crs import CRS

from geodeploy.tasks.vector_ingest import _resolve_source

SHAPE = {"geometry": {"type": "Point", "coordinates": (0.5, 0.5)},
         "properties": {"name": "a place"}}
SCHEMA = {"geometry": "Point", "properties": {"name": "str"}}


def write_dataset(path, driver):
    with fiona.open(str(path), "w", driver=driver, schema=SCHEMA,
                    crs=CRS.from_epsg(4326)) as dst:
        dst.write(SHAPE)
    return str(path)


def zip_up(zip_path, files, prefix=""):
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in files:
            archive.write(path, prefix + os.path.basename(path))
    return str(zip_path)


def shapefile_parts(base):
    return [base + ext for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg")
            if os.path.exists(base + ext)]


@pytest.fixture
def shapefile(tmp_path):
    path = write_dataset(tmp_path / "survey.shp", "ESRI Shapefile")
    return shapefile_parts(os.path.splitext(path)[0])


class TestAShapefileWhereverItSits:

    def test_at_the_top_level(self, tmp_path, shapefile):
        found = _resolve_source(zip_up(tmp_path / "flat.zip", shapefile))
        assert found.endswith(".shp")
        with fiona.open(found) as src:
            assert len(src) == 1

    def test_inside_a_folder(self, tmp_path, shapefile):
        """The reported shape, and the commonest one there is."""
        found = _resolve_source(zip_up(tmp_path / "nested.zip", shapefile, "survey_2024/"))
        assert found.endswith(".shp")
        assert "survey_2024" in found
        with fiona.open(found) as src:
            assert len(src) == 1

    def test_two_folders_deep(self, tmp_path, shapefile):
        found = _resolve_source(zip_up(tmp_path / "deep.zip", shapefile, "data/gis/vector/"))
        assert found.endswith(".shp")

    def test_a_mac_sidecar_folder_is_not_mistaken_for_data(self, tmp_path, shapefile):
        """macOS puts `__MACOSX/._survey.shp` beside everything it compresses, and those dotfiles
        are resource forks, not datasets — opening one is an error with no useful message."""
        with zipfile.ZipFile(tmp_path / "mac.zip", "w") as archive:
            for path in shapefile:
                archive.write(path, "survey/" + os.path.basename(path))
                archive.writestr("__MACOSX/survey/._" + os.path.basename(path), b"\x00\x05\x16\x07")
        found = _resolve_source(str(tmp_path / "mac.zip"))
        assert not os.path.basename(found).startswith("._")
        with fiona.open(found) as src:
            assert len(src) == 1


class TestTheOtherFormatsPeopleZip:

    @pytest.mark.parametrize("name,driver", [
        ("places.gpkg", "GPKG"),
        ("places.geojson", "GeoJSON"),
        ("places.fgb", "FlatGeobuf"),
    ])
    def test_a_single_file_dataset_in_a_zip(self, tmp_path, name, driver):
        path = write_dataset(tmp_path / name, driver)
        found = _resolve_source(zip_up(tmp_path / (name + ".zip"), [path]))
        with fiona.open(found) as src:
            assert len(src) == 1

    def test_a_shapefile_wins_over_anything_else_in_the_same_archive(self, tmp_path, shapefile):
        """A shapefile export often ships a README, a stray GeoJSON preview or a metadata file
        beside it. The shapefile is the dataset; the extras are not."""
        extra = write_dataset(tmp_path / "preview.geojson", "GeoJSON")
        found = _resolve_source(zip_up(tmp_path / "mixed.zip", shapefile + [extra]))
        assert found.endswith(".shp")


class TestWhenThereIsNothingToRead:

    def test_the_error_says_what_it_looked_for_and_what_it_found(self, tmp_path):
        """The old message named `.shp` and nothing else, so an archive of PDFs and an archive of
        GeoPackages produced the same sentence — and only one of them was about a missing
        shapefile."""
        with zipfile.ZipFile(tmp_path / "docs.zip", "w") as archive:
            archive.writestr("report.pdf", b"%PDF-1.4")
            archive.writestr("notes.txt", b"nothing spatial here")
        with pytest.raises(ValueError) as raised:
            _resolve_source(str(tmp_path / "docs.zip"))
        message = str(raised.value)
        assert ".shp" in message and ".gpkg" in message, "it must say what it can read"
        assert ".pdf" in message, "and what it actually found: {0}".format(message)


class TestTheArchiveIsNotTrusted:

    def test_an_entry_naming_a_path_outside_the_extraction_dir_is_dropped(self, tmp_path):
        """Belt and braces, and said plainly: CPython's own `extractall` already strips `..` and a
        leading `/` from member names, so this was not a live vulnerability — putting the old code
        back leaves this test passing. The filter is here because the guarantee is CPython's rather
        than this code's, the cost is four lines, and an upload is the one input where an arbitrary
        file write would be worst."""
        target = tmp_path / "escaped.txt"
        with zipfile.ZipFile(tmp_path / "evil.zip", "w") as archive:
            archive.writestr("../../escaped.txt", b"should never be written")
            archive.writestr("harmless.txt", b"fine")
        with pytest.raises(ValueError):
            _resolve_source(str(tmp_path / "evil.zip"))
        assert not target.exists(), "the traversing entry was written outside the extraction dir"
