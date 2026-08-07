"""A share link is either about ONE layer or about the whole service, and it must say which.

Every link here is copied out of the UI and pasted into someone else's tool, so the scope of the URL
is the thing the user is really choosing. Two of the vector links are service-wide; the rest address
a single layer. They sat adjacent with near-identical titles ("— this layer" / "— service
endpoint"), which is how a service URL gets copied in the belief that it is one dataset.

The service scope is NOT a data leak: `/api/ogc` lists only layers explicitly shared as public
(`ogcapi._public_layers` filters `is_public`, which `common.apply_sharing` derives from the
visibility axis). It is a surprise, not a disclosure — so this pins the labelling, not the filter.
"""
from geodeploy.services.share_links import public_ref, raster_links, vector_links


class _Layer:
    """Enough of a layer for the URL builders. `storage_backend='postgis'` picks the branch that
    emits the tile links; the GeoParquet branch is covered by its own module's tests."""
    id = 7
    uid = "abc123"
    name = "Test layer"
    schema_name = "gd"
    table_name = "t7"
    storage_backend = "postgis"
    s3_key = "rasters/1/x/y.tif"
    band_count = 1
    abstract = None
    attribution = None
    keywords = None
    license = None


BASE = "https://example.org"


def _by_key(links):
    return {l["id"]: l for l in links}


def test_the_service_link_is_the_only_service_wide_vector_link():
    links = _by_key(vector_links(_Layer(), BASE))
    ref = public_ref(_Layer())

    assert links["ogc-service"]["url"] == f"{BASE}/api/ogc"
    assert ref not in links["ogc-service"]["url"], "the service URL must not look layer-scoped"

    # Everything else that exists for a vector layer addresses THAT layer.
    for key in ("ogc-features", "ogc-items", "stac"):
        assert ref in links[key]["url"], f"{key} should be scoped to one layer"


def test_the_service_link_says_it_is_not_just_this_layer():
    """The label is the fix — a user reading only the title must not think it is one dataset."""
    svc = _by_key(vector_links(_Layer(), BASE))["ogc-service"]
    blurb = (svc["label"] + " " + svc.get("hint", "")).lower()
    assert "all" in blurb or "every" in blurb
    assert "not just this layer" in blurb


def test_raster_links_are_all_layer_scoped():
    """Rasters have no service-wide endpoint, so every link must carry the ref — a raster link that
    did not would be addressing someone else's data."""
    links = raster_links(_Layer(), BASE)
    ref = public_ref(_Layer())
    for l in links:
        assert ref in l["url"] or _Layer.s3_key in l["url"], f"{l['id']} is not layer-scoped"


def test_qgis_is_pointed_at_wmts_and_not_at_xyz():
    """XYZ carries no extent, so 'Zoom to Layer' fails there. WMTS is the QGIS answer and the hints
    must not contradict that."""
    links = _by_key(raster_links(_Layer(), BASE))
    assert links["wmts"]["url"] == f"{BASE}/api/data/raster/{public_ref(_Layer())}/wmts"
    assert "QGIS" in links["wmts"]["tools"]
    assert "extent" in links["xyz"]["hint"].lower()
