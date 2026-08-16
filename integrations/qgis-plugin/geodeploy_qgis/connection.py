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


class Instance:
    """One connection. `token` may be None — that is a supported way to use this, not an error."""

    def __init__(self, url: str, token: str | None = None):
        self.url = normalize_url(url)
        self.token = token or None
        self.client = Client(self.url, token=self.token)

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
        request = Request(url, headers={"User-Agent": self._c_user_agent(),
                                        "Accept": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - our own instance URL
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
