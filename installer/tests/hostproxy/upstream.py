"""A stand-in for GeoDeploy's ingress on 127.0.0.1:8080, the way behind-proxy mode publishes it.

It answers `/api/public/whoami` with the `instance` key Apply's routing check looks for, and echoes
what arrived — Host, X-Forwarded-Proto, X-Forwarded-For — so a test can see what the proxy in front
actually forwarded rather than what its configuration says it should.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def _answer(self):
        body = json.dumps({
            "geodeploy": True,
            "instance": "fake-instance-0001",
            "host": self.headers.get("Host"),
            "xfp": self.headers.get("X-Forwarded-Proto"),
            "xff": self.headers.get("X-Forwarded-For"),
            "path": self.path,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer

    def do_POST(self):
        # Drain the body, so an upload test measures the proxy in front rather than this stub.
        remaining = int(self.headers.get("Content-Length") or 0)
        while remaining > 0:
            remaining -= len(self.rfile.read(min(remaining, 65536)) or b"x" * remaining)
        self._answer()

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
