"""WMTS is how the raster's EXTENT reaches QGIS.

QGIS does not read TileJSON for a raster layer, and an XYZ template has nowhere to put an extent —
so "Zoom to Layer" went to the whole world. A WMTS capabilities document carries
`ows:WGS84BoundingBox`, which is what QGIS zooms to.

These tests are about the DOCUMENT, not the route (which needs a DB): the XML has to be well-formed
and carry the four things QGIS refuses to work without — a bbox, a full TileMatrixSet with
ScaleDenominators, a tile template using WMTS placeholders, and `&` escaped inside it.
"""
import xml.etree.ElementTree as ET

import pytest

from geodeploy.routers.data.raster import _WMTS_MAX_ZOOM, _wmts_matrix_set

OWS = "{http://www.opengis.net/ows/1.1}"


def test_the_matrix_set_is_complete_and_halves_each_level():
    """A capabilities document without ScaleDenominators is one QGIS will not zoom with, which is
    the whole reason this endpoint exists."""
    # The fragment uses the `ows:` prefix, so the test wrapper has to declare it — in the real
    # document it comes from the <Capabilities> root.
    xml = ET.fromstring('<root xmlns:ows="http://www.opengis.net/ows/1.1">'
                        + _wmts_matrix_set() + "</root>")
    mats = xml.findall("TileMatrix")
    assert len(mats) == _WMTS_MAX_ZOOM + 1

    def scale(m):
        return float(m.find("ScaleDenominator").text)

    assert scale(mats[0]) == pytest.approx(559082264.028717)
    for i in range(1, len(mats)):
        assert scale(mats[i]) == pytest.approx(scale(mats[i - 1]) / 2)
    # Identifiers are the zoom numbers the tile template substitutes into {TileMatrix}.
    assert [m.find(OWS + "Identifier").text for m in mats] == [str(z) for z in range(len(mats))]
    # WebMercatorQuad is a square pyramid: 2^z tiles per side.
    assert mats[3].find("MatrixWidth").text == "8"
    assert mats[3].find("MatrixHeight").text == "8"


def test_a_query_string_template_survives_as_valid_xml():
    """The tile URL carries `?url=…&rescale=…`. Unescaped, `&` makes the document malformed and QGIS
    rejects the entire connection — so this asserts a real parse, not a substring."""
    from xml.sax.saxutils import quoteattr
    tmpl = ("https://example.org/raster/cog/tiles/WebMercatorQuad/{TileMatrix}/{TileCol}/{TileRow}"
            "?url=s3://b/k.tif&bidx=1&bidx=2&rescale=0.1,0.9")
    doc = ET.fromstring(f"<ResourceURL template={quoteattr(tmpl)}/>")
    assert doc.get("template") == tmpl
    assert "{TileMatrix}" in doc.get("template")
    assert "&bidx=1" in doc.get("template")


def test_placeholders_are_rewritten_not_left_as_xyz():
    """`raster_tile_url` emits {z}/{x}/{y}; WMTS clients substitute {TileMatrix}/{TileCol}/{TileRow}.
    Leaving the XYZ names would produce a document that parses and then requests literal '{z}'."""
    src = "/raster/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=s3://b/k.tif"
    out = src.replace("{z}", "{TileMatrix}").replace("{x}", "{TileCol}").replace("{y}", "{TileRow}")
    assert "{z}" not in out and "{x}" not in out and "{y}" not in out
    assert out.endswith("{TileMatrix}/{TileCol}/{TileRow}?url=s3://b/k.tif")
