"""HEAD works on every GET route (`main._HeadAsGet`).

Found by trying to open a GeoDeploy layer in QGIS. `ogrinfo /vsicurl/…/pmtiles` answered "not
recognized as being in a supported file format", which reads like a corrupt archive — the file was
perfect (`PMTiles` v3 magic, byte ranges served, 206 on a ranged GET). **`/vsicurl/` probes a URL
with a HEAD request first**, FastAPI's `APIRoute` does not add HEAD to a GET route, and every
endpoint answered 405. So GDAL gave up before reading a byte, and with it QGIS, ogr2ogr and
everything else built on GDAL — including the `/cog` path this project's own documentation
recommends for QGIS.

The bar these tests set: a HEAD answers with the GET's STATUS and HEADERS and an empty body.
"""
import pytest


class TestHeadIsAnswered:
    @pytest.mark.parametrize("path", [
        "/health",
        "/api/setup/status",
        "/api/public",
        "/api/ogc",
        "/api/stac",
    ])
    async def test_head_is_not_405(self, client, path):
        r = await client.head(path)
        assert r.status_code != 405, f"HEAD {path} answered 405 — /vsicurl/ cannot open this"
        assert r.status_code < 500

    async def test_head_matches_the_get_status(self, client):
        assert (await client.head("/health")).status_code == (
            await client.get("/health")).status_code

    async def test_head_returns_no_body(self, client):
        """The whole point of HEAD. The headers still describe what a GET would return."""
        r = await client.head("/health")
        assert r.content == b""

    async def test_the_headers_survive(self, client):
        """A probe reads Content-Type (and, on a real artifact, Content-Length and Accept-Ranges)
        from the HEAD — dropping them would leave GDAL as stuck as the 405 did."""
        head = await client.head("/health")
        get = await client.get("/health")
        assert head.headers.get("content-type") == get.headers.get("content-type")

    async def test_a_missing_route_still_404s(self, client):
        """Answering HEAD everywhere must not turn unknown paths into successes."""
        assert (await client.head("/api/no-such-endpoint")).status_code == 404

    async def test_get_is_unaffected(self, client):
        r = await client.get("/health")
        assert r.status_code == 200 and r.content
