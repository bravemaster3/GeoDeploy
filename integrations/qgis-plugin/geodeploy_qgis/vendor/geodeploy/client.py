"""The GeoDeploy API client.

This is the piece the QGIS plugin imports. It is deliberately a *library*: it never prints, never
reads the config file, never calls `sys.exit` — it takes a URL and a credential, makes requests, and
raises the typed errors in `errors.py`. Everything user-facing lives in `geodeploy.cli`.

    from geodeploy import Client
    gd = Client("https://geodeploy.example.org", token="gdp_…")
    gd.whoami()
    gd.uploads.upload("roads.gpkg", wait=True)
    gd.portals.publish(3)

Grouping is by API area (`gd.vector`, `gd.portals`, `gd.admin`, …) so that discovering the client
in an editor mirrors discovering the API in `/api/docs`.
"""
from __future__ import annotations

import json as _json
import os
from typing import Any, Callable, Dict, Optional, Union
from urllib.parse import quote, urlencode, urljoin

from . import errors
from .transport import Request, Response, UrllibTransport

__all__ = ["Client"]

#: Sent on every request. An instance's access log is where an operator works out that "the API is
#: hammering us" is in fact someone's nightly CLI job, so it names the tool and its version.
def _default_user_agent() -> str:
    from . import __version__
    return "geodeploy-cli/{0}".format(__version__)


