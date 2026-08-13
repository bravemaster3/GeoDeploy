"""Data layers — vector, raster, and the resolver that lets you name one.

The API addresses a layer three ways and they are not interchangeable: authenticated routes take
the integer **id**, public routes take the stable **uid** (`models.new_uid`, 12 hex chars — an
integer is unique only within one layer kind and one database, so a shared URL must never use
one), and a person thinks in **names**. `Layers.resolve` accepts all three plus a `vector-3` /
`raster-7` prefix, so every command in the CLI can take whatever the user has to hand.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import NotFoundError, ValidationError

#: Layer fields worth showing in a list, in the order a table should print them.
SUMMARY_FIELDS = ("id", "uid", "name", "status", "geometry_type", "feature_count",
                  "crs", "storage_backend", "visibility", "created_by")


class _LayerBase(object):
    """Shared implementation for the two layer kinds — the routes are deliberately parallel."""

    kind = ""      # "vector" | "raster"
    base = ""      # "/data/vector" | "/data/raster"

    def __init__(self, client: Any):
        self._c = client

    # -- read ------------------------------------------------------------------------------------

    def list(self, status: Optional[str] = None, query: Optional[str] = None,
             visibility: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every layer of this kind that the caller can see.

        Filtering is client-side because the API has no filter params on the list endpoints (and
        `routers/README.md` explicitly asks that no `?created_by=` be added). At the scale a list
        endpoint is still un-paginated, filtering here is honest and instant.
        """
        rows = self._c.get(self.base) or []
        if status:
            rows = [r for r in rows if (r.get("status") or "") == status]
        if visibility:
            rows = [r for r in rows if (r.get("visibility") or "") == visibility]
        if query:
            needle = query.lower()
            rows = [r for r in rows
                    if needle in (r.get("name") or "").lower()
                    or needle in (r.get("abstract") or "").lower()
                    or needle in (r.get("keywords") or "").lower()]
        return rows

    def get(self, layer_id: Any) -> Dict[str, Any]:
        """One layer. There is no by-id authed GET, so this reads the list — the same data the
        dashboard shows, including live `progress`/`current_step` for a layer still ingesting."""
        wanted = str(layer_id)
        for row in self.list():
            if str(row.get("id")) == wanted or str(row.get("uid") or "") == wanted:
                return row
        raise NotFoundError(404, "No {0} layer {1}.".format(self.kind, layer_id))

    def usage(self, layer_id: Any) -> List[Dict[str, Any]]:
        """The portals that include this layer — what the UI shows before a delete."""
        return self._c.get("{0}/{1}/usage".format(self.base, int(layer_id))) or []

    def links(self, layer_id: Any) -> Dict[str, Any]:
        """Tool-labelled share URLs (OGC API - Features, WMTS, TileJSON, PMTiles, COG, …).

        The server decides which artifact suits which backend, so this is always current — the CLI
        must not build these URLs itself.
        """
        return self._c.get("{0}/{1}/links".format(self.base, int(layer_id)))

    # -- write -----------------------------------------------------------------------------------

    def rename(self, layer_id: Any, name: str) -> Dict[str, Any]:
        return self._c.put("{0}/{1}/rename".format(self.base, int(layer_id)), {"name": name})

    def share(self, layer_id: Any, visibility: Optional[str] = None, abstract: Optional[str] = None,
              license: Optional[str] = None, attribution: Optional[str] = None,
              keywords: Optional[str] = None) -> Dict[str, Any]:
        """Visibility + catalog metadata. Partial: only what you pass is applied.

        `visibility="public"` is the opt-IN that puts a layer in the STAC catalog and OGC API -
        Features collections and makes its raw asset readable — nothing is public by default.
        """
        body = {}
        for key, value in (("visibility", visibility), ("abstract", abstract), ("license", license),
                           ("attribution", attribution), ("keywords", keywords)):
            if value is not None:
                body[key] = value
        if not body:
            raise ValidationError(400, "Nothing to change — pass a visibility or a metadata field.")
        return self._c.put("{0}/{1}/sharing".format(self.base, int(layer_id)), body)

    def set_default_style(self, layer_id: Any, style: Dict[str, Any]) -> Dict[str, Any]:
        """The layer's own default styling — what a portal starts from when the layer is added."""
        return self._c.put("{0}/{1}/default-style".format(self.base, int(layer_id)), style)

    def delete(self, layer_id: Any) -> Any:
        """Delete the layer AND prune it from every portal that used it (the API re-publishes the
        published ones, so no ghost layer is left on a live map)."""
        return self._c.delete("{0}/{1}".format(self.base, int(layer_id)))

    # -- download --------------------------------------------------------------------------------

    def export(self, ref: Any, format: Optional[str] = None, bbox: Optional[str] = None,
               target_crs: str = "4326") -> Dict[str, Any]:
        """Queue a file export of this layer. No bbox = the whole layer.

        Public on the same terms as the layer's other artifacts, so this works with no token for a
        shared layer — which is the point: it is how an outside client downloads a PostGIS layer,
        the one backend that has no file to fetch directly.
        """
        body = {"format": format, "bbox": bbox, "target_crs": target_crs}
        return self._c.post("{0}/{1}/export".format(self.base, ref),
                            {k: v for k, v in body.items() if v is not None}, auth=False)

    def export_status(self, ref: Any, job_id: str) -> Dict[str, Any]:
        return self._c.get("{0}/{1}/export-status/{2}".format(self.base, ref, job_id), auth=False)

    def export_download(self, ref: Any, job_id: str, sink) -> Any:
        return self._c.download("{0}/{1}/export-download/{2}".format(self.base, ref, job_id),
                                sink, auth=False)

    def export_to_file(self, ref: Any, path: str, format: Optional[str] = None,
                       bbox: Optional[str] = None, target_crs: str = "4326",
                       interval: float = 2.0, timeout: Optional[float] = 1800.0,
                       on_status: Optional[Any] = None,
                       on_ready: Optional[Any] = None) -> str:
        """Queue, wait, download. Returns the path written (a zip: an export may be several files).

        Waiting is the caller's time either way — the work is a Celery job — so doing it here keeps
        the common case to one line for both a script and a plugin.

        `on_ready(status)` receives the final status document, whose `truncated` list names any
        file that stopped at the server's row cap. A caller that ignores it gets a file that looks
        complete and is not, so the CLI does not ignore it.
        """
        import time

        from .errors import GeoDeployError

        job_id = (self.export(ref, format, bbox, target_crs) or {}).get("job_id")
        if not job_id:
            raise GeoDeployError("The instance did not return an export job id.")
        started = time.time()
        while True:
            status = self.export_status(ref, job_id) or {}
            state = status.get("status")
            if on_status:
                on_status(state)
            if state == "ready":
                if on_ready:
                    on_ready(status)
                break
            if state in ("error", "failed"):
                raise GeoDeployError("The export failed on the server.")
            if timeout is not None and time.time() - started > timeout:
                raise GeoDeployError(
                    "The export is still running after {0:.0f}s. It continues on the server; "
                    "poll export_status(job_id={1!r}).".format(time.time() - started, job_id))
            time.sleep(interval)
        with open(path, "wb") as fh:
            self.export_download(ref, job_id, fh)
        return path


