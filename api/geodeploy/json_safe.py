"""A JSON response that cannot be broken by a number.

JSON has no literal for NaN or infinity, and Starlette serialises with `allow_nan=False`. The
ValueError that follows is raised while the RESPONSE is being built — after the handler has already
returned successfully — so the failure is never "one bad value": the whole endpoint returns 500 and
the client sees nothing at all. On a live instance this emptied My Data, took down `/api/public`,
`/api/ogc/collections` and a layer's `/legend` together, and left the QGIS plugin reporting
"HTTP 500" with no layers, all from a single uploaded raster whose nodata was NaN.

Guarding individual fields cannot close this. A non-finite float can arrive from anywhere:

  * a float32 raster's nodata, which is NaN more often than not;
  * bounds, when reprojection fails to converge;
  * classification breaks stored in a layer's style — `json.dumps` WRITES NaN by default and
    `json.loads` reads it back without complaint, so it round-trips into the database unnoticed;
  * any float column in the user's own data, served through OGC API - Features.

That last one is the argument for fixing it here: user data is not ours to validate, and a column
of measurements with gaps in it is not an error.

The scrub is not free, so it is not paid unless it is needed: serialise strictly first — the fast C
path, and the overwhelmingly common case — and only walk the content when that raises. A response
with no non-finite float in it costs exactly what it did before.

`null` is the right substitute. It is what every JSON dialect that has faced this chose (orjson,
simplejson's `ignore_nan`, JavaScript's own `JSON.stringify`), it parses everywhere, and a client
that checks for null already handles "no value" — which is what NaN meant.
"""
from __future__ import annotations

import json
import math
from typing import Any

from starlette.responses import JSONResponse


def scrub(value: Any) -> Any:
    """Replace every non-finite float with None, structurally.

    Containers are rebuilt rather than mutated: the content may be a cached object, or a Pydantic
    dump someone else still holds a reference to.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


class SafeJSONResponse(JSONResponse):
    """`JSONResponse`, except a NaN costs a value instead of the whole response."""

    def render(self, content: Any) -> bytes:
        try:
            return json.dumps(
                content,
                ensure_ascii=False,
                allow_nan=False,
                indent=None,
                separators=(",", ":"),
            ).encode("utf-8")
        except ValueError:
            # Only reachable when the content holds NaN or +/-Infinity. Walk it, then serialise
            # strictly again — so anything the scrub misses still fails loudly rather than
            # emitting invalid JSON that a browser would refuse to parse.
            return json.dumps(
                scrub(content),
                ensure_ascii=False,
                allow_nan=False,
                indent=None,
                separators=(",", ":"),
            ).encode("utf-8")
