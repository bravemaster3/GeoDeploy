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
    python geodeploy_cli.py portal-get 3 portal3.json     # dump editable config (incl. layer_configs styles)
    python geodeploy_cli.py portal-set 3 portal3.json      # push edits back
    python geodeploy_cli.py portal-add-layer 3 6           # add layer 6 to portal 3 (type auto-detected)
    python geodeploy_cli.py portal-add-layer 3 6 --color "#e11d48" --radius 4 --opacity 0.8   # with styling
    python geodeploy_cli.py portal-remove-layer 3 6        # remove it again
    python geodeploy_cli.py set-description 3 "About this map…"   # or @about.md — drives the About page
    python geodeploy_cli.py layer-set-sharing 6 --visibility public --license "CC-BY-4.0"
    python geodeploy_cli.py publish 3                      # (re)publish to make edits live

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


def _get_json(path):
    """Best-effort GET → parsed JSON, or None on any error (for lookups that shouldn't hard-fail)."""
    try:
        r = requests.get(f"{BASE}/api{path}", headers=_headers(), timeout=120)
        return r.json() if r.status_code < 400 else None
    except Exception:
        return None


def _detect_layer_type(layer_id):
    for path, kind in (("/data/vector", "vector"), ("/data/raster", "raster")):
        for layer in (_get_json(path) or []):
            if layer.get("id") == layer_id:
                return kind
    return None


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _layer_summary(configs):
    # Top of the list = drawn on top (layer_configs[0]).
    return [{"layer_id": c["layer_id"], "layer_type": c["layer_type"]} for c in configs]


def whoami(_):
    _print(_call("GET", "/auth/me").json())


def layers(args):
    _print(_call("GET", "/data/raster" if args.raster else "/data/vector").json())


def portals(_):
    _print([{"id": p["id"], "title": p["title"], "published": p.get("published"),
             "access_type": p.get("access_type")} for p in _call("GET", "/portals").json()])


def portal_get(args):
    data = _call("GET", f"/portals/{args.id}").json()
    if args.out:  # write clean UTF-8 ourselves (PowerShell's `>` writes UTF-16+BOM, which breaks portal-set)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        _print(data)  # stdout — capture with `> file.json` on bash/WSL (UTF-8), or pass an outfile above


def portal_set(args):
    with open(args.config, encoding="utf-8-sig") as f:  # utf-8-sig tolerates a BOM from any editor/shell
        body = json.load(f)
    _print(_call("PUT", f"/portals/{args.id}", json=body).json())


def portal_add_layer(args):
    ltype = args.type or _detect_layer_type(args.layer_id)
    if not ltype:
        sys.exit(f"Layer {args.layer_id} not found among vector/raster layers — pass --type explicitly "
                 "(needs a token with data:read to auto-detect).")
    # Fold only the formatting flags the user actually set into the layer_config's `style` dict; any
    # key left out keeps the layer's default styling. (arg name -> style key; argparse turns the
    # hyphenated flags into underscored attributes, e.g. --fill-opacity -> args.fill_opacity.)
    style = {}
    for attr, key in (("color", "color"), ("fill_opacity", "fill_opacity"),
                      ("outline_color", "outline_color"), ("line_width", "line_width"),
                      ("radius", "radius"), ("marker", "marker"), ("line_type", "lineType"),
                      ("colormap", "colormap"), ("rescale", "rescale")):
        val = getattr(args, attr, None)
        if val is not None:
            style[key] = val
    portal = _call("GET", f"/portals/{args.portal_id}").json()
    configs = portal.get("layer_configs", [])
    if any(c["layer_id"] == args.layer_id and c["layer_type"] == ltype for c in configs):
        sys.exit(f"Layer {args.layer_id} ({ltype}) is already in portal {args.portal_id}.")
    entry = {"layer_id": args.layer_id, "layer_type": ltype,
             "visible": not args.hidden, "opacity": args.opacity, "style": style, "popup_fields": []}
    configs = configs + [entry] if args.bottom else [entry] + configs  # default: top of the layer list
    out = _call("PUT", f"/portals/{args.portal_id}", json={"layer_configs": configs}).json()
    print(f"Added layer {args.layer_id} ({ltype}) to portal {args.portal_id} — publish to make it live:\n"
          f"    python geodeploy_cli.py publish {args.portal_id}", file=sys.stderr)
    _print(_layer_summary(out.get("layer_configs", [])))


def portal_remove_layer(args):
    portal = _call("GET", f"/portals/{args.portal_id}").json()
    configs = portal.get("layer_configs", [])
    kept = [c for c in configs
            if not (c["layer_id"] == args.layer_id and (not args.type or c["layer_type"] == args.type))]
    if len(kept) == len(configs):
        sys.exit(f"Layer {args.layer_id} not in portal {args.portal_id}.")
    out = _call("PUT", f"/portals/{args.portal_id}", json={"layer_configs": kept}).json()
    print(f"Removed layer {args.layer_id} from portal {args.portal_id} — publish to apply.", file=sys.stderr)
    _print(_layer_summary(out.get("layer_configs", [])))


