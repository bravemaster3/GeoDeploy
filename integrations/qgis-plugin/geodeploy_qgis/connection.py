"""Talking to an instance — with or without an account.

**Anonymous is the default path, not a fallback.** A GeoDeploy instance publishes an index of what
it shares (`GET /api/public`), so someone can paste a URL and see the public portals and layers
before signing in to anything. A token then reveals whatever that token is allowed to see. Both
paths return rows of the same shape, so nothing downstream has to care which one it got.

The credential comes from the vendored client's own config, which means **a `geodeploy login` at a
shell is already a login here** — one place where instances and tokens live, not two.
"""
from __future__ import annotations

import os
import sys

# The client is vendored (a plugin cannot pip-install into someone's QGIS). Put it on the path
# before importing, and prefer the vendored copy over any `geodeploy` the user happens to have
# installed, so the plugin's behaviour does not depend on their site-packages.
_VENDOR = os.path.join(os.path.dirname(__file__), "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from geodeploy import Client                      # noqa: E402
from geodeploy.config import Config, load_credential, normalize_url  # noqa: E402
from geodeploy.errors import GeoDeployError       # noqa: E402


#: The only schemes this plugin will open. `normalize_url` already restricts the instance ORIGIN,
#: but that is not where the risk is: `fetch_json`/`fetch_text` are handed URLs that came BACK from
#: the server — a layer's `links`, a portal's `style.json` sources — and a compromised or hostile
#: instance could answer with `file:///etc/passwd` and have the plugin read it. Checking at the point
#: of opening covers every caller at once, including ones written later.
_ALLOWED_SCHEMES = ("http", "https")


def http_url(url: str) -> str:
    """`url` if it is http(s), else raise. The guard in front of every open in this module."""
    scheme = str(url or "").split("://", 1)[0].lower() if "://" in str(url or "") else ""
    if scheme not in _ALLOWED_SCHEMES:
        raise GeoDeployError(
            "Refusing to open {0!r}: only http:// and https:// URLs are followed.".format(url))
    return url


class Instance:
    """One connection. `token` may be None — that is a supported way to use this, not an error."""

    def __init__(self, url: str, token: str | None = None):
        self.url = normalize_url(url)
        self.token = token or None
        self.client = Client(self.url, token=self.token)
        # url -> parsed document, or the GeoDeployError it failed with. Per CONNECTION, so
        # reconnecting is the way to drop it — these describe layers, and a layer's description
        # does not change while you are looking at it. See `fetch_json`.
        self._doc_cache: dict = {}

    # -- discovery ---------------------------------------------------------------------------------

    def layers(self) -> list[dict]:
        """Every layer this connection may see, newest listing first.

        With a token: the authenticated list. Without: the public index, which groups layers by how
        they are stored — flattened here so the caller sees one list either way. Each row carries
        `_base` so a URL can be built from it later without threading the instance through.
        """
        rows: list[dict] = []
        if self.token:
            for row in self.client.layers.list() or []:
                rows.append(dict(row, _base=self.url))
            return rows

        index = self.client.catalog.public() or {}
        for group, entries in (index.get("layers") or {}).items():
            kind = "raster" if group == "raster" else "vector"
            for entry in entries or []:
                rows.append(dict(entry,
                                 layer_type=kind,
                                 uid=entry.get("id"),
                                 storage_backend=("raster" if kind == "raster" else group),
                                 _base=self.url,
                                 _public=True))
        return rows

    def portals(self) -> list[dict]:
        """Published portals. Anonymous callers get the public ones; a token adds the rest."""
        if self.token:
            try:
                return self.client.portals.list() or []
            except GeoDeployError:
                pass
        return (self.client.catalog.public() or {}).get("portals") or []

    def fetch_json(self, url: str, cache: bool = True) -> dict:
        """GET any URL on this instance as JSON, with the token when there is one.

        The layer surfaces (TileJSON, WMTS, legends) are ordinary URLs rather than client methods,
        and a private layer's are behind the credential — so this carries it, and the same
        User-Agent as everything else.

        CACHED BY DEFAULT, and that is a speed fix rather than a nicety. These documents describe a
        layer, not its data: a TileJSON, a legend, a set of bounds. Opening a seven-layer portal
        asked for a dozen of them, one after another, and every one was a blocking round trip —
        which is a large part of what "QGIS freezes for a while" was. They are re-read when the
        instance is reconnected, which is the only moment they can meaningfully change.
        """
        import json
        from urllib.request import Request, urlopen

        if cache and url in self._doc_cache:
            hit = self._doc_cache[url]
            if isinstance(hit, Exception):
                raise hit               # a 404 is an answer too; do not ask again for it
            return hit

        headers = {"User-Agent": self._c_user_agent(), "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer {0}".format(self.token)
        try:
            with urlopen(Request(http_url(url), headers=headers), timeout=30) as response:  # noqa: S310  # nosec B310 - scheme restricted to http/https by http_url above
                doc = json.loads(response.read().decode("utf-8"))
        except Exception as exc:        # noqa: BLE001 - surfaced as a plugin message
            error = GeoDeployError("Could not read {0}: {1}".format(url, exc))
            if cache:
                self._doc_cache[url] = error
            raise error
        if cache:
            self._doc_cache[url] = doc
        return doc

    def prefetch(self, urls) -> None:
        """Warm the cache for several documents. Safe to call from a WORKER thread — which is the
        point: the layers themselves must be built on the GUI thread, so the network part is done
        before that starts rather than one blocking request at a time in the middle of it."""
        for url in urls:
            if not url or url in self._doc_cache:
                continue
            try:
                self.fetch_json(url)
            except GeoDeployError:
                pass                    # already recorded in the cache; the caller degrades

    def fetch_text(self, url: str, cache: bool = True) -> str:
        """GET a URL on this instance as text — WMTS capabilities are XML, not JSON.

        Cached like `fetch_json`, and for the same reason: this is read on the GUI thread every time
        a raster is added, and a capabilities document describes the raster rather than its pixels.
        """
        from urllib.request import Request, urlopen

        key = "text:" + url
        if cache and key in self._doc_cache:
            hit = self._doc_cache[key]
            if isinstance(hit, Exception):
                raise hit
            return hit
        headers = {"User-Agent": self._c_user_agent(), "Accept": "application/xml, text/xml, */*"}
        if self.token:
            headers["Authorization"] = "Bearer {0}".format(self.token)
        try:
            with urlopen(Request(http_url(url), headers=headers), timeout=30) as response:  # noqa: S310  # nosec B310 - scheme restricted to http/https by http_url above
                body = response.read().decode("utf-8", "replace")
        except Exception as exc:        # noqa: BLE001 - surfaced as a plugin message
            error = GeoDeployError("Could not read {0}: {1}".format(url, exc))
            if cache:
                self._doc_cache[key] = error
            raise error
        if cache:
            self._doc_cache[key] = body
        return body

    def published_style(self, slug: str) -> dict:
        """A published portal's own style.json — served to anyone, no credential involved.

        Reading a portal through `/api/portals/<id>` needs a token. A PUBLISHED portal is public by
        definition, and this is the document its own web page loads, so it is the honest source for
        "show me this portal" without an account.
        """
        import json
        from urllib.request import Request, urlopen

        url = "{0}/portals/{1}/style.json".format(self.url.rstrip("/"), slug)
        # A User-Agent is not optional here. urllib sends "Python-urllib/3.x" by default, and a
        # Cloudflare-fronted instance answers that with 403 — measured: the same URL returns 200 to
        # curl and to a browser, 403 to urllib. The portal is public; it was the client that looked
        # suspicious. Named after the tool, like the API client's own agent.
        request = Request(http_url(url), headers={"User-Agent": self._c_user_agent(),
                                                  "Accept": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - scheme checked by http_url  # nosec B310 - scheme restricted to http/https by http_url above
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:        # noqa: BLE001 - surfaced as a plugin message
            raise GeoDeployError("Could not read the published portal at {0}: {1}".format(url, exc))

    def _c_user_agent(self) -> str:
        """The same agent string the API client sends, so one instance sees one tool."""
        try:
            return self.client.user_agent
        except Exception:               # noqa: BLE001 - never fail over a header
            return "geodeploy-qgis"

    def check(self) -> dict:
        """Prove the connection works, and say what kind it is — shown in the dock's header.

        Deliberately tolerant: an instance that publishes nothing, or has its index switched off,
        is still a usable connection for someone holding a token.
        """
        who = None
        if self.token:
            who = self.client.whoami()
        index = {}
        try:
            index = self.client.catalog.public() or {}
        except GeoDeployError:
            index = {}
        counts = index.get("counts") or {}
        # What a TOKEN can see is the number that matters once you are signed in. Reporting only the
        # public counts told someone holding an editor token that their instance had "4 layers" when
        # it had forty — the public index is, by design, the smallest view of the instance.
        visible_layers = visible_portals = None
        if self.token:
            try:
                visible_layers = len(self.layers())
                visible_portals = len(self.portals())
            except GeoDeployError:
                # A token that cannot list is still a valid connection: say nothing rather than
                # claim zero, and let the listing below report the real error.
                visible_layers = visible_portals = None
        return {
            "url": self.url,
            "authenticated": bool(who),
            "user": (who or {}).get("email") or (who or {}).get("name"),
            "public_layers": sum(v for k, v in counts.items() if k != "portals"),
            "public_portals": counts.get("portals", 0),
            "visible_layers": visible_layers,
            "visible_portals": visible_portals,
            "index_available": bool(index),
        }


def saved_instances() -> list[tuple[str, str | None]]:
    """`(url, token)` for every profile the CLI has stored, so the plugin offers them.

    Nothing is written back here: the plugin reads the CLI's config, it does not manage it.
    """
    out: list[tuple[str, str | None]] = []
    try:
        config = Config.load()
    except Exception:                     # noqa: BLE001 - a broken config must not stop the plugin
        return out
    for _name, profile in (config.profiles or {}).items():
        url = (profile or {}).get("url")
        if not url:
            continue
        token = None
        try:
            # `{token?, jwt?, email?}` — a stored browser SESSION is not a token and is not used
            # here: it expires, and a plugin failing hours later is worse than browsing anonymously.
            token = (load_credential(url) or {}).get("token")
        except Exception:                 # noqa: BLE001 - keyring can fail; anonymous still works
            token = None
        out.append((url, token))
    return out
