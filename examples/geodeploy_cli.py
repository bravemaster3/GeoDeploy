#!/usr/bin/env python3
"""GeoDeploy CLI — a thin reference client for the GeoDeploy API using a scoped API token (A-03).

It exercises the real endpoints the GeoLibre / QGIS plugins build on: authenticate, list layers and
portals, upload a dataset, and open a portal in edit mode (get config -> edit -> put) or publish it.
Deliberately dependency-light (just `requests`) so it's easy to lift into a plugin.

Setup:
    pip install requests
    export GEODEPLOY_URL=http://127.0.0.1            # your instance origin (use 127.0.0.1, not
                                                     # localhost: on Windows/WSL2 localhost stalls on IPv6)
    export GEODEPLOY_TOKEN=gdp_xxxxxxxxxxxxxxxxxxxx  # mint one in Settings -> API tokens

Examples:
    python geodeploy_cli.py whoami
    python geodeploy_cli.py layers
    python geodeploy_cli.py upload roads.gpkg --poll
    python geodeploy_cli.py portals
    python geodeploy_cli.py portal-get 3 > portal3.json   # dump editable config (incl. layer_configs styles)
    python geodeploy_cli.py portal-set 3 portal3.json      # push edits back
    python geodeploy_cli.py publish 3

The token's scopes gate what works: e.g. a `portal:publish`-only token can `publish` but not `upload`
(the API returns 403 "Token missing scope: data:write").
"""
import argparse
import json
import os
import sys
import time

import requests

BASE = os.environ.get("GEODEPLOY_URL", "http://localhost").rstrip("/")
TOKEN = os.environ.get("GEODEPLOY_TOKEN", "")


def _headers():
    if not TOKEN:
        sys.exit("Set GEODEPLOY_TOKEN (mint one in Settings -> API tokens).")
    return {"Authorization": f"Bearer {TOKEN}"}


def _call(method, path, **kw):
    r = requests.request(method, f"{BASE}/api{path}", headers=_headers(), timeout=120, **kw)
    if r.status_code >= 400:
        detail = r.json().get("detail") if "application/json" in r.headers.get("content-type", "") else r.text
        sys.exit(f"HTTP {r.status_code}: {detail}")
    return r


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def whoami(_):
    _print(_call("GET", "/auth/me").json())


def layers(args):
    _print(_call("GET", "/data/raster" if args.raster else "/data/vector").json())


def portals(_):
    _print([{"id": p["id"], "title": p["title"], "published": p.get("published"),
             "access_type": p.get("access_type")} for p in _call("GET", "/portals").json()])


def portal_get(args):
    _print(_call("GET", f"/portals/{args.id}").json())


def portal_set(args):
    with open(args.config, encoding="utf-8") as f:
        body = json.load(f)
    _print(_call("PUT", f"/portals/{args.id}", json=body).json())


def publish(args):
    _print(_call("POST", f"/portals/{args.id}/publish").json())


def unpublish(args):
    _print(_call("POST", f"/portals/{args.id}/unpublish").json())


def upload(args):
    with open(args.file, "rb") as f:
        job = _call("POST", "/data/vector/upload", files={"file": (os.path.basename(args.file), f)}).json()
    _print(job)
    if not args.poll:
        return
    job_id = job.get("job_id") or job.get("id")
    while True:
        time.sleep(3)
        st = _call("GET", f"/data/vector/jobs/{job_id}").json()
        print(f"  status: {st.get('status')}  {st.get('message', '')}", file=sys.stderr)
        if st.get("status") in ("ready", "completed", "failed", "error"):
            _print(st)
            break


def main():
    p = argparse.ArgumentParser(description="GeoDeploy API reference CLI (scoped token auth).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="show the authenticated user").set_defaults(func=whoami)
    lp = sub.add_parser("layers", help="list vector (or --raster) layers")
    lp.add_argument("--raster", action="store_true")
    lp.set_defaults(func=layers)
    sub.add_parser("portals", help="list portals").set_defaults(func=portals)

    gp = sub.add_parser("portal-get", help="dump a portal's editable config")
    gp.add_argument("id", type=int)
    gp.set_defaults(func=portal_get)
    spp = sub.add_parser("portal-set", help="PUT a portal config from a JSON file")
    spp.add_argument("id", type=int)
    spp.add_argument("config")
    spp.set_defaults(func=portal_set)

    for name, fn in (("publish", publish), ("unpublish", unpublish)):
        ap = sub.add_parser(name, help=f"{name} a portal")
        ap.add_argument("id", type=int)
        ap.set_defaults(func=fn)

    up = sub.add_parser("upload", help="upload a vector file (.gpkg/.geojson/.zip/.json)")
    up.add_argument("file")
    up.add_argument("--poll", action="store_true", help="poll the ingest job to completion")
    up.set_defaults(func=upload)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