def set_description(args):
    text = args.text
    if text.startswith("@"):  # @file → read the About text (markdown ok) from a file
        with open(text[1:], encoding="utf-8-sig") as f:
            text = f.read()
    out = _call("PUT", f"/portals/{args.portal_id}", json={"description": text}).json()
    print(f"Set description on portal {args.portal_id} ({len(text)} chars) — publish to update the About page:\n"
          f"    python geodeploy_cli.py publish {args.portal_id}", file=sys.stderr)
    _print({"id": out["id"], "title": out["title"], "description": out.get("description")})


def layer_set_sharing(args):
    ltype = args.type or _detect_layer_type(args.layer_id)
    if not ltype:
        sys.exit(f"Layer {args.layer_id} not found — pass --type (needs data:read to auto-detect).")
    body = {attr: getattr(args, attr) for attr in
            ("visibility", "abstract", "license", "attribution", "keywords")
            if getattr(args, attr, None) is not None}
    if not body:
        sys.exit("Nothing to set — pass --visibility and/or a metadata flag "
                 "(--abstract/--license/--attribution/--keywords).")
    out = _call("PUT", f"/data/{ltype}/{args.layer_id}/sharing", json=body).json()
    print(f"Updated sharing on {ltype} layer {args.layer_id} — re-publish any portal using it to apply.",
          file=sys.stderr)
    _print({k: out.get(k) for k in
            ("id", "name", "visibility", "is_public", "abstract", "license", "attribution", "keywords")})


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
    last = None
    while True:
        time.sleep(2)
        st = _call("GET", f"/data/vector/jobs/{job_id}").json()
        line = f"  {st.get('progress', 0):3d}%  {st.get('current_step') or st.get('status')}"
        if line != last:  # only print when progress or step changes — no wall of repeats
            print(line, file=sys.stderr)
            last = line
        if st.get("status") in ("ready", "completed", "failed", "error"):
            if st.get("error_message"):
                print(f"  error: {st['error_message']}", file=sys.stderr)
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

    gp = sub.add_parser("portal-get", help="dump a portal's editable config (to stdout, or to a file)")
    gp.add_argument("id", type=int)
    gp.add_argument("out", nargs="?", help="optional output file (written as UTF-8); omit to print to stdout")
    gp.set_defaults(func=portal_get)
    spp = sub.add_parser("portal-set", help="PUT a portal config from a JSON file")
    spp.add_argument("id", type=int)
    spp.add_argument("config")
    spp.set_defaults(func=portal_set)

    al = sub.add_parser("portal-add-layer", help="add a data layer to a portal (top of the list)")
    al.add_argument("portal_id", type=int)
    al.add_argument("layer_id", type=int)
    al.add_argument("--type", choices=["vector", "raster", "external"],
                    help="layer type (auto-detected from your layers if omitted)")
    al.add_argument("--bottom", action="store_true", help="add at the bottom of the layer list, not the top")
    # ── Formatting (only the flags you pass are set; the rest keep their defaults) ──
    al.add_argument("--opacity", type=float, default=1.0, help="layer opacity 0-1 (default 1.0)")
    al.add_argument("--hidden", action="store_true", help="start the layer hidden")
    al.add_argument("--color", help="main colour, hex e.g. #e11d48 (polygon fill / line / point)")
    al.add_argument("--fill-opacity", type=float, help="polygon fill opacity 0-1")
    al.add_argument("--outline-color", help="polygon/line outline colour, hex")
    al.add_argument("--line-width", type=float, help="line width in px")
    al.add_argument("--radius", type=float, help="point radius in px")
    al.add_argument("--marker", choices=["circle", "square", "triangle", "diamond", "star", "cross"],
                    help="point marker shape")
    al.add_argument("--line-type", choices=["solid", "dashed", "dotted"], help="line style")
    al.add_argument("--colormap", help="raster colormap name, e.g. viridis")
    al.add_argument("--rescale", help="raster stretch 'min,max'")
    al.set_defaults(func=portal_add_layer)

    rl = sub.add_parser("portal-remove-layer", help="remove a layer from a portal")
    rl.add_argument("portal_id", type=int)
    rl.add_argument("layer_id", type=int)
    rl.add_argument("--type", choices=["vector", "raster", "external"])
    rl.set_defaults(func=portal_remove_layer)

    sd = sub.add_parser("set-description", help="set a portal's About text (drives the published About page)")
    sd.add_argument("portal_id", type=int)
    sd.add_argument("text", help="the About text, or @file.md to read it from a file (markdown ok)")
    sd.set_defaults(func=set_description)

    ss = sub.add_parser("layer-set-sharing", help="set a layer's visibility + catalog metadata")
    ss.add_argument("layer_id", type=int)
    ss.add_argument("--type", choices=["vector", "raster"], help="auto-detected if omitted")
    ss.add_argument("--visibility", choices=["private", "organization", "public"],
                    help="public opts the layer into the STAC catalog + data links (shows on the About page)")
    ss.add_argument("--abstract")
    ss.add_argument("--license")
    ss.add_argument("--attribution")
    ss.add_argument("--keywords", help="comma-separated")
    ss.set_defaults(func=layer_set_sharing)

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
