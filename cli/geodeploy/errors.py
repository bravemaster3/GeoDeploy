"""Typed errors.

Every failure a caller can reasonably branch on gets its own class, because the alternative — one
exception carrying a status code — pushes `if err.status == 404` into every plugin and script that
uses this client. The QGIS plugin in particular needs to tell "your token is wrong" (stop and ask
the user) from "that layer is gone" (refresh the tree) from "the network blipped" (retry), and it
should not have to read status codes to do it.
"""
from __future__ import annotations

from typing import Any, Optional


class GeoDeployError(Exception):
    """Base class — catching this catches everything this package raises."""


class ConfigError(GeoDeployError):
    """Nothing usable to talk to: no instance URL, no credentials, unreadable profile file."""


class TransportError(GeoDeployError):
    """The request never got an HTTP answer — DNS, TLS, connection reset, timeout.

    Distinct from every `APIError` below: the server may or may not have done the work, so a caller
    retrying this must think about idempotency, which is never true of a 4xx.
    """


class APIError(GeoDeployError):
    """The instance answered, with a status we did not want."""

    def __init__(self, status: int, detail: str, url: str = "", payload: Any = None):
        self.status = status
        self.detail = detail
        self.url = url
        #: The decoded JSON body when there was one — FastAPI validation errors put the useful
        #: part (which field, why) in a list here that no single-line message can carry.
        self.payload = payload
        super().__init__(f"HTTP {status}: {detail}" if detail else f"HTTP {status}")


class AuthError(APIError):
    """401 — no credentials, an expired session, or a revoked/expired API token."""


class PermissionError_(APIError):
    """403 — authenticated, but not allowed.

    Two distinguishable causes, and the message says which: the caller's ROLE is too low, or the
    API TOKEN lacks a scope ("Token missing scope: data:write"). The second is fixable by minting a
    better token, the first is not — see `missing_scope`.
    """

    @property
    def missing_scope(self) -> Optional[str]:
        marker = "Token missing scope:"
        if marker in (self.detail or ""):
            return self.detail.split(marker, 1)[1].strip() or None
        return None


class NotFoundError(APIError):
    """404 — including "exists but you cannot see it": a private resource is hidden as absent."""


class ConflictError(APIError):
    """409 — the state moved under you (a backup already running, an email already registered)."""


class ValidationError(APIError):
    """400 / 413 / 422 — the request itself was wrong: bad geometry columns, oversized file."""


class ServerError(APIError):
    """5xx — the instance failed. Worth retrying; not worth reformulating the request."""


def from_status(status: int, detail: str, url: str = "", payload: Any = None) -> APIError:
    """Map an HTTP status onto the class above that a caller can act on."""
    if status == 401:
        return AuthError(status, detail, url, payload)
    if status == 403:
        return PermissionError_(status, detail, url, payload)
    if status == 404:
        return NotFoundError(status, detail, url, payload)
    if status == 409:
        return ConflictError(status, detail, url, payload)
    if status in (400, 413, 422) or status == 415:
        return ValidationError(status, detail, url, payload)
    if status >= 500:
        return ServerError(status, detail, url, payload)
    return APIError(status, detail, url, payload)
