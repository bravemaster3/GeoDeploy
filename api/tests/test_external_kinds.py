"""Every external-source kind: what is stored, what the style says, and what the browser fetches.

An external source is a REFERENCE — nothing is ingested — and until now the instance held three
kinds. This is the rest of what a web map can actually draw: WMTS, OGC API - Features, third-party
vector tiles, and a remote PMTiles archive.

THE THING THAT DECIDES THE DESIGN IS CORS. A raster tile is fetched the way the web has fetched
images for twenty years and every tile server is set up for it. GeoJSON, an MVT tile and a PMTiles
byte range are cross-origin XHRs, and a provider who has not set `Access-Control-Allow-Origin`
breaks the layer SILENTLY — an empty map, a console error the reader never sees. So those go
through our own proxy, which is the rule the WFS proxy already established.

WCS is deliberately absent and that is asserted here too: GetCoverage returns a coverage, not map
images, so there is nothing a web map can draw from it. Storing one would publish a layer that
draws nothing.
"""
import pytest

from geodeploy.services import external_sources as ext
from geodeploy.services import portal_generator as generator


class Src:
    """The columns the style and the URL builders read off a source row."""

    def __init__(self, **kw):
        self.id = kw.get("id", 3)
        self.name = kw.get("name", "A source")
        self.source_type = kw["source_type"]
        self.kind = kw.get("kind") or ext.kind_for(self.source_type)
        self.url = kw.get("url", "https://example.org/service")
        self.layer_name = kw.get("layer_name")
        self.source_layer = kw.get("source_layer")
        self.matrix_set = kw.get("matrix_set")
        self.min_zoom = kw.get("min_zoom")
        self.max_zoom = kw.get("max_zoom")
        self.version = kw.get("version")
        self.image_format = kw.get("image_format")
        self.attribution = kw.get("attribution")
        self.geometry_type = kw.get("geometry_type")
        self.bbox = kw.get("bbox")


class TestWhatEachKindDrawsAs:
    @pytest.mark.parametrize("source_type,kind", [
        ("xyz", "raster"), ("wms", "raster"), ("wmts", "raster"),
        ("wfs", "vector"), ("ogcapi", "vector"), ("vectortile", "vector"),
    ])
    def test_the_kind_follows_from_the_type(self, source_type, kind):
        assert ext.kind_for(source_type) == kind

    def test_a_pmtiles_archive_says_which_it_is(self):
        # An archive holds raster OR vector tiles and its header says which, so this is read from
        # the file rather than guessed from the extension.
        assert ext.kind_for("pmtiles", "raster") == "raster"
        assert ext.kind_for("pmtiles", "vector") == "vector"
        assert ext.kind_for("pmtiles") == "vector", "the common case when nothing was read"


class TestWhatTheBrowserFetches:
    def test_a_raster_tile_comes_from_the_provider(self):
        url = ext.tile_url(Src(source_type="xyz", url="https://t.example/{z}/{x}/{y}.png"))
        assert url == "https://t.example/{z}/{x}/{y}.png"

    def test_a_wmts_request_is_built_with_the_matrix_set(self):
        url = ext.tile_url(Src(source_type="wmts", url="https://example.org/wmts",
                               layer_name="topo", matrix_set="EPSG:3857"))
        assert "request=GetTile" in url and "layer=topo" in url
        assert "tilematrixset=EPSG:3857" in url
        # MapLibre's tokens, not WMTS's: the client substitutes these per tile.
        assert "tilematrix={z}" in url and "tilerow={y}" in url and "tilecol={x}" in url

    def test_a_wmts_with_no_matrix_set_uses_the_one_everybody_publishes(self):
        url = ext.tile_url(Src(source_type="wmts", url="https://example.org/wmts",
                               layer_name="topo"))
        assert "tilematrixset=GoogleMapsCompatible" in url

    @pytest.mark.parametrize("source_type", ["vectortile", "pmtiles"])
    def test_the_kinds_that_need_cors_go_through_us(self, source_type):
        # THE POINT OF THE PROXY. Both of these are read as DATA by the browser, so a provider
        # without a CORS policy breaks them silently.
        url = ext.tile_url(Src(id=9, source_type=source_type))
        assert url == "/api/data/sources/9/tiles/{z}/{x}/{y}"

    @pytest.mark.parametrize("source_type", ["wfs", "ogcapi"])
    def test_features_are_proxied_too(self, source_type):
        assert ext.features_url(Src(id=9, source_type=source_type)) \
            == "/api/data/sources/9/features.geojson"

    @pytest.mark.parametrize("source_type", ["xyz", "wms", "wmts", "vectortile", "pmtiles"])
    def test_a_tiled_source_has_no_features_endpoint(self, source_type):
        # A `vectortile` source IS vector, and answering the features route for it would hand the
        # portal an empty feature collection for a layer that has a tile endpoint.
        assert ext.features_url(Src(source_type=source_type)) is None


