"""Test fixtures: a real HTTP server that behaves like a GeoDeploy instance.

Why a server rather than mocks: the parts of this client most likely to break are the ones a mock
cannot see — a streamed multipart body, a presigned PUT that must NOT carry an Authorization
header, a relative `/s3/…` upload URL, an ETag read from a response header, chunked parts
assembled in the right order. `FakeInstance` therefore records what actually arrived on the wire,
and the tests assert against that.

It is a stub, not an emulator: it knows the routes this client calls, in the shapes
`api/geodeploy/routers/` returns.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeInstance(object):
    """In-memory GeoDeploy: layers, portals, jobs, uploads — and a log of every request."""

    def __init__(self):
        self.requests = []          # [{method, path, query, headers, body}]
        self.uploads = {}           # s3_key -> assembled bytes
        self.multiparts = {}        # upload_id -> {key, parts:{n: bytes}, aborted: bool}
        self.jobs = {}              # job_id -> [status dicts to return, last one repeats]
        self.fail_next = None       # (status, detail) injected into the next API call
        self.exports = {}           # export job id -> the bytes its download returns
        self.public_index_enabled = True
        self.token_forbidden = set()  # path prefixes that reject token auth, like /admin
        self.vector_layers = [
            {"id": 1, "uid": "aaaaaaaaaaaa", "name": "roads", "layer_type": "vector",
             "status": "ready", "geometry_type": "linestring", "feature_count": 1200,
             "storage_backend": "postgis", "visibility": "organization", "is_public": False,
             "crs": "EPSG:4326", "file_size": 2048, "created_by": "Koffi",
             "columns": [{"name": "id", "type": "integer"}, {"name": "pop", "type": "integer"}],
             "default_style": {"opacity": 1.0, "style": {"color": "#3b82f6"},
                               "popup_fields": []}},
            {"id": 2, "uid": "bbbbbbbbbbbb", "name": "field sites", "layer_type": "vector",
             "status": "processing", "progress": 42, "current_step": "Converting",
             "geometry_type": "point", "feature_count": None, "storage_backend": "geoparquet",
             "visibility": "private", "is_public": False, "columns": []},
        ]
        self.raster_layers = [
            {"id": 1, "uid": "cccccccccccc", "name": "dem", "layer_type": "raster",
             "status": "ready", "band_count": 1, "visibility": "public", "is_public": True,
             "crs": "EPSG:3006", "file_size": 999, "default_style": {"opacity": 1.0}},
        ]
        self.portals = [
            {"id": 3, "title": "Field sites 2026", "slug": "field-sites-2026", "published": True,
             "access_type": "public", "template_id": "minimal", "description": "About",
             "layer_configs": [
                 {"layer_id": 1, "layer_type": "vector", "visible": True, "opacity": 1.0,
                  "style": {"color": "#3b82f6"}, "popup_fields": []}],
             "layer_groups": None, "created_by": "Koffi"},
        ]

    # ── dispatch ────────────────────────────────────────────────────────────────────────────────

    def handle(self, method, path, query, headers, body):
        self.requests.append({"method": method, "path": path, "query": query,
                              "headers": dict(headers), "body": body})

        if path.startswith("/s3/"):
            return self._storage(method, path, query, headers, body)

        if self.fail_next is not None:
            status, detail = self.fail_next
            self.fail_next = None
            return status, {"detail": detail}

        auth = headers.get("authorization") or ""
        is_token = auth.startswith("Bearer gdp_")
        api = path[len("/api"):] if path.startswith("/api") else path

        if any(api.startswith(prefix) for prefix in self.token_forbidden) and is_token:
            return 403, {"detail": "Not permitted with an API token."}

        for pattern, handler in self._routes():
            match = re.match(pattern + "$", api)
            if match and handler:
                return handler(method, match, query, headers, body)
        return 404, {"detail": "Not Found: {0} {1}".format(method, path)}

    def _routes(self):
        return [
            (r"/auth/me", self._me),
            (r"/auth/login", self._login),
            (r"/setup/status", lambda *a: (200, {"completed": True, "email_enabled": False})),
            (r"/tokens", self._tokens),
            (r"/tokens/(\d+)", lambda m, mo, *a: (200, {"ok": True, "id": int(mo.group(1))})),
            (r"/data/vector", self._vector_list),
            (r"/data/raster", self._raster_list),
            (r"/data/vector/upload", self._upload_form),
            (r"/data/vector/upload-csv", self._upload_form),
            (r"/data/raster/upload", self._upload_form),
            (r"/data/(vector|raster)/upload/multipart/initiate", self._mp_initiate),
            (r"/data/(vector|raster)/upload/multipart/complete", self._mp_complete),
            (r"/data/(vector|raster)/upload/multipart/abort", self._mp_abort),
            (r"/data/vector/geoparquet/presign", self._presign),
            (r"/data/vector/large/presign", self._presign),
            (r"/data/vector/geoparquet/complete", self._register),
            (r"/data/vector/large/complete", self._register),
            (r"/data/raster/large/complete", self._register_raster),
            (r"/data/(vector|raster)/jobs/([\w-]+)", self._job),
            (r"/data/vector/(\S+)/field-stats", self._field_stats),
            (r"/data/(vector|raster)/(\d+)/sharing", self._sharing),
            (r"/data/(vector|raster)/(\d+)/rename", self._rename),
            (r"/data/(vector|raster)/(\d+)/default-style", self._default_style),
            (r"/data/(vector|raster)/(\d+)/usage", lambda *a: (200, [])),
            (r"/data/(vector|raster)/(\d+)/links", self._links),
            (r"/data/(vector|raster)/(\d+)", self._delete_layer),
            (r"/data/vector/(\S+)/features", self._features),
            (r"/data/raster/(\S+)/cog", lambda *a: (200, b"II-pretend-GeoTIFF-bytes")),
            (r"/data/vector/(\S+)/pmtiles", lambda *a: (200, b"PMTiles pretend")),
            (r"/public", self._public),
            (r"/public/portals", self._public_portals),
            (r"/data/(vector|raster)/(\S+)/export", self._export_start),
            (r"/data/(vector|raster)/(\S+)/export-status/([\w-]+)", self._export_status),
            (r"/data/(vector|raster)/(\S+)/export-download/([\w-]+)", self._export_download),
            (r"/data/vector/(\d+)/tile", lambda *a: (202, self._new_job(1, "vector"))),
            (r"/data/vector/(\d+)/reprocess", lambda *a: (202, self._new_job(1, "vector"))),
            (r"/data/sources", self._sources),
            (r"/data/sources/(\d+)", lambda m, mo, *a: (200, {"ok": True})),
            (r"/portals", self._portals),
            (r"/portals/([\w-]+)/style\.json", self._portal_style),
            (r"/portals/(\d+)", self._portal),
            (r"/portals/(\d+)/publish", self._publish),
            (r"/portals/(\d+)/unpublish", self._unpublish),
            (r"/templates", lambda *a: (200, [{"id": "minimal", "name": "Minimal",
                                               "archetypes": ["webmap"], "version": "1.0",
                                               "is_official": True, "tags": []}])),
            (r"/basemaps", lambda *a: (200, [{"id": "positron", "name": "Positron"}])),
            (r"/ogc/collections", lambda *a: (200, {"collections": [
                {"id": "vector-aaaaaaaaaaaa", "title": "roads"}]})),
            (r"/ogc/conformance", lambda *a: (200, {"conformsTo": ["core", "geojson"]})),
            (r"/stac/collections", lambda *a: (200, {"collections": [{"id": "vectors"}]})),
            (r"/admin/public-index", self._public_index_setting),
            (r"/admin/health", lambda *a: (200, [{"name": "api", "status": "healthy",
                                                  "controllable": False}])),
            (r"/admin/storage-stats", lambda *a: (200, {"used_bytes": 1024, "postgis_bytes": 512,
                                                        "raster_bytes": None, "vector_layers": 2,
                                                        "raster_layers": 1, "portals": 1})),
            (r"/users", lambda *a: (200, [{"id": 1, "name": "Koffi", "email": "k@example.org",
                                           "role": "owner"}])),
        ]

    # ── handlers ────────────────────────────────────────────────────────────────────────────────

    def _me(self, method, match, query, headers, body):
        if not (headers.get("authorization") or ""):
            return 401, {"detail": "Not authenticated"}
        return 200, {"id": 1, "email": "k@example.org", "name": "Koffi", "role": "owner",
                     "is_admin": True, "created_at": "2026-01-01T00:00:00"}

    def _login(self, method, match, query, headers, body):
        form = parse_qs((body or b"").decode())
        if form.get("password", [""])[0] != "correct-horse":
            return 401, {"detail": "Incorrect email or password"}
        return 200, {"access_token": "jwt-for-" + form.get("username", [""])[0],
                     "token_type": "bearer"}

    def _tokens(self, method, match, query, headers, body):
        if method == "POST":
            payload = json.loads(body or b"{}")
            return 200, {"id": 9, "name": payload.get("name"), "prefix": "gdp_abcd1234",
                         "scopes": payload.get("scopes"), "expires_at": "2027-01-01T00:00:00",
                         "created_at": "2026-01-01T00:00:00", "token": "gdp_secret_value"}
        return 200, [{"id": 9, "name": "ci", "prefix": "gdp_abcd1234", "scopes": ["data:read"],
                      "expires_at": "2027-01-01T00:00:00", "created_at": "2026-01-01T00:00:00"}]

    def _vector_list(self, method, match, query, headers, body):
        return 200, self.vector_layers

    def _raster_list(self, method, match, query, headers, body):
        return 200, self.raster_layers

    def _upload_form(self, method, match, query, headers, body):
        """Record what the multipart body actually contained, then answer like the API does."""
        parsed = parse_multipart(headers.get("content-type", ""), body or b"")
        self.last_form = parsed
        layer_type = "raster" if "raster" in match.group(0) else "vector"
        return 202, self._new_job(len(self.vector_layers) + 1, layer_type)

    def _presign(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        key = "vectors/1/{0}/{1}".format(uuid.uuid4().hex, payload.get("filename") or "file")
        # Relative, exactly like a managed MinIO behind the nginx /s3/ proxy.
        return 200, {"upload_url": "/s3/{0}?X-Amz-Signature=abc".format(key), "s3_key": key}

    def _mp_initiate(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        kind = payload.get("kind")
        prefix = "rasters" if kind == "raster" else "vectors"
        key = "{0}/1/{1}/{2}".format(prefix, uuid.uuid4().hex, payload.get("filename"))
        upload_id = uuid.uuid4().hex
        part_size = 1024                      # tiny, so tests exercise many parts cheaply
        count = max(1, -(-int(payload.get("file_size") or 1) // part_size))
        self.multiparts[upload_id] = {"key": key, "parts": {}, "aborted": False}
        return 200, {"s3_key": key, "upload_id": upload_id, "part_size": part_size,
                     "parts": [{"part_number": n + 1,
                                "url": "/s3/{0}?partNumber={1}&uploadId={2}".format(
                                    key, n + 1, upload_id)}
                               for n in range(count)]}

    def _mp_complete(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        record = self.multiparts.get(payload.get("upload_id"))
        if record is None:
            return 400, {"detail": "no such upload"}
        ordered = [record["parts"][n] for n in sorted(record["parts"])]
        self.uploads[record["key"]] = b"".join(ordered)
        record["completed_parts"] = payload.get("parts")
        return 200, {"s3_key": record["key"]}

    def _mp_abort(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        record = self.multiparts.get(payload.get("upload_id"))
        if record is not None:
            record["aborted"] = True
        return 200, {"ok": True}

    def _register(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        self.last_register = payload
        return 202, self._new_job(len(self.vector_layers) + 1, "vector")

    def _register_raster(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        self.last_register = payload
        return 202, self._new_job(len(self.raster_layers) + 1, "raster")

    def _job(self, method, match, query, headers, body):
        job_id = match.group(2)
        states = self.jobs.get(job_id)
        if not states:
            return 404, {"detail": "Job not found"}
        state = states.pop(0) if len(states) > 1 else states[0]
        return 200, state

    def _new_job(self, layer_id, layer_type, states=None):
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = states or [
            {"id": job_id, "layer_id": layer_id, "layer_type": layer_type, "status": "queued",
             "progress": 0, "current_step": "Queued", "error_message": None},
            {"id": job_id, "layer_id": layer_id, "layer_type": layer_type, "status": "processing",
             "progress": 60, "current_step": "Loading", "error_message": None},
            {"id": job_id, "layer_id": layer_id, "layer_type": layer_type, "status": "ready",
             "progress": 100, "current_step": "Done", "error_message": None},
        ]
        first = dict(self.jobs[job_id][0])
        return first

    # ── the public index + per-layer export ─────────────────────────────────────────────────────

    def _public(self, method, match, query, headers, body):
        if not self.public_index_enabled:
            return 404, {"detail": "This instance does not publish a public index."}
        base = "http://127.0.0.1"
        return 200, {
            "geodeploy": {"url": base, "api": base + "/api"},
            "counts": {"portals": 1, "postgis": 1, "geoparquet": 0, "raster": 1},
            "portals": self._public_portal_rows(),
            "layers": {
                "postgis": [{"id": "aaaaaaaaaaaa", "name": "roads", "kind": "postgis",
                             "geometry_type": "linestring", "feature_count": 1200,
                             "crs": "EPSG:4326", "keywords": ["transport"], "license": "CC-BY-4.0",
                             "links": [{"label": "OGC API - Features", "url": base + "/api/ogc"}],
                             "download": base + "/api/data/vector/aaaaaaaaaaaa/export"}],
                "geoparquet": [],
                "raster": [{"id": "cccccccccccc", "name": "dem", "kind": "raster",
                            "band_count": 1, "crs": "EPSG:3006", "keywords": [], "links": [],
                            "download": base + "/api/data/raster/cccccccccccc/cog"}],
            },
            "catalogs": {"stac": base + "/api/stac", "ogc_features": base + "/api/ogc"},
        }

    def _public_portals(self, method, match, query, headers, body):
        if not self.public_index_enabled:
            return 404, {"detail": "This instance does not publish a public index."}
        return 200, self._public_portal_rows()

    def _public_portal_rows(self):
        base = "http://127.0.0.1"
        return [{"slug": "field-sites-2026", "title": "Field sites 2026", "experience": "webmap",
                 "layer_count": 1, "url": base + "/portals/field-sites-2026/",
                 "style_url": base + "/portals/field-sites-2026/style.json",
                 "thumbnail_url": None, "published_at": "2026-08-01T00:00:00"}]

    def _portal_style(self, method, match, query, headers, body):
        """A published portal's own style.json — served outside /api, like the real bundle."""
        if match.group(1) != "field-sites-2026":
            return 404, {"detail": "No such portal."}
        base = "http://127.0.0.1"
        return 200, {
            "version": 8,
            "sources": {
                "basemap": {"type": "raster", "tiles": ["https://tiles.example/{z}/{x}/{y}.png"]},
                "vector_1": {"type": "vector", "tiles": [base + "/tiles/vector_1/{z}/{x}/{y}.pbf"]},
                "vector_3": {"type": "vector", "tiles": [base + "/tiles/vector_3/{z}/{x}/{y}.pbf"]},
                "raster_2": {"type": "raster", "tiles": [base + "/cog/2/{z}/{x}/{y}.png"]},
            },
            "layers": [
                {"id": "basemap", "type": "raster", "source": "basemap"},
                {"id": "vector-1", "type": "line", "source": "vector_1",
                 "metadata": {"geodeploy:name": "Roads", "geodeploy:layer_id": 1,
                              "geodeploy:geometry": "linestring"}},
                {"id": "vector-3", "type": "fill", "source": "vector_3",
                 "metadata": {"geodeploy:name": "Plots", "geodeploy:layer_id": 3,
                              "geodeploy:geometry": "polygon"}},
                {"id": "vector-3-outline", "type": "line", "source": "vector_3",
                 "metadata": {"geodeploy:layer_id": 3, "geodeploy:part": True}},
                {"id": "raster-2", "type": "raster", "source": "raster_2",
                 "layout": {"visibility": "none"},
                 "metadata": {"geodeploy:name": "DEM", "geodeploy:layer_id": 2,
                              "geodeploy:geometry": "raster"}},
            ],
            "geodeploy": {"title": "Field sites 2026", "bounds": [11.0, 55.0, 12.0, 56.0],
                          "deckLayers": [{"id": "deck-7", "name": "Trees",
                                          "url": base + "/data/trees.parquet"}]},
        }

    def _export_start(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        self.last_export = payload
        if match.group(1) == "raster" and not payload.get("bbox"):
            return 400, {"detail": "A raster export needs a bbox. … GET /api/data/raster/x/cog"}
        job_id = uuid.uuid4().hex
        self.exports[job_id] = b"PK pretend zip"
        return 202, {"job_id": job_id}

    def _export_status(self, method, match, query, headers, body):
        return 200, {"status": "ready" if match.group(3) in self.exports else "queued"}

    def _export_download(self, method, match, query, headers, body):
        data = self.exports.get(match.group(3))
        if data is None:
            return 404, {"detail": "That export is not ready (or has been swept)."}
        return 200, data

    def _public_index_setting(self, method, match, query, headers, body):
        if method == "PUT":
            self.public_index_enabled = bool(json.loads(body or b"{}").get("enabled"))
        return 200, {"enabled": self.public_index_enabled}

    def _field_stats(self, method, match, query, headers, body):
        field = (query.get("field") or [""])[0]
        if field == "name":
            return 200, {"kind": "text", "count": 12,
                         "categories": [{"value": "forest", "count": 7},
                                        {"value": "water", "count": 5}],
                         "suggestion": {"color_mode": "categorized",
                                        "categories": [{"value": "forest", "color": "#3b82f6"},
                                                       {"value": "water", "color": "#ef4444"}]}}
        if field == "missing":
            return 400, {"detail": "No such field on this layer: missing"}
        return 200, {"kind": "numeric", "count": 100, "min": 0, "max": 500, "values": None,
                     "suggestion": {"color_mode": "graduated", "classes": [
                         {"min": None, "max": 100, "color": "#440154"},
                         {"min": 100, "max": 250, "color": "#21918c"},
                         {"min": 250, "max": None, "color": "#fde725"}]}}

    def _sharing(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        rows = self.vector_layers if match.group(1) == "vector" else self.raster_layers
        for row in rows:
            if row["id"] == int(match.group(2)):
                row.update(payload)
                if payload.get("visibility"):
                    row["is_public"] = payload["visibility"] == "public"
                return 200, row
        return 404, {"detail": "Layer not found."}

    def _rename(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        rows = self.vector_layers if match.group(1) == "vector" else self.raster_layers
        for row in rows:
            if row["id"] == int(match.group(2)):
                row["name"] = payload.get("name")
                return 200, row
        return 404, {"detail": "Layer not found."}

    def _default_style(self, method, match, query, headers, body):
        payload = json.loads(body or b"{}")
        rows = self.vector_layers if match.group(1) == "vector" else self.raster_layers
        for row in rows:
            if row["id"] == int(match.group(2)):
                row["default_style"] = payload
                return 200, row
        return 404, {"detail": "Layer not found."}

    def _links(self, method, match, query, headers, body):
        return 200, {"public": match.group(1) == "raster", "name": "roads", "catalog": "/api/stac",
                     "links": [{"label": "OGC API - Features", "url": "http://x/api/ogc",
                                "hint": "QGIS: Add OGC API - Features Layer"}]}

    def _delete_layer(self, method, match, query, headers, body):
        if method != "DELETE":
            return 405, {"detail": "Method Not Allowed"}
        rows = self.vector_layers if match.group(1) == "vector" else self.raster_layers
        target = int(match.group(2))
        keep = [r for r in rows if r["id"] != target]
        if len(keep) == len(rows):
            return 404, {"detail": "Layer not found."}
        rows[:] = keep
        return 200, {"ok": True}

    def _features(self, method, match, query, headers, body):
        return 200, {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [17.6, 59.8]},
             "properties": {"id": 1}}]}

    def _sources(self, method, match, query, headers, body):
        if method == "POST":
            payload = json.loads(body or b"{}")
            source = dict(payload, id=5, kind="vector" if payload.get("source_type") == "wfs"
                          else "raster", created_at="2026-01-01T00:00:00",
                          visibility="organization", bbox=None, geometry_type=None)
            return 200, source
        return 200, [{"id": 5, "name": "OSM", "source_type": "xyz", "kind": "raster",
                      "url": "https://tile/{z}/{x}/{y}.png", "visibility": "organization",
                      "layer_name": None, "created_at": "2026-01-01T00:00:00"}]

    def _portals(self, method, match, query, headers, body):
        if method == "POST":
            payload = json.loads(body or b"{}")
            portal = {"id": 10 + len(self.portals), "title": payload.get("title"),
                      "slug": (payload.get("title") or "portal").lower().replace(" ", "-"),
                      "published": False, "access_type": payload.get("access_type", "public"),
                      "template_id": payload.get("template_id", "minimal"),
                      "description": payload.get("description"),
                      "layer_configs": payload.get("layer_configs") or [],
                      "layer_groups": None,
                      "layout_config": payload.get("layout_config"),
                      "created_at": "2026-01-01T00:00:00"}
            self.portals.append(portal)
            return 200, portal
        return 200, self.portals

    def _find_portal(self, portal_id):
        for portal in self.portals:
            if portal["id"] == int(portal_id):
                return portal
        return None

    def _portal(self, method, match, query, headers, body):
        portal = self._find_portal(match.group(1))
        if portal is None:
            return 404, {"detail": "Portal not found."}
        if method == "PUT":
            payload = json.loads(body or b"{}")
            self.last_put = payload
            portal.update(payload)
            return 200, portal
        if method == "DELETE":
            self.portals.remove(portal)
            return 200, {"ok": True}
        return 200, portal

    def _publish(self, method, match, query, headers, body):
        portal = self._find_portal(match.group(1))
        if portal is None:
            return 404, {"detail": "Portal not found."}
        portal["published"] = True
        portal["published_at"] = "2026-08-11T10:00:00"
        return 200, portal

    def _unpublish(self, method, match, query, headers, body):
        portal = self._find_portal(match.group(1))
        portal["published"] = False
        return 200, portal

    # ── object storage ──────────────────────────────────────────────────────────────────────────

    def _storage(self, method, path, query, headers, body):
        """A presigned PUT target. Asserts the two things that go wrong in real life."""
        if headers.get("authorization"):
            # An Authorization header alongside a presigned signature makes S3 reject the request
            # as doubly authenticated — the client must not send one here.
            return 400, {"detail": "presigned URL must not carry an Authorization header"}
        key = path[len("/s3/"):]
        upload_id = (query.get("uploadId") or [None])[0]
        if upload_id:
            number = int((query.get("partNumber") or ["1"])[0])
            record = self.multiparts.get(upload_id)
            if record is None:
                return 404, {"detail": "no such upload"}
            record["parts"][number] = body or b""
            return 200, b"", {"ETag": '"etag-{0}"'.format(number)}
        self.uploads[key] = body or b""
        return 200, b"", {"ETag": '"etag-single"'}

    # ── helpers for tests ───────────────────────────────────────────────────────────────────────

    def requests_to(self, path_fragment, method=None):
        return [r for r in self.requests
                if path_fragment in r["path"] and (method is None or r["method"] == method)]

    def sent_authorization(self, path_fragment):
        for request in self.requests_to(path_fragment):
            return request["headers"].get("authorization")
        return None


