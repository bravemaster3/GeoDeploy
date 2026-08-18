"""HTTP transport — stdlib only, and swappable.

Two things this module exists for:

1. **No dependencies.** `urllib.request` sends every request this client makes, including a 10 GB
   presigned PUT, because a file object passed as the body is streamed by `http.client` in 8 KB
   blocks rather than read into memory.

2. **A seam for a host that has its own network stack.** A QGIS plugin should go through
   `QgsNetworkAccessManager` so it inherits the user's proxy, their CA bundle and their
   authentication configuration — none of which urllib knows about. Anything with a
   `send(Request) -> Response` method can be passed as `Client(transport=…)`, so that plugin
   supplies ~30 lines and reuses every other line in this package.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, Optional, Union

from .errors import TransportError

#: Bytes read per chunk when streaming a file body. Matches http.client's own block size, so a
#: progress callback fires at the same rate the socket is actually fed.
BLOCK = 64 * 1024


class Request:
    """One HTTP request, fully resolved: absolute URL, final headers, body ready to send."""

    __slots__ = ("method", "url", "headers", "body", "timeout")

    def __init__(self, method: str, url: str, headers: Optional[Dict[str, str]] = None,
                 body: Union[bytes, io.IOBase, None] = None, timeout: Optional[float] = None):
        self.method = method.upper()
        self.url = url
        self.headers = dict(headers or {})
        #: bytes, or a file-like object with `read(n)` (streamed — never loaded whole).
        self.body = body
        self.timeout = timeout

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Request {0} {1}>".format(self.method, self.url)


class Response:
    """One HTTP answer. `content` is always bytes; decoding is the caller's business."""

    __slots__ = ("status", "headers", "content", "url")

    def __init__(self, status: int, headers: Dict[str, str], content: bytes, url: str = ""):
        self.status = status
        #: Lower-cased keys — HTTP header names are case-insensitive and callers should not have to
        #: remember whether this instance's nginx wrote `ETag` or `etag`.
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.content = content
        self.url = url

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        if not self.content:
            return None
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Response {0} {1}>".format(self.status, self.url)