class TestTemplatesTheProviderNamedDifferently:
    def test_a_restful_wmts_template_is_read_as_tiles(self):
        raw = "https://example.org/wmts/topo/{TileMatrix}/{TileRow}/{TileCol}.png"
        assert ext.is_tile_template(raw)
        assert ext.normalise_template(raw) == "https://example.org/wmts/topo/{z}/{y}/{x}.png"

    def test_a_plain_xyz_template_is_left_alone(self):
        raw = "https://t.example/{z}/{x}/{y}@2x.png"
        assert ext.normalise_template(raw) == raw

    def test_a_service_endpoint_is_not_a_template(self):
        assert not ext.is_tile_template("https://example.org/wmts?service=WMTS")
        assert not ext.is_tile_template("")


class TestOgcApiAddressing:
    """People paste three different things, all reasonable. A source that took one would be a
    source that "does not work" for two thirds of the people who try it."""

    def test_a_landing_page_plus_a_collection(self):
        url = ext.oapif_items_url("https://host/ogc", "roads")
        assert url == "https://host/ogc/collections/roads/items?f=json"

    def test_a_collection_url_already_names_it(self):
        url = ext.oapif_items_url("https://host/ogc/collections/roads", None)
        assert url == "https://host/ogc/collections/roads/items?f=json"

    def test_an_items_url_is_used_as_it_stands(self):
        url = ext.oapif_items_url("https://host/ogc/collections/roads/items", None)
        assert url == "https://host/ogc/collections/roads/items?f=json"

    def test_a_limit_is_appended_without_losing_the_format(self):
        url = ext.oapif_items_url("https://host/ogc", "roads", limit=5)
        assert "f=json" in url and "limit=5" in url

    def test_an_existing_query_is_kept(self):
        url = ext.oapif_items_url("https://host/ogc/collections/roads/items?filter=x", None)
        assert "filter=x" in url and "f=json" in url and url.count("?") == 1

    @pytest.mark.parametrize("url,expected", [
        ("https://host/ogc/collections/roads", "roads"),
        ("https://host/ogc/collections/roads/items", "roads"),
        ("https://host/ogc/collections/roads/items/", "roads"),
        ("https://host/ogc", None),
    ])
    def test_the_collection_is_recognised_in_a_url(self, url, expected):
        assert ext.oapif_collection_of(url) == expected