def parse_multipart(content_type, body):
    """Minimal multipart/form-data parser: `{field: value, "_files": {name: (filename, bytes)}}`."""
    match = re.search(r"boundary=([^\s;]+)", content_type or "")
    if not match:
        return {}
    boundary = ("--" + match.group(1)).encode()
    out = {"_files": {}}
    for part in body.split(boundary):
        # Leading CRLF only: a trailing one may belong to the file's own last line.
        while part.startswith(b"\r\n"):
            part = part[2:]
        if not part or part.startswith(b"--"):
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        header_text = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]+)"', header_text)
        filename = re.search(r'filename="([^"]*)"', header_text)
        if not name:
            continue
        # Strip EXACTLY the one CRLF that precedes the boundary — `rstrip` would eat a trailing
        # newline that belongs to the uploaded file itself, which every CSV has.
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if filename:
            out["_files"][name.group(1)] = (filename.group(1), payload)
        else:
            out[name.group(1)] = payload.decode("utf-8", "replace")
    return out


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _run(self, method):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        result = self.server.instance.handle(method, parsed.path, parse_qs(parsed.query),
                                             headers, body)
        status, payload = result[0], result[1]
        extra = result[2] if len(result) > 2 else {}
        if isinstance(payload, (bytes, bytearray)):
            data, ctype = bytes(payload), "application/octet-stream"
        else:
            data = json.dumps(payload).encode("utf-8")
            ctype = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    do_GET = lambda self: self._run("GET")          # noqa: E731 - BaseHTTPRequestHandler protocol
    do_POST = lambda self: self._run("POST")        # noqa: E731
    do_PUT = lambda self: self._run("PUT")          # noqa: E731
    do_DELETE = lambda self: self._run("DELETE")    # noqa: E731


@pytest.fixture
def instance():
    return FakeInstance()


@pytest.fixture
def server(instance):
    # Threading: the client uploads multipart parts in parallel, and a
    # single-threaded server would serialise them into a stall.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.instance = instance
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:{0}".format(httpd.server_port)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def client(server):
    from geodeploy import Client
    return Client(server, token="gdp_test_token")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config directory, with the OS keyring switched off.

    Both matter: a test must never read the developer's real profiles, and must never write a
    secret into their login keychain.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setenv("GEODEPLOY_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("GEODEPLOY_NO_KEYRING", "1")
    monkeypatch.delenv("GEODEPLOY_URL", raising=False)
    monkeypatch.delenv("GEODEPLOY_TOKEN", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    return config_dir


@pytest.fixture
def run(home, capsys):
    """Invoke the CLI as a user would; returns (exit_code, stdout, stderr)."""
    from geodeploy.cli.main import main

    def invoke(*argv):
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


@pytest.fixture
def logged_in(run, server, home):
    """A configured profile pointing at the fake instance, using an API token."""
    code, _, _ = run("login", server, "--token", "gdp_test_token", "--json")
    assert code == 0
    return server