class VectorLayers(_LayerBase):
    kind = "vector"
    base = "/data/vector"

    def features(self, ref: Any, bbox: Optional[str] = None, limit: int = 50000,
                 public: bool = False) -> Dict[str, Any]:
        """GeoJSON for a viewport. `public=True` uses the unauthenticated `.geojson` route (which
        takes a uid) — useful for checking what a published portal actually serves."""
        params = {"bbox": bbox, "limit": limit}
        if public:
            return self._c.get("/data/vector/{0}/features.geojson".format(ref), params, auth=False)
        return self._c.get("/data/vector/{0}/features".format(int(ref)), params)

    def identify(self, ref: Any, lng: float, lat: float, tol: float = 1e-4,
                 limit: int = 10) -> Any:
        """Attributes of the features under a point — the same call a portal popup makes."""
        return self._c.get("/data/vector/{0}/identify".format(ref),
                           {"lng": lng, "lat": lat, "tol": tol, "limit": limit}, auth=False)

    def tilejson(self, ref: Any) -> Dict[str, Any]:
        return self._c.get("/data/vector/{0}/tilejson".format(ref), auth=False)

    def legend(self, ref: Any) -> Dict[str, Any]:
        """The swatches and labels for this layer's default style, as the SERVER computes them.

        `styles.Style.legend()` produces the same thing locally; this is the authoritative copy —
        the portal draws from the same function — so anything that has to match a published map
        should ask rather than derive.
        """
        return self._c.get("/data/vector/{0}/legend".format(ref), auth=False)

    # -- the whole GeoParquet dataset, straight from storage ---------------------------------------

    def parquet_manifest(self, ref: Any) -> Dict[str, Any]:
        """The partition map of a prepared GeoParquet layer: grid, CRS, columns and file keys.

        Raises `NotFoundError` when the layer has no partitioned dataset — a PostGIS layer, or a
        single `.parquet` uploaded as-is. Callers treat that as "use the export job instead".
        """
        return self._c.get("/data/vector/{0}/parquet/manifest.json".format(ref), auth=False)

    def parquet_parts(self, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every partition file the manifest lists, flattened and in a stable order."""
        parts: List[Dict[str, Any]] = []
        for cell, entries in sorted((manifest.get("cells") or {}).items(),
                                    key=lambda kv: (len(kv[0]), kv[0])):
            for entry in entries or []:
                key = entry.get("key")
                if not key:
                    continue
                if key.startswith("/") or ".." in key.split("/") or ":" in key:
                    # The key becomes a local path. The server is not hostile, but a path from
                    # over the network must not be able to name a file outside the target folder.
                    raise ValidationError(400, "Refusing a suspicious partition key: {0!r}".format(key))
                parts.append({"cell": cell, "key": key, "rows": entry.get("rows")})
        return parts

    def download_dataset(self, ref: Any, directory: str,
                         on_file: Optional[Any] = None) -> Dict[str, Any]:
        """Download a prepared GeoParquet layer WHOLE — manifest plus every partition file.

        This is the complete, lossless, **uncapped** copy: the files are what the instance stores,
        byte for byte, with no worker, no row limit and no format conversion. `layers export` is
        the other path, and the one to use for a clip or another format; it builds a new file and
        stops at the server's row cap, which for exactly these layers (the big ones — GeoParquet is
        where the millions of features live) is a real ceiling.

        The result reads directly in DuckDB or GDAL:

            SELECT * FROM read_parquet('<directory>/**/*.parquet')
        """
        import json as _json
        import os

        manifest = self.parquet_manifest(ref)
        parts = self.parquet_parts(manifest)
        if not parts:
            raise NotFoundError(404, "That GeoParquet dataset lists no partition files.")

        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as fh:
            _json.dump(manifest, fh, indent=1)

        written, done_bytes = [], 0
        for index, part in enumerate(parts):
            dest = os.path.join(directory, *part["key"].split("/"))
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(dest, "wb") as fh:
                self._c.download("/data/vector/{0}/parquet/{1}".format(ref, part["key"]),
                                 fh, auth=False)
            written.append(dest)
            done_bytes += os.path.getsize(dest)
            if on_file:                       # (files done, files total, this part, bytes so far)
                on_file(index + 1, len(parts), part, done_bytes)
        return {"directory": directory, "files": written, "parts": len(written),
                "bytes": done_bytes, "rows": manifest.get("feature_count"),
                "crs": manifest.get("crs")}

    def field_stats(self, ref: Any, field: str, classes: int = 5, method: str = "quantile",
                    ramp: str = "viridis") -> Dict[str, Any]:
        """Distribution of ONE attribute plus a ready-made classification `suggestion`.

        The classification maths lives on the server (`services/symbology.py`) and is shared with
        the editor and the published portal. The CLI asks for the suggestion rather than computing
        breaks itself, so a CLI-styled layer lands in exactly the classes the editor would show —
        two implementations of quantile breaks would eventually disagree, and the disagreement
        would only be visible on a published map.
        """
        return self._c.get("/data/vector/{0}/field-stats".format(ref),
                           {"field": field, "classes": classes, "method": method, "ramp": ramp})

    def tile(self, layer_id: Any) -> Dict[str, Any]:
        """(Re)generate the layer's PMTiles archive — the fallback display path for heavy layers."""
        return self._c.post("/data/vector/{0}/tile".format(int(layer_id)))

    def prepare(self, layer_id: Any) -> Dict[str, Any]:
        """Re-run the GeoParquet spatial prep (partitioning + covering column)."""
        return self._c.post("/data/vector/{0}/prepare".format(int(layer_id)))

    def reprocess(self, layer_id: Any) -> Dict[str, Any]:
        """Restart a stalled/failed layer's background processing without re-uploading it.

        The usual cause is the worker being recreated mid-convert, which leaves the layer stuck at
        whatever percentage it had reached.
        """
        return self._c.post("/data/vector/{0}/reprocess".format(int(layer_id)))