class TestWhatTheStyleSays:
    def _style_for(self, src, style=None):
        configs = [{"layer_id": src.id, "layer_type": "external",
                    "visible": True, "opacity": 1.0, "style": style or {}}]
        return generator.generate_style(configs, [], [], external_sources=[src])

    def test_a_vector_tile_source_is_a_vector_source_with_its_layer(self):
        src = Src(id=4, source_type="vectortile", url="https://t.example/{z}/{x}/{y}.pbf",
                  source_layer="roads", geometry_type="line")
        style = self._style_for(src)
        source = style["sources"]["ext_4"]
        assert source["type"] == "vector"
        assert source["tiles"] == ["/api/data/sources/4/tiles/{z}/{x}/{y}"]
        layer = [ml for ml in style["layers"] if ml["id"] == "external-4"][0]
        # WITHOUT THIS THE LAYER DRAWS NOTHING, and MapLibre reports no error for it.
        assert layer["source-layer"] == "roads"
        assert layer["type"] == "line"

    def test_a_pmtiles_archive_is_drawn_as_ordinary_tiles(self):
        # Not `pmtiles://`: the archive is read by US, so the portal needs no PMTiles library and
        # the provider needs no CORS policy.
        src = Src(id=5, source_type="pmtiles", url="https://files.example/a.pmtiles",
                  source_layer="places", geometry_type="point", min_zoom=2, max_zoom=12)
        style = self._style_for(src)
        source = style["sources"]["ext_5"]
        assert source["type"] == "vector"
        assert source["tiles"] == ["/api/data/sources/5/tiles/{z}/{x}/{y}"]
        assert (source["minzoom"], source["maxzoom"]) == (2, 12)
        assert [ml for ml in style["layers"] if ml["id"] == "external-5"][0]["type"] == "circle"

    def test_a_raster_pmtiles_archive_is_a_raster_source(self):
        src = Src(id=6, source_type="pmtiles", kind="raster",
                  url="https://files.example/a.pmtiles")
        style = self._style_for(src)
        assert style["sources"]["ext_6"]["type"] == "raster"
        assert [ml for ml in style["layers"] if ml["id"] == "external-6"][0]["type"] == "raster"

    def test_an_ogcapi_source_is_drawn_from_the_proxy(self):
        src = Src(id=7, source_type="ogcapi", url="https://host/ogc", layer_name="roads",
                  geometry_type="polygon")
        style = self._style_for(src)
        assert style["sources"]["ext_7"] == {"type": "geojson",
                                             "data": "/api/data/sources/7/features.geojson"}

    def test_a_zoom_range_is_only_written_when_the_provider_stated_one(self):
        # A source with no range draws at every zoom, which is what a bare XYZ template means.
        # Inventing one would blank the layer outside it.
        src = Src(id=8, source_type="xyz", url="https://t.example/{z}/{x}/{y}.png")
        source = self._style_for(src)["sources"]["ext_8"]
        assert "minzoom" not in source and "maxzoom" not in source

    def test_the_attribution_reaches_the_source(self):
        # The provider's credit is the condition of using their service.
        src = Src(id=9, source_type="vectortile", url="https://t.example/{z}/{x}/{y}.pbf",
                  source_layer="roads", attribution="© Somebody")
        assert self._style_for(src)["sources"]["ext_9"]["attribution"] == "© Somebody"


class TestWcsIsNotADisplayService:
    def test_it_is_not_a_source_type(self):
        assert "wcs" not in ext.SOURCE_TYPES

    def test_and_the_reason_is_written_down_where_the_api_can_say_it(self):
        assert "GetCoverage" in ext.IMPORT_ONLY_HINT
        assert "WMS" in ext.IMPORT_ONLY_HINT, "the way forward has to be in the message"

    def test_the_create_schema_refuses_it(self):
        from pydantic import ValidationError

        from geodeploy.schemas import ExternalSourceCreate
        with pytest.raises(ValidationError):
            ExternalSourceCreate(name="c", source_type="wcs", url="https://example.org/wcs")


class TestTheCreateSchema:
    @pytest.mark.parametrize("source_type", list(ext.SOURCE_TYPES))
    def test_every_kind_the_service_knows_is_accepted(self, source_type):
        from geodeploy.schemas import ExternalSourceCreate
        # …or the service and the API disagree about what exists, which shows up as a 422 on a kind
        # the rest of the code fully supports.
        body = ExternalSourceCreate(name="x", source_type=source_type,
                                    url="https://example.org/x")
        assert body.source_type == source_type

    def test_the_fields_the_new_kinds_need_are_there(self):
        from geodeploy.schemas import ExternalSourceCreate
        body = ExternalSourceCreate(name="x", source_type="vectortile",
                                    url="https://t.example/{z}/{x}/{y}.pbf",
                                    source_layer="roads", matrix_set="EPSG:3857")
        assert body.source_layer == "roads" and body.matrix_set == "EPSG:3857"


class TestTheColumnsExist:
    def test_the_migration_adds_every_new_column(self):
        # `create_all` never adds a column to a table that already exists, so a new column breaks
        # every EXISTING install while working perfectly on a fresh one.
        from geodeploy.schema_migrations import PG_MIGRATIONS
        joined = " ".join(PG_MIGRATIONS)
        for column in ("source_layer", "matrix_set", "min_zoom", "max_zoom"):
            assert f"external_sources ADD COLUMN IF NOT EXISTS {column}" in joined, column

    def test_the_model_has_them_too(self):
        from geodeploy.models import ExternalSource
        for column in ("source_layer", "matrix_set", "min_zoom", "max_zoom"):
            assert hasattr(ExternalSource, column), column



