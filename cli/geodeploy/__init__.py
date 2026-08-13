"""GeoDeploy — command-line client and Python API.

    from geodeploy import Client
    gd = Client("https://geodeploy.example.org", token="gdp_…")
    layer = gd.uploads.upload("roads.gpkg", wait=True)
    portal = gd.portals.create("Roads")
    gd.portals.add_layer(portal["id"], layer.layer_id, "vector", {"color": "#e11d48"})
    gd.portals.publish(portal["id"])

Zero runtime dependencies, Python 3.9+, so the QGIS plugin can vendor this package as-is.
"""
from __future__ import annotations

# A PyPI version can never be re-uploaded, so the FIRST upload is a pre-release: it proves the
# packaging against the real index without spending the number this ships under. `pip install
# geodeploy` ignores it (pre-releases need --pre), so nobody gets it by accident. Becomes 1.3.0
# when GeoDeploy v1.3 tags — the CLI's version tracks the release it belongs to.
__version__ = "1.3.0b1"

from .client import Client  # noqa: E402  (after __version__ — the user agent reads it)
from .errors import (  # noqa: E402
    APIError,
    AuthError,
    ConfigError,
    ConflictError,
    GeoDeployError,
    NotFoundError,
    PermissionError_,
    ServerError,
    TransportError,
    ValidationError,
)
from .jobs import JobFailed, JobTimeout  # noqa: E402

__all__ = [
    "Client",
    "GeoDeployError",
    "APIError",
    "AuthError",
    "ConfigError",
    "ConflictError",
    "NotFoundError",
    "PermissionError_",
    "ServerError",
    "TransportError",
    "ValidationError",
    "JobFailed",
    "JobTimeout",
    "__version__",
]
