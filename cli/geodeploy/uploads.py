"""Uploads — one entry point, five routes, chosen for you.

GeoDeploy has five ways in, and picking the wrong one fails in ways that are hard to read:

===========================  ==========================================================
route                        when
===========================  ==========================================================
``vector-api``               .zip/.geojson/.json/.gpkg under the direct-upload threshold
``csv-api``                  a small .csv, with X/Y or WKT columns → PostGIS
``large-vector``             any of the above at or over the threshold → GeoParquet
``geoparquet``               .parquet/.geoparquet, at any size
``raster-api`` / ``raster-large``  .tif/.tiff, small / large
===========================  ==========================================================

**The threshold is 48 MB, not the API's 2 GB.** A file POSTed through the API is buffered by
whatever proxy sits in front of the instance, and Cloudflare's free tier cuts a request body at
100 MB — which surfaced as a bare "Network error" at 1 % with *nothing in the API log*, because the
request never arrived. Above 48 MB everything goes direct-to-storage in 48 MB presigned parts, so
no single request approaches an edge limit whatever the total size. `ui/src/composables/useUpload.js`
makes the same decision with the same numbers; the two must stay in step.
"""
from __future__ import annotations

import csv as _csv
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from .errors import ValidationError
from .transport import MultipartBody, ProgressReader, Request

#: At or above this, bypass the API and upload direct to object storage. See the module docstring.
LARGE_UPLOAD_THRESHOLD = 48 * 1024 * 1024

#: Above this a single presigned PUT is replaced by chunked parts. Must stay <= the server's
#: PART_SIZE (48 MB), which is itself sized to clear a CDN's ~100 MB request-body cap.
CHUNK_THRESHOLD = 48 * 1024 * 1024

#: Parts uploaded at once. Four saturates a normal uplink without making progress unreadable.
PART_CONCURRENCY = 4

#: Retries for ONE part. Parts are the only safely retryable piece of a big upload: each is an
#: idempotent PUT to its own presigned URL, so a reset at 90 % costs one part, not the whole file.
PART_RETRIES = 3

VECTOR_API_EXTENSIONS = frozenset((".zip", ".geojson", ".json", ".gpkg"))
GEOPARQUET_EXTENSIONS = frozenset((".parquet", ".geoparquet"))
LARGE_VECTOR_EXTENSIONS = frozenset(VECTOR_API_EXTENSIONS | {".csv"})
RASTER_EXTENSIONS = frozenset((".tif", ".tiff"))

#: Column names that are almost always coordinates. Used ONLY to offer a default for a CSV that
#: was given no geometry options — the choice is always reported, never silent.
X_NAMES = ("longitude", "lon", "lng", "long", "x", "easting", "east", "xcoord", "x_coord")
Y_NAMES = ("latitude", "lat", "y", "northing", "north", "ycoord", "y_coord")
WKT_NAMES = ("wkt", "geom", "geometry", "the_geom", "wkt_geom", "geometry_wkt")

DELIMITERS = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe", " ": "space"}


class UploadPlan(object):
    """What will happen to one file, decided before a byte moves.

    Separated from the doing so the CLI can show `--dry-run` and a plugin can explain the route to
    a user before committing to a multi-gigabyte transfer.
    """

    __slots__ = ("path", "route", "layer_type", "name", "size", "csv_opts", "chunked", "reason")

    def __init__(self, path: str, route: str, layer_type: str, name: str, size: int,
                 csv_opts: Optional[Dict[str, Any]] = None, chunked: bool = False,
                 reason: str = ""):
        self.path = path
        self.route = route
        self.layer_type = layer_type
        self.name = name
        self.size = size
        self.csv_opts = csv_opts
        self.chunked = chunked
        self.reason = reason

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "route": self.route, "layer_type": self.layer_type,
                "name": self.name, "size": self.size, "chunked": self.chunked,
                "csv_opts": self.csv_opts, "reason": self.reason}