class ProgressReader(io.RawIOBase):
    """A file wrapper that reports how much of it has been sent.

    Wrapping the READER rather than counting before the request is what makes the number honest:
    it advances as the socket drains, so a stalled upload stops moving instead of showing 100 %
    while the connection hangs.
    """

    def __init__(self, fh, total: int, on_progress: Optional[Callable[[int, int], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None):
        self._fh = fh
        self._total = total
        self._sent = 0
        self._on_progress = on_progress
        self._cancel = cancel

    def read(self, size: int = -1) -> bytes:  # noqa: D102 - file protocol
        if self._cancel is not None and self._cancel():
            # http.client turns this into a failed request, which is what a cancel should look
            # like: the upload stops mid-flight and the caller gets an exception, not a silent
            # truncated object on the far end.
            raise TransportError("Upload cancelled.")
        chunk = self._fh.read(BLOCK if size is None or size < 0 else size)
        if chunk:
            self._sent += len(chunk)
            if self._on_progress:
                self._on_progress(self._sent, self._total)
        return chunk

    def readable(self) -> bool:
        return True

    def __len__(self) -> int:
        return self._total


class MultipartBody(io.RawIOBase):
    """`multipart/form-data` built as a STREAM, so a 2 GB upload never enters memory.

    `urllib` will happily take a bytes body, and every "upload a file" example does exactly that —
    which is fine until the file is a GeoPackage. This reads the parts in order (preamble, file
    from disk, epilogue) and reports a real `Content-Length`, which the API needs since it does not
    accept chunked transfer.
    """

    def __init__(self, fields: Optional[Dict[str, Any]] = None,
                 file_field: str = "file", file_path: Optional[str] = None,
                 filename: Optional[str] = None, content_type: str = "application/octet-stream",
                 boundary: Optional[str] = None,
                 on_progress: Optional[Callable[[int, int], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None):
        self.boundary = boundary or ("gd" + os.urandom(16).hex())
        self._path = file_path
        self._fh = None
        self._on_progress = on_progress
        self._cancel = cancel
        self._sent = 0

        pre = io.BytesIO()
        for key, value in (fields or {}).items():
            if value is None:
                continue
            pre.write(self._dash())
            pre.write('Content-Disposition: form-data; name="{0}"\r\n\r\n'.format(key).encode())
            pre.write(str(value).encode("utf-8"))
            pre.write(b"\r\n")
        if file_path is not None:
            pre.write(self._dash())
            pre.write(
                'Content-Disposition: form-data; name="{0}"; filename="{1}"\r\n'
                .format(file_field, filename or os.path.basename(file_path)).encode("utf-8"))
            pre.write("Content-Type: {0}\r\n\r\n".format(content_type).encode())
        self._pre = pre.getvalue()
        self._post = b"\r\n" + self._dash(final=True) if file_path is not None else self._dash(final=True)
        self._file_size = os.path.getsize(file_path) if file_path is not None else 0
        self._stage = 0  # 0 preamble, 1 file, 2 epilogue, 3 done
        self._offset = 0

    def _dash(self, final: bool = False) -> bytes:
        return "--{0}{1}\r\n".format(self.boundary, "--" if final else "").encode()

    @property
    def content_type(self) -> str:
        return "multipart/form-data; boundary={0}".format(self.boundary)

    def __len__(self) -> int:
        return len(self._pre) + self._file_size + len(self._post)

    def read(self, size: int = -1) -> bytes:  # noqa: D102 - file protocol
        if self._cancel is not None and self._cancel():
            raise TransportError("Upload cancelled.")
        want = BLOCK if size is None or size < 0 else size
        if self._stage == 0:
            chunk = self._pre[self._offset:self._offset + want]
            self._offset += len(chunk)
            if self._offset >= len(self._pre):
                self._stage, self._offset = (1 if self._path else 2), 0
            return self._advance(chunk)
        if self._stage == 1:
            if self._fh is None:
                self._fh = open(self._path, "rb")
            chunk = self._fh.read(want)
            if not chunk:
                self._fh.close()
                self._fh = None
                self._stage, self._offset = 2, 0
                return self.read(size)
            return self._advance(chunk)
        if self._stage == 2:
            chunk = self._post[self._offset:self._offset + want]
            self._offset += len(chunk)
            if self._offset >= len(self._post):
                self._stage = 3
            return self._advance(chunk)
        return b""

    def _advance(self, chunk: bytes) -> bytes:
        self._sent += len(chunk)
        if self._on_progress and chunk:
            self._on_progress(min(self._sent, len(self)), len(self))
        return chunk

    def readable(self) -> bool:
        return True

    def close(self) -> None:  # pragma: no cover - cleanup path
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None
        super().close()


class _MethodRequest(urllib.request.Request):
    """urllib picks GET/POST from whether there is a body; PUT and DELETE need saying out loud."""

    def __init__(self, *args, **kwargs):
        self._method = kwargs.pop("method_", "GET")
        urllib.request.Request.__init__(self, *args, **kwargs)

    def get_method(self) -> str:
        return self._method


class UrllibTransport:
    """The default transport: `urllib.request`, no dependencies.

    Retries are deliberately narrow — connection-level failures and 502/503/504, which mean the
    request did not run or the gateway gave up. A 4xx is never retried (it will fail identically),
    and neither is a non-idempotent request that actually reached the app.
    """

    #: Statuses worth a second attempt: an nginx/gateway answer, not the application's.
    RETRY_STATUS = (502, 503, 504)

    def __init__(self, verify_tls: bool = True, retries: int = 2, backoff: float = 0.75,
                 ca_bundle: Optional[str] = None):
        self.retries = max(0, int(retries))
        self.backoff = backoff
        if verify_tls:
            self._ctx = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None
        else:
            # For a self-signed instance on a lab network. The CLI only reaches this via an
            # explicit --insecure, and it says so on every run.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ctx = ctx
        # No cookie jar and no redirect-following for anything but GET: a 307 on a streamed upload
        # cannot be replayed (the file object is already partly consumed), so it must surface.
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self._ctx)
                                                   if self._ctx else urllib.request.HTTPSHandler())

    def send(self, request: Request) -> Response:
        streamed = not isinstance(request.body, (bytes, bytearray, type(None)))
        attempts = 1 if streamed else self.retries + 1
        last_exc = None  # type: Optional[Exception]
        for attempt in range(attempts):
            try:
                return self._send_once(request)
            except TransportError as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    raise
                time.sleep(self.backoff * (2 ** attempt))
            except _RetryStatus as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    return exc.response
                time.sleep(self.backoff * (2 ** attempt))
        raise last_exc if last_exc else TransportError("Request failed")  # pragma: no cover

    def _send_once(self, request: Request) -> Response:
        headers = dict(request.headers)
        body = request.body
        if body is not None and not isinstance(body, (bytes, bytearray)):
            # Streamed body: urllib will not guess a length for a file object, and without
            # Content-Length http.client falls back to chunked encoding, which S3 presigned PUTs
            # reject outright (the signature covers the exact length).
            length = len(body) if hasattr(body, "__len__") else None
            if length is not None:
                headers.setdefault("Content-Length", str(length))
        req = _MethodRequest(request.url, data=body, headers=headers, method_=request.method)
        try:
            with self._opener.open(req, timeout=request.timeout) as resp:
                content = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    content = gzip.decompress(content)
                out = Response(resp.status, dict(resp.headers), content, request.url)
        except urllib.error.HTTPError as exc:
            content = exc.read() or b""
            if (exc.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    content = gzip.decompress(content)
                except OSError:  # pragma: no cover - a lying header
                    pass
            out = Response(exc.code, dict(exc.headers or {}), content, request.url)
            if exc.code in self.RETRY_STATUS:
                raise _RetryStatus(out)
            return out
        except urllib.error.URLError as exc:
            raise TransportError(_reason(exc.reason, request.url)) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise TransportError("Timed out talking to {0}".format(request.url)) from exc
        except ConnectionError as exc:  # reset mid-body, broken pipe on a big upload
            raise TransportError("Connection lost talking to {0}: {1}".format(request.url, exc)) from exc
        return out

    def stream(self, request: Request, sink, chunk: int = BLOCK) -> Response:
        """GET straight to a writable file object — for downloads that must not enter memory."""
        req = _MethodRequest(request.url, data=None, headers=dict(request.headers),
                             method_=request.method)
        try:
            with self._opener.open(req, timeout=request.timeout) as resp:
                read = 0
                while True:
                    block = resp.read(chunk)
                    if not block:
                        break
                    sink.write(block)
                    read += len(block)
                return Response(resp.status, dict(resp.headers), b"", request.url)
        except urllib.error.HTTPError as exc:
            return Response(exc.code, dict(exc.headers or {}), exc.read() or b"", request.url)
        except urllib.error.URLError as exc:
            raise TransportError(_reason(exc.reason, request.url)) from exc


class _RetryStatus(Exception):
    """Internal: a gateway status that `send` may retry, carrying the response if it will not."""

    def __init__(self, response: Response):
        self.response = response
        Exception.__init__(self, "HTTP {0}".format(response.status))


def _reason(reason: Any, url: str) -> str:
    """Turn urllib's reason object into something that names the instance that failed.

    "[Errno 11001] getaddrinfo failed" tells a user nothing; which URL could not be reached tells
    them they typed the host wrong, which is the actual cause most of the time.
    """
    if isinstance(reason, ssl.SSLError):
        return ("TLS error talking to {0}: {1}. If this is a self-signed instance, pass --insecure."
                .format(url, reason))
    return "Could not reach {0}: {1}".format(url, reason)


def iter_chunks(paths: Iterable[str]) -> Iterable[bytes]:  # pragma: no cover - reserved
    """Placeholder kept out of the public API on purpose; multipart uploads read per part."""
    raise NotImplementedError
