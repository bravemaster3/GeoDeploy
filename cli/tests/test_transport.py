"""The wire: streamed bodies, retries, and turning a status into the right exception."""
from __future__ import annotations

import io
import os

import pytest

from geodeploy import errors
from geodeploy.transport import (MultipartBody, ProgressReader, Request, Response,
                                 UrllibTransport, _RetryStatus)


class TestMultipartBody:
    def test_length_matches_what_it_produces(self, tmp_path):
        path = tmp_path / "roads.gpkg"
        path.write_bytes(b"x" * 5000)
        body = MultipartBody(fields={"name": "Roads", "srid": 4326}, file_path=str(path))

        produced = b""
        while True:
            chunk = body.read(512)
            if not chunk:
                break
            produced += chunk
        # Content-Length is computed BEFORE reading; if the two disagree the request hangs or the
        # server sees a truncated body, which is the worst kind of upload bug to debug.
        assert len(produced) == len(body)

    def test_carries_fields_and_the_file(self, tmp_path):
        from tests.conftest import parse_multipart
        path = tmp_path / "sites.csv"
        path.write_bytes(b"lon,lat\n17.6,59.8\n")
        body = MultipartBody(fields={"x_column": "lon", "y_column": "lat", "srid": 4326,
                                     "skipped": None},
                             file_path=str(path))
        raw = b""
        while True:
            chunk = body.read(64)
            if not chunk:
                break
            raw += chunk

        parsed = parse_multipart(body.content_type, raw)
        assert parsed["x_column"] == "lon"
        assert parsed["y_column"] == "lat"
        assert "skipped" not in parsed          # a None field is absent, not the string "None"
        assert parsed["_files"]["file"][0] == "sites.csv"
        assert parsed["_files"]["file"][1] == b"lon,lat\n17.6,59.8\n"

    def test_reads_the_file_lazily(self, tmp_path):
        """A 2 GB upload must not become a 2 GB string; the file is opened only when reached."""
        path = tmp_path / "big.bin"
        path.write_bytes(b"y" * 4096)
        body = MultipartBody(file_path=str(path))
        assert body._fh is None
        body.read(16)
        assert len(body) == len(body._pre) + 4096 + len(body._post)

    def test_progress_is_reported_as_it_is_read(self, tmp_path):
        path = tmp_path / "f.geojson"
        path.write_bytes(b"z" * 2048)
        seen = []
        body = MultipartBody(file_path=str(path), on_progress=lambda done, total: seen.append(done))
        while body.read(256):
            pass
        assert seen == sorted(seen)             # monotonic
        assert seen[-1] == len(body)


class TestProgressReader:
    def test_counts_what_the_socket_took(self):
        source = io.BytesIO(b"a" * 1000)
        seen = []
        reader = ProgressReader(source, 1000, lambda done, total: seen.append((done, total)))
        while reader.read(256):
            pass
        assert seen[-1] == (1000, 1000)

    def test_cancel_stops_mid_flight(self):
        source = io.BytesIO(b"a" * 1000)
        state = {"stop": False}
        reader = ProgressReader(source, 1000, cancel=lambda: state["stop"])
        assert reader.read(100)
        state["stop"] = True
        with pytest.raises(errors.TransportError):
            reader.read(100)


class _ScriptedTransport(object):
    """Returns canned responses; records every request. Enough to test the client's own logic."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def _json_response(status, payload, url="http://x/api/thing"):
    import json as _json
    return Response(status, {"Content-Type": "application/json"},
                    _json.dumps(payload).encode(), url)


class TestErrorMapping:
    """Every status a caller might branch on becomes a class, not a number to compare."""

    @pytest.mark.parametrize("status,expected", [
        (400, errors.ValidationError),
        (401, errors.AuthError),
        (403, errors.PermissionError_),
        (404, errors.NotFoundError),
        (409, errors.ConflictError),
        (413, errors.ValidationError),
        (422, errors.ValidationError),
        (500, errors.ServerError),
        (503, errors.ServerError),
    ])
    def test_status_to_class(self, status, expected):
        from geodeploy.client import Client
        client = Client("https://gd.example.org", token="gdp_x",
                        transport=_ScriptedTransport([_json_response(status, {"detail": "no"})]))
        with pytest.raises(expected):
            client.get("/anything")

    def test_missing_scope_is_extractable(self):
        from geodeploy.client import Client
        client = Client("https://gd.example.org", token="gdp_x", transport=_ScriptedTransport(
            [_json_response(403, {"detail": "Token missing scope: data:write"})]))
        with pytest.raises(errors.PermissionError_) as caught:
            client.get("/data/vector")
        assert caught.value.missing_scope == "data:write"

    def test_validation_lists_become_one_readable_line(self):
        from geodeploy.client import Client
        payload = {"detail": [{"loc": ["body", "access_type"], "msg": "string does not match"}]}
        client = Client("https://gd.example.org", token="gdp_x",
                        transport=_ScriptedTransport([_json_response(422, payload)]))
        with pytest.raises(errors.ValidationError) as caught:
            client.post("/portals", {})
        assert "access_type" in caught.value.detail
        assert "string does not match" in caught.value.detail

    def test_an_html_proxy_page_becomes_a_sentence(self):
        from geodeploy.client import Client
        html = Response(413, {"Content-Type": "text/html"},
                        b"<html><head><title>413</title></head></html>", "http://x")
        client = Client("https://gd.example.org", token="gdp_x",
                        transport=_ScriptedTransport([html]))
        with pytest.raises(errors.ValidationError) as caught:
            client.post("/data/vector/upload")
        assert "too large" in caught.value.detail.lower()
        assert "<html>" not in caught.value.detail


class TestRetries:
    def test_gateway_errors_are_retried(self):
        transport = UrllibTransport(retries=2, backoff=0)
        attempts = {"n": 0}

        def fake_send_once(request):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _RetryStatus(Response(503, {}, b"", request.url))
            return Response(200, {"Content-Type": "application/json"}, b"{}", request.url)

        transport._send_once = fake_send_once
        assert transport.send(Request("GET", "http://x/api/thing")).status == 200
        assert attempts["n"] == 3

    def test_a_streamed_body_is_never_retried(self, tmp_path):
        """The file object is already partly consumed, so a replay would upload a truncated file."""
        transport = UrllibTransport(retries=3, backoff=0)
        attempts = {"n": 0}

        def fake_send_once(request):
            attempts["n"] += 1
            raise _RetryStatus(Response(503, {}, b"", request.url))

        transport._send_once = fake_send_once
        path = tmp_path / "f.bin"
        path.write_bytes(b"data")
        with open(str(path), "rb") as fh:
            response = transport.send(Request("PUT", "http://x/s3/key", body=fh))
        assert response.status == 503
        assert attempts["n"] == 1

    def test_a_client_error_is_not_retried(self):
        transport = UrllibTransport(retries=3, backoff=0)
        attempts = {"n": 0}

        def fake_send_once(request):
            attempts["n"] += 1
            return Response(404, {}, b"", request.url)

        transport._send_once = fake_send_once
        assert transport.send(Request("GET", "http://x/api/thing")).status == 404
        assert attempts["n"] == 1


class TestResponse:
    def test_headers_are_case_insensitive(self):
        response = Response(200, {"ETag": '"abc"'}, b"", "http://x")
        assert response.headers["etag"] == '"abc"'

    def test_empty_body_is_none_not_a_crash(self):
        from geodeploy.client import Client
        client = Client("https://gd.example.org", token="gdp_x",
                        transport=_ScriptedTransport([Response(204, {}, b"", "http://x")]))
        assert client.delete("/portals/1") is None