class Client(object):
    """A connection to one GeoDeploy instance.

    Args:
        url: instance origin, e.g. ``https://geodeploy.example.org`` (with or without ``/api``).
        token: a scoped API token (``gdp_…``) from Settings → API tokens.
        jwt: a session JWT instead of a token — what ``geodeploy login --password`` obtains. Admin
            routes (`/admin/*`, ownership transfer, token management) REJECT API tokens by design,
            so anything under `gd.admin` needs this rather than a `gdp_` token.
        transport: anything with ``send(Request) -> Response``. Swap in a Qt/QGIS network stack to
            inherit the host's proxy and certificate configuration.
        timeout: seconds for ordinary API calls.
        upload_timeout: seconds for calls that move file bytes — a 5 GB PUT over a slow link is not
            a hung request, and killing it at 120 s would make big uploads impossible.
    """

    def __init__(self, url: str, token: Optional[str] = None, jwt: Optional[str] = None,
                 transport: Optional[Any] = None, timeout: float = 120.0,
                 upload_timeout: float = 3600.0, user_agent: Optional[str] = None,
                 verify_tls: bool = True, retries: int = 2,
                 on_request: Optional[Callable[[str, str], None]] = None):
        from .config import normalize_url
        self.url = normalize_url(url)
        self.token = token or None
        self.jwt = jwt or None
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.user_agent = user_agent or _default_user_agent()
        self.transport = transport or UrllibTransport(verify_tls=verify_tls, retries=retries)
        #: Called with (method, url) before each request — the CLI's `-v` uses it, and a plugin can
        #: route it to the QGIS message log without this module knowing what logging is.
        self.on_request = on_request

        # Namespaces. Imported here rather than at module scope because each one imports this
        # module for typing; the cost is one attribute lookup at construction.
        from .admin import Admin, Users
        from .catalog import Catalog
        from .imports import Imports
        from .jobs import Jobs
        from .layers import Layers, RasterLayers, VectorLayers
        from .portals import Portals
        from .sources import Sources
        from .uploads import Uploads

        self.vector = VectorLayers(self)
        self.raster = RasterLayers(self)
        #: Backend-agnostic helpers: resolve a layer by id/uid/name across both kinds.
        self.layers = Layers(self)
        self.portals = Portals(self)
        self.sources = Sources(self)
        self.imports = Imports(self)
        self.jobs = Jobs(self)
        self.uploads = Uploads(self)
        self.admin = Admin(self)
        self.users = Users(self)
        self.catalog = Catalog(self)

    # ── URLs ─────────────────────────────────────────────────────────────────────────────────────

    def api_url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Absolute URL for an API path. `path` is relative to `/api` unless it already starts there."""
        if path.startswith("http://") or path.startswith("https://"):
            base = path
        else:
            clean = path if path.startswith("/") else "/" + path
            if not clean.startswith("/api"):
                clean = "/api" + clean
            base = self.url + clean
        query = _query(params)
        return base + (("&" if "?" in base else "?") + query if query else "")

    def absolute(self, url_or_path: str) -> str:
        """Resolve something the API handed back (`/s3/…?X-Amz-…`) against this instance.

        Presigned upload URLs come back RELATIVE for a managed MinIO — nginx proxies `/s3/` with
        the signed Host preserved — and absolute for an external S3 endpoint. Both must work, and
        only the client knows the origin.
        """
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            return url_or_path
        return urljoin(self.url + "/", url_or_path.lstrip("/"))

    # ── Requests ─────────────────────────────────────────────────────────────────────────────────

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                json: Any = None, body: Any = None, headers: Optional[Dict[str, str]] = None,
                timeout: Optional[float] = None, auth: bool = True,
                parse: bool = True, content_type: Optional[str] = None) -> Any:
        """One API call. Returns parsed JSON by default, or the raw `Response` when `parse=False`."""
        url = self.api_url(path, params)
        hdrs = {"Accept": "application/json", "User-Agent": self.user_agent}
        if auth:
            hdrs.update(self.auth_headers())
        if headers:
            hdrs.update(headers)

        data = body
        if json is not None:
            data = _json.dumps(json).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        if content_type:
            hdrs["Content-Type"] = content_type

        if self.on_request:
            self.on_request(method.upper(), url)
        response = self.transport.send(
            Request(method, url, hdrs, data, timeout if timeout is not None else self.timeout))
        return self._handle(response, parse)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, **kw: Any) -> Any:
        return self.request("GET", path, params=params, **kw)

    def post(self, path: str, json: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json=json, **kw)

    def put(self, path: str, json: Any = None, **kw: Any) -> Any:
        return self.request("PUT", path, json=json, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    def send_absolute(self, method: str, url: str, body: Any = None,
                      headers: Optional[Dict[str, str]] = None,
                      timeout: Optional[float] = None) -> Response:
        """A request to a URL that is NOT the API — a presigned storage PUT.

        Auth headers are deliberately absent: a presigned URL carries its own signature in the
        query string, and an extra `Authorization` header makes S3 reject the request as
        double-authenticated. (The UI's api client has the same note for the same reason.)
        """
        hdrs = {"User-Agent": self.user_agent}
        hdrs.update(headers or {})
        if self.on_request:
            self.on_request(method.upper(), url)
        return self.transport.send(
            Request(method, self.absolute(url), hdrs, body,
                    timeout if timeout is not None else self.upload_timeout))

    def download(self, path: str, sink, params: Optional[Dict[str, Any]] = None,
                 auth: bool = True, timeout: Optional[float] = None) -> Response:
        """Stream a GET into a writable binary file object (COG, PMTiles, an export bundle)."""
        url = self.api_url(path, params)
        hdrs = {"User-Agent": self.user_agent}
        if auth:
            hdrs.update(self.auth_headers())
        streamer = getattr(self.transport, "stream", None)
        if streamer is None:  # a custom transport without streaming: fall back to buffered
            response = self.transport.send(Request("GET", url, hdrs, None,
                                                   timeout or self.upload_timeout))
            self._handle(response, parse=False)
            sink.write(response.content)
            return response
        response = streamer(Request("GET", url, hdrs, None, timeout or self.upload_timeout), sink)
        return self._handle(response, parse=False)

    def auth_headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": "Bearer " + self.token}
        if self.jwt:
            return {"Authorization": "Bearer " + self.jwt}
        return {}

    # ── Responses ────────────────────────────────────────────────────────────────────────────────

    def _handle(self, response: Response, parse: bool = True) -> Any:
        if response.status >= 400:
            raise errors.from_status(response.status, _detail(response), response.url,
                                     _safe_json(response))
        if not parse:
            return response
        if response.status == 204 or not response.content:
            return None
        ctype = response.headers.get("content-type", "")
        if "json" in ctype:
            return response.json()
        return response.text

    # ── Identity ─────────────────────────────────────────────────────────────────────────────────

    def whoami(self) -> Dict[str, Any]:
        """The authenticated user (`GET /auth/me`). Works for both a token and a session JWT."""
        return self.get("/auth/me")

    def login(self, email: str, password: str) -> str:
        """Exchange a password for a session JWT, store it on this client, and return it.

        Needed for the routes that refuse API tokens on purpose (`/admin/*`, `/tokens`, ownership
        transfer): a leaked token must not be able to reconfigure the instance or mint more tokens.
        """
        form = urlencode({"username": email, "password": password}).encode()
        data = self.request("POST", "/auth/login", body=form, auth=False,
                            content_type="application/x-www-form-urlencoded")
        jwt = (data or {}).get("access_token")
        if not jwt:
            raise errors.AuthError(401, "Login did not return a token.", self.api_url("/auth/login"))
        self.jwt = jwt
        self.token = None  # a session supersedes any token on this client instance
        return jwt

    def setup_status(self) -> Dict[str, Any]:
        """Public: whether the instance is set up, and which login methods it offers."""
        return self.get("/setup/status", auth=False)

    def tokens(self) -> Any:
        """List the acting user's API tokens (session auth only — a token cannot list tokens)."""
        return self.get("/tokens")

    def create_token(self, name: str, scopes, expires_in_days: int = 90) -> Dict[str, Any]:
        """Mint an API token. The raw `gdp_…` secret is in the response and never retrievable again."""
        return self.post("/tokens", {"name": name, "scopes": list(scopes),
                                     "expires_in_days": expires_in_days})

    def revoke_token(self, token_id: int) -> Any:
        return self.delete("/tokens/{0}".format(int(token_id)))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        how = "token" if self.token else ("session" if self.jwt else "anonymous")
        return "<geodeploy.Client {0} ({1})>".format(self.url, how)


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _query(params: Optional[Dict[str, Any]]) -> str:
    """Encode query params, dropping Nones and flattening lists — `?bbox=…&ids=a&ids=b`."""
    if not params:
        return ""
    pairs = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        elif isinstance(value, (list, tuple)):
            for item in value:
                if item is not None:
                    pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    return urlencode(pairs)


def _safe_json(response: Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _detail(response: Response) -> str:
    """The most useful one-line message an error response contains.

    FastAPI puts a string in `detail` for a raised HTTPException and a LIST of field errors there
    for a validation failure; nginx returns HTML. All three have to become something a person can
    read on one line, because that line is what the CLI prints.
    """
    payload = _safe_json(response)
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    loc = ".".join(str(x) for x in (item.get("loc") or [])[1:])
                    parts.append("{0}: {1}".format(loc, item.get("msg")) if loc else str(item.get("msg")))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        if detail is not None:
            return str(detail)
        for key in ("message", "error"):
            if payload.get(key):
                return str(payload[key])
    text = (response.text or "").strip()
    if text.startswith("<"):  # an nginx/proxy HTML page — the status is the only real information
        return {413: "Request body too large for the server or a proxy in front of it.",
                502: "Bad gateway — the instance is up but the API did not answer.",
                504: "Gateway timeout."}.get(response.status, "HTTP {0}".format(response.status))
    return text[:500] or "HTTP {0}".format(response.status)


def path_segment(value: Any) -> str:
    """URL-quote one path segment (a slug, a uid, a storage key)."""
    return quote(str(value), safe="")


def env_client(**kw: Any) -> Client:
    """A client from `GEODEPLOY_URL` / `GEODEPLOY_TOKEN` alone — for scripts and doctests."""
    url = os.environ.get("GEODEPLOY_URL")
    if not url:
        raise errors.ConfigError("Set GEODEPLOY_URL (and usually GEODEPLOY_TOKEN).")
    return Client(url, token=os.environ.get("GEODEPLOY_TOKEN"), **kw)