# ── The probes, against archives and services built here ────────────────────────────────────────

import struct


def _pmtiles_header(tile_type: int, min_zoom: int = 0, max_zoom: int = 14,
                    metadata: bytes = b"{}") -> bytes:
    """A PMTiles v3 header, built by hand, with the metadata JSON right behind it.

    Hand-built because the thing under test is how the HEADER is read: a real archive would prove
    it too (and does, in the manual check that found the bug this pins), but a test may not depend
    on somebody else's bucket being up.
    """
    meta_offset = 127
    head = bytearray(meta_offset)
    head[0:7] = b"PMTiles"
    head[7] = 3
    struct.pack_into("<11Q", head, 8,
                     0, 0,                        # root dir offset/length
                     meta_offset, len(metadata),  # metadata offset/length
                     0, 0,                        # leaf offset/length
                     0, 0,                        # data offset/length
                     0, 0, 0)                     # addressed / entries / contents
    struct.pack_into("<6B", head, 96,
                     1,           # clustered
                     0,           # internal compression: none
                     0,           # tile compression: none
                     tile_type,
                     min_zoom, max_zoom)
    struct.pack_into("<4i", head, 102,
                     -1800000000, -850511287, 1800000000, 850511287)
    return bytes(head) + metadata


class TestProbingAPmtilesArchive:
    @pytest.mark.asyncio
    async def test_a_vector_archive_is_not_registered_as_a_raster_one(self, monkeypatch):
        """THE BUG A REAL ARCHIVE CAUGHT.

        The kind was decided by sniffing the media type for "mvt" — and the reader spells an MVT
        archive `application/vnd.mapbox-vector-tile`, which ends in "tile". So EVERY vector archive
        was registered as raster and published as a raster layer: vector tiles fetched into an
        image source, drawing nothing, with no error. Read the tile TYPE, not the type's name.
        """
        from geodeploy.services import pmtiles_reader

        raw = _pmtiles_header(pmtiles_reader.TILETYPE_MVT,
                              metadata=b'{"vector_layers":[{"id":"boundaries"}]}')

        async def fake(url, start, length):
            return raw[start:start + length]

        monkeypatch.setattr(ext, "_range", fake)
        info = await ext.probe_pmtiles("https://files.example/a.pmtiles", None)
        assert info["kind"] == "vector"
        # …and the layer inside it is read from the archive's own metadata, because a style that
        # names no `source-layer` draws nothing at all.
        assert info["source_layer"] == "boundaries"
        assert (info["min_zoom"], info["max_zoom"]) == (0, 14)
        assert info["bbox"][0] == pytest.approx(-180.0)

    @pytest.mark.asyncio
    async def test_a_raster_archive_needs_no_layer_name(self, monkeypatch):
        from geodeploy.services import pmtiles_reader

        raw = _pmtiles_header(pmtiles_reader.TILETYPE_PNG)

        async def fake(url, start, length):
            return raw[start:start + length]

        monkeypatch.setattr(ext, "_range", fake)
        info = await ext.probe_pmtiles("https://files.example/a.pmtiles", None)
        assert info["kind"] == "raster"
        assert info["source_layer"] is None

    @pytest.mark.asyncio
    async def test_a_vector_archive_that_names_nothing_is_refused(self, monkeypatch):
        # Storing it would publish a layer that draws nothing, silently. Better to say so now.
        from geodeploy.services import pmtiles_reader

        raw = _pmtiles_header(pmtiles_reader.TILETYPE_MVT, metadata=b"{}")

        async def fake(url, start, length):
            return raw[start:start + length]

        monkeypatch.setattr(ext, "_range", fake)
        with pytest.raises(ValueError, match="layer"):
            await ext.probe_pmtiles("https://files.example/a.pmtiles", None)

    @pytest.mark.asyncio
    async def test_something_that_is_not_an_archive_says_so(self, monkeypatch):
        async def fake(url, start, length):
            return b"<!doctype html><html>404</html>" + b"\0" * 200

        monkeypatch.setattr(ext, "_range", fake)
        with pytest.raises(ValueError, match="PMTiles"):
            await ext.probe_pmtiles("https://files.example/index.html", None)


