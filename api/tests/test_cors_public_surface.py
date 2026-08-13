"""The public read surface must be cross-origin readable — pinned as a pattern test.

This exists because of a real, silent outage. Public layer URLs moved from the integer id to the
stable `uid` (2026-07-29), but `main._PUBLIC_CORS` still matched `data/vector/\\d+/…`. Digits stop
matching a hex uid, so the middleware quietly stopped adding `Access-Control-Allow-Origin` to the
very endpoints it exists for. Nothing errored: the server answered `206 Partial Content` perfectly
and the BROWSER threw the response away, so PMTiles "worked before and not anymore" with no
server-side symptom at all.

The lesson generalised: whenever the SHAPE of a public URL changes, this pattern has to change with
it. These cases are the contract.
"""
import re

import pytest

from geodeploy.main import _PUBLIC_CORS

UID = "488c2c7f55d7"          # a real-shaped uid: hex, letters AND digits


@pytest.mark.parametrize("path", [
    # The regression: uid-addressed vector artifacts.
    f"/api/data/vector/{UID}/pmtiles",
    f"/api/data/vector/{UID}/features.geojson",
    f"/api/data/vector/{UID}/features.arrow",
    f"/api/data/vector/{UID}/tilejson",
    f"/api/data/vector/{UID}/identify",
    f"/api/data/vector/{UID}/parquet/manifest.json",
    f"/api/data/vector/{UID}/parquet/__cell=137/data_0.parquet",
    # The legend (2026-08-13): a browser-side renderer draws swatches from it cross-origin.
    f"/api/data/vector/{UID}/legend",
    f"/api/data/raster/{UID}/legend",
    # Legacy integer ids must keep working — links shared before uids exist in the wild.
    "/api/data/vector/12/pmtiles",
    "/api/data/raster/12/cog",
    f"/api/data/raster/{UID}/cog",
    f"/api/data/raster/{UID}/tilejson",
    # The anonymous instance index (2026-08-12) — a browse client reads it cross-origin.
    "/api/public",
    "/api/public/portals",
    # Per-layer downloads: queued, polled and fetched from a browser like any other artifact.
    f"/api/data/vector/{UID}/export",
    f"/api/data/vector/{UID}/export-status/9f1c2b3a-4d5e-6f70-8192-a3b4c5d6e7f8",
    f"/api/data/vector/{UID}/export-download/9f1c2b3a-4d5e-6f70-8192-a3b4c5d6e7f8",
    f"/api/data/raster/{UID}/export",
    "/api/data/raster/12/export-status/9f1c2b3a-4d5e-6f70-8192-a3b4c5d6e7f8",
    # Discovery + the standards surface.
    "/api/stac",
    "/api/stac/collections/vectors/items/vector-" + UID,
    "/api/ogc",
    "/api/ogc/collections",
    f"/api/ogc/collections/vector-{UID}/items",
])
def test_public_surface_is_cors_matched(path):
    assert _PUBLIC_CORS.match(path), (
        f"{path} is a PUBLIC artifact but does not match _PUBLIC_CORS — browsers will reject it "
        "even though the server answers correctly")


@pytest.mark.parametrize("path", [
    f"/api/data/vector/{UID}/links",      # authed: the share-links panel
    "/api/data/vector",                   # authed list
    "/api/portals/1",
    "/api/admin/health",
    "/api/auth/login",
    "/api/backups/settings",
    "/api/ogcx",                          # must not leak past the `ogc` alternative
    "/api/publicx",                       # nor past `public`
    "/api/admin/public-index",            # the TOGGLE is admin-only, not part of the public surface
])
def test_private_surface_is_not_wildcard_cors(path):
    """Wildcard CORS on an authenticated route would let any origin read a logged-in user's data.
    The credentialed CORSMiddleware governs these; this pattern must not touch them."""
    assert not _PUBLIC_CORS.match(path), f"{path} must NOT get wildcard CORS"


def test_pattern_has_no_digit_only_id_segments():
    """Guards the exact mistake: a `\\d+` id segment silently excludes every uid-addressed URL."""
    assert r"\d+" not in _PUBLIC_CORS.pattern, (
        "an id segment is digits-only again — uid-addressed public URLs would stop being "
        "CORS-enabled without any server-side error")