class UploadResult(object):
    """The outcome for one file: which layer it became, and the job that is (or was) building it."""

    __slots__ = ("plan", "job", "layer_id", "job_id", "final")

    def __init__(self, plan: UploadPlan, job: Dict[str, Any]):
        self.plan = plan
        self.job = job or {}
        self.layer_id = self.job.get("layer_id")
        self.job_id = self.job.get("id")
        #: The last status seen when `wait=True`; None when the caller did not wait.
        self.final = None  # type: Optional[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        out = {"file": self.plan.path, "name": self.plan.name, "route": self.plan.route,
               "layer_type": self.plan.layer_type, "layer_id": self.layer_id,
               "job_id": self.job_id, "size": self.plan.size}
        if self.final is not None:
            out["status"] = self.final.get("status")
            out["error_message"] = self.final.get("error_message")
        return out


def detect_layer_type(path: str) -> str:
    return "raster" if os.path.splitext(path)[1].lower() in RASTER_EXTENSIONS else "vector"


def sniff_csv(path: str, sample_bytes: int = 64 * 1024) -> Dict[str, Any]:
    """Header + delimiter of a CSV, and a guess at its geometry columns.

    A guess, offered — never applied silently. The CLI prints "using x=lon, y=lat" so a file whose
    `x`/`y` are something else entirely (a grid reference, a pixel index) is caught by the person
    who knows, not discovered later as a layer sitting off the coast of Ghana.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(sample_bytes)
    delimiter = ","
    try:
        delimiter = _csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except _csv.Error:
        pass
    header = []  # type: List[str]
    for row in _csv.reader(sample.splitlines()[:1], delimiter=delimiter):
        header = [c.strip() for c in row]
        break
    lowered = {c.lower(): c for c in header}
    guess = {"x_column": None, "y_column": None, "wkt_column": None}
    for name in WKT_NAMES:
        if name in lowered:
            guess["wkt_column"] = lowered[name]
            break
    if not guess["wkt_column"]:
        for name in X_NAMES:
            if name in lowered:
                guess["x_column"] = lowered[name]
                break
        for name in Y_NAMES:
            if name in lowered:
                guess["y_column"] = lowered[name]
                break
        if not (guess["x_column"] and guess["y_column"]):
            guess["x_column"] = guess["y_column"] = None
    return {"header": header, "delimiter": DELIMITERS.get(delimiter, "comma"), "guess": guess}


class Uploads(object):
    """Upload files and register them as layers."""

    def __init__(self, client: Any):
        self._c = client

    # ── planning ────────────────────────────────────────────────────────────────────────────────

    def plan(self, path: str, layer_type: Optional[str] = None, name: Optional[str] = None,
             x_column: Optional[str] = None, y_column: Optional[str] = None,
             wkt_column: Optional[str] = None, srid: int = 4326,
             delimiter: Optional[str] = None, guess_csv: bool = True) -> UploadPlan:
        """Decide the route for one file, or raise with a message that says what to do instead."""
        if not os.path.isfile(path):
            raise ValidationError(400, "No such file: {0}".format(path))
        size = os.path.getsize(path)
        if size == 0:
            raise ValidationError(400, "{0} is empty.".format(path))
        ext = os.path.splitext(path)[1].lower()
        kind = layer_type or detect_layer_type(path)
        base = name or os.path.splitext(os.path.basename(path))[0]

        if kind == "raster":
            if ext not in RASTER_EXTENSIONS:
                raise ValidationError(400, "{0} is not a GeoTIFF (.tif/.tiff).".format(path))
            if size >= LARGE_UPLOAD_THRESHOLD:
                return UploadPlan(path, "raster-large", "raster", base, size, chunked=True,
                                  reason="over the {0} MB direct-upload threshold"
                                         .format(LARGE_UPLOAD_THRESHOLD // (1024 * 1024)))
            return UploadPlan(path, "raster-api", "raster", base, size)

        if ext in GEOPARQUET_EXTENSIONS:
            return UploadPlan(path, "geoparquet", "vector", base, size,
                              chunked=size > CHUNK_THRESHOLD,
                              reason="GeoParquet is always uploaded direct to storage")

        if ext not in LARGE_VECTOR_EXTENSIONS:
            raise ValidationError(
                400, "Unsupported file type {0}. Vector: {1}, {2}; raster: {3}.".format(
                    ext or "(none)", ", ".join(sorted(LARGE_VECTOR_EXTENSIONS)),
                    ", ".join(sorted(GEOPARQUET_EXTENSIONS)), ", ".join(sorted(RASTER_EXTENSIONS))))

        csv_opts = None
        if ext == ".csv":
            csv_opts = self._csv_options(path, x_column, y_column, wkt_column, srid, delimiter,
                                         guess_csv)

        if size >= LARGE_UPLOAD_THRESHOLD:
            return UploadPlan(path, "large-vector", "vector", base, size, csv_opts,
                              chunked=size > CHUNK_THRESHOLD,
                              reason="over the {0} MB direct-upload threshold — converts to "
                                     "GeoParquet in the background"
                                     .format(LARGE_UPLOAD_THRESHOLD // (1024 * 1024)))
        if ext == ".csv":
            return UploadPlan(path, "csv-api", "vector", base, size, csv_opts)
        return UploadPlan(path, "vector-api", "vector", base, size)

    def _csv_options(self, path: str, x_column, y_column, wkt_column, srid, delimiter,
                     guess_csv: bool) -> Dict[str, Any]:
        opts = {"x_column": x_column, "y_column": y_column, "wkt_column": wkt_column,
                "srid": int(srid or 4326), "delimiter": delimiter or "comma", "guessed": False}
        if wkt_column or (x_column and y_column):
            return opts
        if not guess_csv:
            raise ValidationError(400, "A CSV needs geometry columns: --x and --y, or --wkt.")
        sniffed = sniff_csv(path)
        guess = sniffed["guess"]
        if not (guess["wkt_column"] or (guess["x_column"] and guess["y_column"])):
            raise ValidationError(
                400, "Could not find geometry columns in {0}. Pass --x/--y or --wkt. "
                     "Columns: {1}".format(os.path.basename(path),
                                           ", ".join(sniffed["header"][:20]) or "(none read)"))
        opts.update(guess)
        opts["guessed"] = True
        if not delimiter:
            opts["delimiter"] = sniffed["delimiter"]
        return opts

    # ── uploading ───────────────────────────────────────────────────────────────────────────────

    def upload(self, path: str, layer_type: Optional[str] = None, name: Optional[str] = None,
               wait: bool = False, on_progress: Optional[Callable[[int, int], None]] = None,
               on_job: Optional[Callable[[Dict[str, Any]], None]] = None,
               cancel: Optional[Callable[[], bool]] = None,
               plan: Optional[UploadPlan] = None, **csv_kw: Any) -> UploadResult:
        """Upload one file and register it. Returns as soon as the ingest job is QUEUED.

        `wait=True` blocks until the job finishes and raises `jobs.JobFailed` if it did not — which
        is what a script wants, since a queued job says nothing about whether the data was readable.
        """
        p = plan or self.plan(path, layer_type, name, **csv_kw)
        route = p.route
        if route == "vector-api":
            job = self._post_file("/data/vector/upload", p, on_progress, cancel)
        elif route == "raster-api":
            job = self._post_file("/data/raster/upload", p, on_progress, cancel)
        elif route == "csv-api":
            job = self._post_file("/data/vector/upload-csv", p, on_progress, cancel,
                                  fields=_csv_fields(p))
        elif route == "geoparquet":
            key = self._to_storage(p, "geoparquet", on_progress, cancel)
            job = self._c.post("/data/vector/geoparquet/complete",
                               {"s3_key": key, "name": p.name, "file_size": p.size})
        elif route == "large-vector":
            key = self._to_storage(p, "large", on_progress, cancel)
            body = {"s3_key": key, "name": p.name, "file_size": p.size}
            if p.csv_opts:
                body.update(_csv_fields(p))
            job = self._c.post("/data/vector/large/complete", body)
        elif route == "raster-large":
            key = self._to_storage(p, "raster", on_progress, cancel)
            job = self._c.post("/data/raster/large/complete",
                               {"s3_key": key, "name": p.name, "file_size": p.size})
        else:  # pragma: no cover - plan() cannot produce anything else
            raise ValidationError(400, "Unknown upload route {0!r}.".format(route))

        result = UploadResult(p, job)
        if wait and result.job_id:
            result.final = self._c.jobs.wait(result.job_id, p.layer_type,
                                             on_progress=on_job)
        return result

    def upload_many(self, paths: Sequence[str], layer_type: Optional[str] = None,
                    wait: bool = False, concurrency: int = 1,
                    on_file: Optional[Callable[[UploadPlan], None]] = None,
                    on_progress: Optional[Callable[[str, int, int], None]] = None,
                    on_job: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                    on_error: Optional[Callable[[str, Exception], None]] = None,
                    stop_on_error: bool = False, **kw: Any) -> List[Any]:
        """Upload several files. Every file is PLANNED first, so a bad argument fails before the
        first byte rather than after the first three uploads.

        Files run sequentially by default: each large file already uses four parallel part uploads,
        and stacking file-level concurrency on top mostly redistributes the same bandwidth while
        making progress output unreadable. Raise `concurrency` for many small files.
        """
        plans = [self.plan(p, layer_type, **kw) for p in paths]
        results = []  # type: List[Any]

        def run(p: UploadPlan):
            if on_file:
                on_file(p)
            return self.upload(
                p.path, plan=p, wait=wait,
                on_progress=(lambda done, total: on_progress(p.path, done, total)) if on_progress else None,
                on_job=(lambda st: on_job(p.path, st)) if on_job else None)

        if concurrency > 1 and len(plans) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(concurrency, len(plans))) as pool:
                futures = [(p, pool.submit(run, p)) for p in plans]
                for p, future in futures:
                    try:
                        results.append(future.result())
                    except Exception as exc:  # noqa: BLE001 - reported per file, not swallowed
                        if on_error:
                            on_error(p.path, exc)
                        results.append(exc)
            return results

        for p in plans:
            try:
                results.append(run(p))
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    on_error(p.path, exc)
                results.append(exc)
                if stop_on_error:
                    break
        return results

    # ── the two transports ──────────────────────────────────────────────────────────────────────

    def _post_file(self, path: str, plan: UploadPlan,
                   on_progress: Optional[Callable[[int, int], None]],
                   cancel: Optional[Callable[[], bool]] = None,
                   fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """multipart/form-data through the API — streamed from disk, never buffered."""
        body = MultipartBody(fields=fields, file_path=plan.path,
                             filename=os.path.basename(plan.path),
                             on_progress=on_progress, cancel=cancel)
        return self._c.request("POST", path, body=body, content_type=body.content_type,
                               timeout=self._c.upload_timeout)

    def _to_storage(self, plan: UploadPlan, kind: str,
                    on_progress: Optional[Callable[[int, int], None]],
                    cancel: Optional[Callable[[], bool]] = None) -> str:
        """Direct-to-storage upload; returns the object key to register. Chunked when big."""
        if plan.chunked:
            return self._chunked(plan, kind, on_progress, cancel)
        endpoint = ("/data/vector/geoparquet/presign" if kind == "geoparquet"
                    else "/data/vector/large/presign")
        pre = self._c.post(endpoint, {"filename": os.path.basename(plan.path),
                                      "name": plan.name, "file_size": plan.size})
        with open(plan.path, "rb") as fh:
            reader = ProgressReader(fh, plan.size, on_progress, cancel)
            response = self._c.send_absolute("PUT", pre["upload_url"], reader,
                                             {"Content-Type": "application/octet-stream"})
        if response.status >= 400:
            from .errors import from_status
            raise from_status(response.status,
                              "Storage rejected the upload: {0}".format(response.text[:300]),
                              response.url)
        return pre["s3_key"]

    def _chunked(self, plan: UploadPlan, kind: str,
                 on_progress: Optional[Callable[[int, int], None]],
                 cancel: Optional[Callable[[], bool]] = None) -> str:
        """initiate → PUT every part (in parallel, with retries) → assemble.

        A failure aborts the multipart upload so the staged parts are not left paying for storage
        on someone's S3 bill.
        """
        base = "/data/raster" if kind == "raster" else "/data/vector"
        init = self._c.post(base + "/upload/multipart/initiate",
                            {"filename": os.path.basename(plan.path), "file_size": plan.size,
                             "kind": kind})
        key, upload_id = init["s3_key"], init["upload_id"]
        part_size, parts = init["part_size"], init["parts"]

        sent = [0] * len(parts)
        lock = threading.Lock()
        results = [None] * len(parts)  # type: List[Optional[Dict[str, Any]]]

        def report():
            if on_progress:
                on_progress(min(sum(sent), plan.size), plan.size)

        def put(index: int) -> None:
            part = parts[index]
            number = int(part["part_number"])
            offset = (number - 1) * part_size
            length = min(part_size, plan.size - offset)
            last_error = None
            for attempt in range(PART_RETRIES):
                if cancel is not None and cancel():
                    raise _Cancelled()
                try:
                    with open(plan.path, "rb") as fh:
                        fh.seek(offset)
                        chunk = _Slice(fh, length)

                        def progress(done, _total, i=index):
                            with lock:
                                sent[i] = done
                            report()

                        reader = ProgressReader(chunk, length, progress, cancel)
                        response = self._c.send_absolute("PUT", part["url"], reader)
                    if response.status < 400:
                        etag = (response.headers.get("etag") or "").strip('"')
                        if not etag:
                            raise ValidationError(
                                502, "Storage accepted part {0} but returned no ETag, so the "
                                     "upload cannot be assembled.".format(number))
                        results[index] = {"part_number": number, "etag": etag}
                        with lock:
                            sent[index] = length
                        report()
                        return
                    last_error = "HTTP {0}: {1}".format(response.status, response.text[:200])
                except _Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - retried below
                    last_error = str(exc)
                with lock:
                    sent[index] = 0
                report()
            raise ValidationError(502, "Part {0} of {1} failed after {2} attempts: {3}".format(
                number, len(parts), PART_RETRIES, last_error))

        try:
            if len(parts) > 1 and PART_CONCURRENCY > 1:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=min(PART_CONCURRENCY, len(parts))) as pool:
                    for _ in pool.map(put, range(len(parts))):
                        pass
            else:
                for index in range(len(parts)):
                    put(index)
            self._c.post(base + "/upload/multipart/complete",
                         {"s3_key": key, "upload_id": upload_id,
                          "parts": [r for r in results if r]})
        except BaseException:
            try:
                self._c.post(base + "/upload/multipart/abort",
                             {"s3_key": key, "upload_id": upload_id})
            except Exception:  # noqa: BLE001 - the original failure is what matters  # nosec B110 - intentional: a cosmetic failure must not take down the layer
                pass
            raise
        return key


class _Cancelled(Exception):
    """Internal: a cancel callback said stop. Turned into a TransportError by the caller."""


class _Slice(object):
    """A read-only window onto an open file — one multipart part, without copying it."""

    def __init__(self, fh, length: int):
        self._fh = fh
        self._left = length

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        want = self._left if size is None or size < 0 else min(size, self._left)
        chunk = self._fh.read(want)
        self._left -= len(chunk)
        return chunk


def _csv_fields(plan: UploadPlan) -> Dict[str, Any]:
    """The CSV geometry options, in the shape both CSV routes expect (form fields / JSON body)."""
    opts = dict(plan.csv_opts or {})
    opts.pop("guessed", None)
    fields = {"name": plan.name, "srid": opts.get("srid", 4326),
              "delimiter": opts.get("delimiter", "comma")}
    for key in ("x_column", "y_column", "wkt_column"):
        if opts.get(key):
            fields[key] = opts[key]
    return fields