class TestProbingVectorTiles:
    @pytest.mark.asyncio
    async def test_a_template_with_a_named_layer_needs_no_tilejson(self, monkeypatch):
        async def no_tilejson(url):
            raise ValueError("404")

        monkeypatch.setattr(ext, "_fetch_tilejson", no_tilejson)
        info = await ext.probe_vector_tiles("https://t.example/{z}/{x}/{y}.pbf", "roads")
        assert info["tiles"] == "https://t.example/{z}/{x}/{y}.pbf"
        assert info["source_layer"] == "roads"

    @pytest.mark.asyncio
    async def test_a_template_with_no_layer_and_no_tilejson_is_refused(self, monkeypatch):
        async def no_tilejson(url):
            raise ValueError("404")

        monkeypatch.setattr(ext, "_fetch_tilejson", no_tilejson)
        with pytest.raises(ValueError, match="layer"):
            await ext.probe_vector_tiles("https://t.example/{z}/{x}/{y}.pbf", None)

    @pytest.mark.asyncio
    async def test_a_tilejson_supplies_everything(self, monkeypatch):
        async def tilejson(url):
            return {"tiles": ["https://t.example/{z}/{x}/{y}.pbf"],
                    "vector_layers": [{"id": "water"}, {"id": "roads"}],
                    "minzoom": 2, "maxzoom": 12, "bounds": [-10, -20, 30, 40]}

        monkeypatch.setattr(ext, "_fetch_tilejson", tilejson)
        info = await ext.probe_vector_tiles("https://t.example/tiles.json", None)
        assert info["tiles"] == "https://t.example/{z}/{x}/{y}.pbf"
        assert info["source_layer"] == "water", "the first layer, when the user named none"
        assert (info["min_zoom"], info["max_zoom"]) == (2, 12)
        assert info["bbox"] == [-10.0, -20.0, 30.0, 40.0]

    @pytest.mark.asyncio
    async def test_the_users_own_layer_choice_wins_over_the_tilejson(self, monkeypatch):
        async def tilejson(url):
            return {"tiles": ["https://t.example/{z}/{x}/{y}.pbf"],
                    "vector_layers": [{"id": "water"}, {"id": "roads"}]}

        monkeypatch.setattr(ext, "_fetch_tilejson", tilejson)
        info = await ext.probe_vector_tiles("https://t.example/tiles.json", "roads")
        assert info["source_layer"] == "roads"

    @pytest.mark.asyncio
    async def test_a_url_that_is_neither_is_refused(self, monkeypatch):
        async def tilejson(url):
            return {"name": "not a tile set"}

        monkeypatch.setattr(ext, "_fetch_tilejson", tilejson)
        with pytest.raises(ValueError, match="template"):
            await ext.probe_vector_tiles("https://t.example/whatever", "roads")


class TestFetchingAVectorTile:
    @pytest.mark.asyncio
    async def test_a_missing_tile_is_nothing_rather_than_an_error(self, monkeypatch):
        """A tile server answers a missing tile with 404 or 204 depending on who wrote it, and
        both mean the same thing to a map: nothing here. Neither may become a 502."""

        class Resp:
            def __init__(self, code):
                self.status_code, self.content, self.headers = code, b"", {}

        class Client:
            def __init__(self, code):
                self._code = code

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                return Resp(self._code)

        class Src:
            source_type = "vectortile"
            url = "https://t.example/{z}/{x}/{y}.pbf"

        for code in (404, 204):
            monkeypatch.setattr(ext.httpx, "AsyncClient", lambda **kw: Client(code))
            assert await ext.fetch_vector_tile(Src(), 1, 1, 1) is None