class RasterLayers(_LayerBase):
    kind = "raster"
    base = "/data/raster"

    def stats(self, layer_id: Any) -> Dict[str, Any]:
        """TiTiler statistics plus a suggested 2–98 % `rescale` — the auto-stretch the UI offers."""
        return self._c.get("/data/raster/{0}/stats".format(int(layer_id)))

    def colormaps(self) -> List[str]:
        return self._c.get("/data/raster/colormaps")

    def tilejson(self, ref: Any) -> Dict[str, Any]:
        return self._c.get("/data/raster/{0}/tilejson".format(ref), auth=False)

    def legend(self, ref: Any) -> Dict[str, Any]:
        """A raster legend is a continuous RAMP, so this returns the ingredients to draw one —
        `colormap`, `rescale`, `algorithm`, `bidx` — not a list of swatches."""
        return self._c.get("/data/raster/{0}/legend".format(ref), auth=False)

    def wmts(self, ref: Any) -> str:
        """The WMTS capabilities document — the URL to paste into QGIS, because it is the only one
        of our raster surfaces that carries an extent, so *Zoom to Layer* works."""
        return self._c.get("/data/raster/{0}/wmts".format(ref), auth=False, parse=False).text


class Layers(object):
    """Kind-agnostic helpers: resolve a reference, list both kinds, delete whatever it is."""

    def __init__(self, client: Any):
        self._c = client

    def list(self, kind: Optional[str] = None, **kw: Any) -> List[Dict[str, Any]]:
        out = []  # type: List[Dict[str, Any]]
        if kind in (None, "all", "vector"):
            out += [dict(r, layer_type="vector") for r in self._c.vector.list(**kw)]
        if kind in (None, "all", "raster"):
            out += [dict(r, layer_type="raster") for r in self._c.raster.list(**kw)]
        return out

    def resolve(self, ref: Any, kind: Optional[str] = None) -> Dict[str, Any]:
        """Find a layer from an id, a uid, a `vector-3` style reference, or a name.

        Name matching is exact first, then case-insensitively, then as a unique substring. An
        ambiguous name raises rather than guessing — picking one of two layers called "roads" and
        publishing it is not a mistake the user can see.
        """
        text = str(ref).strip()
        if kind is None:
            for prefix in ("vector-", "raster-"):
                if text.lower().startswith(prefix):
                    kind, text = prefix[:-1], text[len(prefix):]
                    break
        rows = self.list(kind)

        by_uid = [r for r in rows if str(r.get("uid") or "") == text]
        if by_uid:
            return by_uid[0]                               # uids are unique across both kinds
        by_id = [r for r in rows if str(r.get("id")) == text]
        if len(by_id) == 1:
            return by_id[0]
        if len(by_id) > 1:
            # Vector and raster ids are two separate sequences, so "1" can be two different
            # layers. Returning whichever the listing happened to put first is how you download a
            # vector while asking for a raster.
            raise ValidationError(
                400, "Layer id {0} is ambiguous — vector and raster layers are numbered "
                     "separately. Use {1}, or the layer's uid.".format(
                         text, " or ".join(sorted(
                             "{0}-{1}".format(r.get("layer_type"), r.get("id")) for r in by_id))))
        exact = [r for r in rows if (r.get("name") or "") == text]
        if len(exact) == 1:
            return exact[0]
        ci = [r for r in rows if (r.get("name") or "").lower() == text.lower()]
        if len(ci) == 1:
            return ci[0]
        partial = [r for r in rows if text.lower() in (r.get("name") or "").lower()]
        if len(partial) == 1:
            return partial[0]

        candidates = exact or ci or partial
        if len(candidates) > 1:
            names = ", ".join("{0} (id {1}, {2})".format(r.get("name"), r.get("id"),
                                                         r.get("layer_type"))
                              for r in candidates[:8])
            raise ValidationError(400, "{0!r} matches several layers: {1}. Use the id.".format(
                ref, names))
        raise NotFoundError(404, "No layer matching {0!r}.".format(ref))

    def resolve_public(self, ref: Any, kind: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a layer WITHOUT a credential, from the instance's public index.

        Without this, an anonymous client could only ever address a layer by its opaque uid — but
        the public artifacts are readable by anyone, so "download the layer called roads" should
        work for anyone too. Only public layers are visible here, which is the correct limit.
        """
        text = str(ref).strip()
        index = self._c.catalog.public() or {}
        rows = []
        for group, entries in (index.get("layers") or {}).items():
            layer_type = "raster" if group == "raster" else "vector"
            if kind and kind != layer_type:
                continue
            for entry in entries:
                rows.append(dict(entry, layer_type=layer_type, uid=entry.get("id")))

        for row in rows:
            if str(row.get("id")) == text:
                return row
        matches = [r for r in rows if (r.get("name") or "").lower() == text.lower()]
        if len(matches) == 1:
            return matches[0]
        partial = [r for r in rows if text.lower() in (r.get("name") or "").lower()]
        if len(partial) == 1:
            return partial[0]
        if len(matches or partial) > 1:
            raise ValidationError(400, "{0!r} matches several public layers; use the id.".format(ref))
        raise NotFoundError(
            404, "No PUBLIC layer matching {0!r}. Only shared layers are visible without a "
                 "token — log in to reach the rest.".format(ref))

    def api(self, layer_type: str):
        """The namespace for a layer kind — lets kind-agnostic code stay short."""
        if layer_type == "raster":
            return self._c.raster
        if layer_type == "vector":
            return self._c.vector
        raise ValidationError(400, "Unknown layer type {0!r}.".format(layer_type))

    def delete(self, ref: Any, kind: Optional[str] = None) -> Dict[str, Any]:
        layer = self.resolve(ref, kind)
        self.api(layer["layer_type"]).delete(layer["id"])
        return layer