class TestMixedContent:
    """An `http://` tile inside an `https://` portal is blocked by the browser, silently.

    REPORTED AS two Google tile sources added side by side: the `https` one drew, the `http` one
    did not, and both worked in QGIS — which is a desktop application with no such rule. Nothing
    appears on the map and nothing appears in any log the operator reads; the error is in the
    visitor's console.

    So it is resolved when the source is ADDED, while there is somebody to tell.

    THE REPORTED URL IS THE HARD CASE, and the first version of this check got it wrong:
    `http://www.google.cn/maps/vt?...` answers 302 — to `http://www.google.com/...`, on http,
    whether you ask it over http OR https. Following redirects and asking only for "200 with bytes
    in it" therefore called the https form secure, and the source would have been stored at an
    address that still cannot draw. A chain that touches http anywhere is not https.
    """

    @staticmethod
    def _client(monkeypatch, handler):
        """Install a fake httpx whose `get` is `handler(url) -> Resp`."""
        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                return handler(url)

        monkeypatch.setattr(ext.httpx, "AsyncClient", lambda **kw: Client())

    @staticmethod
    def _resp(final, status=200, content=b"\x89PNG", hops=()):
        """A response that ENDED at `final`, having passed through `hops` on the way."""
        class Url:
            def __init__(self, raw):
                self.raw = raw
                self.scheme = raw.split(":", 1)[0]

            def __str__(self):
                return self.raw

        class Hop:
            def __init__(self, raw):
                self.url = Url(raw)

        class Resp:
            pass

        r = Resp()
        r.status_code, r.content = status, content
        r.url = Url(final)
        r.history = [Hop(h) for h in hops]
        return r

    def test_the_hint_says_what_is_wrong_and_what_to_do(self):
        assert "https" in ext.MIXED_CONTENT_HINT
        assert "never draw" in ext.MIXED_CONTENT_HINT
        # …and it now accounts for the redirect, which is the case that was reported.
        assert "redirects" in ext.MIXED_CONTENT_HINT

    @pytest.mark.asyncio
    async def test_an_https_url_is_never_probed(self, monkeypatch):
        # The question is only ever asked about an http address.
        called = []
        monkeypatch.setattr(ext.httpx, "AsyncClient", lambda **kw: called.append(1))
        assert await ext.secure_alternative("https://t.example/{z}/{x}/{y}.png") is None
        assert not called

    @pytest.mark.asyncio
    async def test_a_template_is_probed_with_a_real_tile(self, monkeypatch):
        """`{z}/{x}/{y}` is not a URL. Fetching the template verbatim asks the provider for a file
        called `{z}`, which many answer with a 404 — so the source would be refused for being
        unreachable when it is merely a template."""
        asked = []
        self._client(monkeypatch, lambda url: (asked.append(url), self._resp(url))[1])
        got = await ext.secure_alternative("http://t.example/{z}/{x}/{y}.png")
        assert got == "https://t.example/{z}/{x}/{y}.png"
        assert "https://t.example/0/0/0.png" in asked

    @pytest.mark.asyncio
    async def test_a_provider_that_does_not_answer_over_https_says_so(self, monkeypatch):
        def handler(url):
            raise OSError("no route to host")

        self._client(monkeypatch, handler)
        assert await ext.secure_alternative("http://t.example/{z}/{x}/{y}.png") is None

    @pytest.mark.asyncio
    async def test_an_empty_body_is_not_an_answer(self, monkeypatch):
        # A 200 with nothing in it is a proxy or a login page, not a tile.
        self._client(monkeypatch, lambda url: self._resp(url, content=b""))
        assert await ext.secure_alternative("http://t.example/{z}/{x}/{y}.png") is None

    @pytest.mark.asyncio
    async def test_a_chain_that_ends_on_http_is_not_https(self, monkeypatch):
        """THE HOLE THE FIRST VERSION HAD. Starting secure and finishing insecure is blocked by a
        browser exactly as an `http` URL is, so a 200 at the end of that chain proves nothing."""
        self._client(monkeypatch, lambda url: self._resp(
            "http://elsewhere.example/0/0/0.png", hops=[url]))
        assert await ext._answers_securely("https://t.example/{z}/{x}/{y}.png") is False

    @pytest.mark.asyncio
    async def test_a_chain_that_stays_on_https_is_fine(self, monkeypatch):
        # A CDN redirect between two https hosts is ordinary and must not be refused.
        self._client(monkeypatch, lambda url: self._resp(
            "https://cdn.example/0/0/0.png", hops=[url]))
        assert await ext._answers_securely("https://t.example/{z}/{x}/{y}.png") is True

    @pytest.mark.asyncio
    async def test_the_reported_google_url_is_moved_to_the_host_that_serves_it(self, monkeypatch):
        """The reported case end to end, with the real shape of Google's answer.

        `www.google.cn` 302s to `http://www.google.com` — the same path, the same query, a
        different host — however you ask it. So the same-address upgrade fails and the MOVED
        address is the one that works.
        """
        def handler(url):
            if "google.cn" in url:
                return self._resp(url.replace("https://", "http://")
                                     .replace("google.cn", "google.com"), hops=[url])
            return self._resp(url)

        self._client(monkeypatch, handler)
        template = "http://www.google.cn/maps/vt?lyrs=s@189&gl=cn&x={x}&y={y}&z={z}"
        assert await ext.secure_alternative(template) == (
            "https://www.google.com/maps/vt?lyrs=s@189&gl=cn&x={x}&y={y}&z={z}")

    @pytest.mark.asyncio
    async def test_a_redirect_that_changes_the_path_is_not_followed(self, monkeypatch):
        """A consent wall, a login page or a per-tile CDN URL is not a template.

        Rewriting the source to one would store an address that works for tile 0/0/0 and for
        nothing else, so only a pure change of HOST is treated as a move.
        """
        def handler(url):
            if "t.example" in url:
                return self._resp("http://t.example/please-log-in", hops=[url])
            return self._resp(url)

        self._client(monkeypatch, handler)
        assert await ext.secure_alternative("http://t.example/{z}/{x}/{y}.png") is None

    @pytest.mark.asyncio
    async def test_the_same_address_wins_over_the_one_it_redirects_to(self, monkeypatch):
        """Order matters: a provider that serves https at its own address keeps that address."""
        def handler(url):
            if url.startswith("https://t.example"):
                return self._resp(url)
            return self._resp(url.replace("t.example", "other.example"), hops=[url])

        self._client(monkeypatch, handler)
        assert await ext.secure_alternative(
            "http://t.example/{z}/{x}/{y}.png") == "https://t.example/{z}/{x}/{y}.png"


class TestLineOpacity:
    """A line's own opacity, which QGIS keeps beside the colour and GeoDeploy carries as a number.

    The twin of `marker_opacity`, added for the same reason a polygon's fill needed one: QGIS has
    TWO opacities — the symbol's, and the alpha inside the colour picker — and `QColor.name()`
    drops the second. A boundary drawn at 40% published fully opaque.
    """

    def test_it_multiplies_with_the_layers_own(self):
        from geodeploy.services import symbology as sym
        assert sym.line_opacity({"line_opacity": 0.5}, 0.5) == 0.25

    def test_a_style_that_says_nothing_leaves_the_layer_alone(self):
        from geodeploy.services import symbology as sym
        assert sym.line_opacity({}, 0.8) == 0.8

    def test_nonsense_is_ignored_rather_than_drawn(self):
        from geodeploy.services import symbology as sym
        assert sym.line_opacity({"line_opacity": "very"}, 0.8) == 0.8
        assert sym.line_opacity({"line_opacity": 5}, 1.0) == 1.0
        assert sym.line_opacity({"line_opacity": -2}, 1.0) == 0.0

    def test_the_line_paint_honours_it(self):
        class Line:
            id = 3
            name = "Boundary"
            geometry_type = "LineString"
            storage_backend = "postgis"
            schema_name = "gd"
            table_name = "b"
            geometry_column = "geom"
            uid = "u3"
            s3_key = None
            pmtiles_key = None
            bbox = None
            default_style = {}
            tile_status = "ready"
            cluster_points = False

        built = generator._vector_layers("vector_3", Line(), {
            "layer_id": 3, "layer_type": "vector", "opacity": 1.0,
            "style": {"color": "#1f4fd8", "line_width": 2, "line_opacity": 0.4}}, {})
        line = [ml for ml in built if ml["type"] == "line"][0]
        assert line["paint"]["line-opacity"] == 0.4

    def test_and_it_is_a_per_class_shape_key(self):
        # A class whose only difference is its transparency must become its own render layer,
        # exactly like one that differs by dash or width.
        from geodeploy.services import symbology as sym
        assert "line_opacity" in sym.CLASS_SHAPE_KEYS
