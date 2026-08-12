"""`geodeploy layers …` — the data on an instance.

A layer can be named by id, uid or name everywhere, because typing `roads` is what a person does
and looking up `7` is what a machine does.
"""
from __future__ import annotations

import json
import sys

from ...errors import GeoDeployError, ValidationError
from ..main import add_command, group_parser
from ..output import EXIT_GENERIC, EXIT_OK, human_size
from ._common import (add_style_args, confirm, layer_ref_arg, parse_fields, resolve_layer,
                      style_from_args, write_json_file)

LIST_COLUMNS = ["layer_type", "id", "name", "status", "geometry_type", "feature_count",
                "storage_backend", "visibility", "created_by"]


def register(subparsers) -> None:
    group = group_parser(subparsers, "layers", "list, inspect, style, share and delete data layers",
                         aliases=["data"])

    listing = add_command(group, "list", cmd_list, "list layers", aliases=["ls"])
    listing.add_argument("--type", dest="kind", choices=["vector", "raster", "all"], default="all")
    listing.add_argument("--status", help="only layers in this state (ready, processing, error)")
    listing.add_argument("--visibility", choices=["private", "organization", "public"])
    listing.add_argument("--query", dest="search",
                         help="match name, abstract or keywords")

    show = add_command(group, "show", cmd_show, "everything known about one layer")
    layer_ref_arg(show)

    rename = add_command(group, "rename", cmd_rename, "change a layer's display name")
    layer_ref_arg(rename)
    rename.add_argument("name")

    share = add_command(group, "share", cmd_share,
                        "set visibility and catalog metadata (public = STAC + OGC + raw asset)")
    layer_ref_arg(share)
    share.add_argument("--visibility", choices=["private", "organization", "public"])
    share.add_argument("--abstract")
    share.add_argument("--license")
    share.add_argument("--attribution")
    share.add_argument("--keywords", help="comma-separated")

    style = add_command(group, "style", cmd_style,
                        "set a layer's DEFAULT styling — what a portal starts from",
                        epilog="""\
examples:
  geodeploy layers style roads --color '#e11d48' --line-width 2
  geodeploy layers style sites --marker star --radius 6 --outline-color none
  geodeploy layers style parcels --color-field pop --classify jenks --classes 6 --ramp magma
  geodeploy layers style dem --colormap terrain --rescale 0,2400
""")
    layer_ref_arg(style)
    style.add_argument("--popup-fields", help="comma-separated attribute names for popups")
    style.add_argument("--replace", action="store_true",
                       help="replace the whole style instead of merging into it")
    add_style_args(style)

    fields = add_command(group, "fields", cmd_fields, "the attribute columns of a vector layer")
    layer_ref_arg(fields)

    stats = add_command(group, "stats", cmd_stats,
                        "distribution of one attribute (vector) or band statistics (raster)")
    layer_ref_arg(stats)
    stats.add_argument("--field", help="attribute to summarise (vector layers)")
    stats.add_argument("--classes", type=int, default=5)
    stats.add_argument("--method", choices=["quantile", "equal", "jenks"], default="quantile")
    stats.add_argument("--ramp", default="viridis")

    links = add_command(group, "links", cmd_links,
                        "share URLs for this layer, labelled by the tool each one suits")
    layer_ref_arg(links)

    usage = add_command(group, "usage", cmd_usage, "which portals include this layer")
    layer_ref_arg(usage)

    features = add_command(group, "features", cmd_features, "GeoJSON features from a vector layer")
    layer_ref_arg(features)
    features.add_argument("--bbox", help="minx,miny,maxx,maxy in EPSG:4326")
    features.add_argument("--limit", type=int, default=1000)
    features.add_argument("-o", "--output", help="write to a file (UTF-8) instead of stdout")

    identify = add_command(group, "identify", cmd_identify,
                           "attributes of the features under a point")
    layer_ref_arg(identify)
    identify.add_argument("lng", type=float)
    identify.add_argument("lat", type=float)
    identify.add_argument("--tolerance", type=float, default=1e-4)
    identify.add_argument("--limit", type=int, default=10)

    download = add_command(group, "download", cmd_download, "download a layer as a file",
                           epilog="""examples:
  geodeploy layers download roads                 the whole layer, GeoPackage
  geodeploy layers download roads --format csv
  geodeploy layers download parcels --format geoparquet --crs native
  geodeploy layers download roads --bbox 11,55,12,56    just that area
  geodeploy layers download dem                   the Cloud-Optimized GeoTIFF itself

gpkg / csv / geojson / geoparquet are built by the instance as a job and arrive as a zip. cog and
pmtiles are the stored file, streamed as-is.
""")
    layer_ref_arg(download)
    download.add_argument("-o", "--output", help="output path (default: the layer name)")
    download.add_argument("--format",
                          choices=["auto", "gpkg", "csv", "geojson", "geoparquet", "cog",
                                   "pmtiles"],
                          default="auto",
                          help="auto = GeoPackage for a vector layer, the COG for a raster")
    download.add_argument("--bbox", help="minx,miny,maxx,maxy in EPSG:4326 (default: everything)")
    download.add_argument("--crs", choices=["4326", "native"], default="4326",
                          help="native keeps the layer's own CRS where the format can carry it")

    tile = add_command(group, "tile", cmd_tile, "(re)generate a vector layer's PMTiles archive")
    layer_ref_arg(tile)
    tile.add_argument("--wait", action="store_true")

    prepare = add_command(group, "prepare", cmd_prepare, "re-run GeoParquet spatial preparation")
    layer_ref_arg(prepare)
    prepare.add_argument("--wait", action="store_true")

    reprocess = add_command(group, "reprocess", cmd_reprocess,
                            "restart a stalled or failed layer without re-uploading it")
    layer_ref_arg(reprocess)
    reprocess.add_argument("--wait", action="store_true")

    delete = add_command(group, "delete", cmd_delete,
                         "delete a layer (and remove it from every portal that used it)",
                         aliases=["rm"])
    layer_ref_arg(delete)
    delete.add_argument("--yes", action="store_true", help="skip the confirmation")

    add_command(group, "colormaps", cmd_colormaps, "raster colormaps this instance offers")


# ── read ─────────────────────────────────────────────────────────────────────────────────────────

def cmd_list(ctx, args) -> int:
    rows = ctx.client().layers.list(args.kind, status=args.status, query=args.search,
                                    visibility=args.visibility)
    if not ctx.out.json_mode:
        for row in rows:
            if row.get("status") == "processing" and row.get("progress") is not None:
                row["status"] = "{0}% {1}".format(row["progress"], row.get("current_step") or "")
    ctx.out.render(rows, LIST_COLUMNS, empty="No layers yet — `geodeploy upload <file>`.")
    return EXIT_OK


def cmd_show(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    if not ctx.out.json_mode:
        layer = dict(layer)
        if layer.get("file_size"):
            layer["file_size"] = human_size(layer["file_size"])
    ctx.out.render(layer)
    return EXIT_OK


def cmd_fields(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    columns = layer.get("columns") or []
    if not columns:
        ctx.out.info("No column information for this layer (rasters have bands, not columns).")
        return EXIT_OK
    ctx.out.render(columns)
    return EXIT_OK


def cmd_stats(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    client = ctx.client()
    if layer["layer_type"] == "raster":
        ctx.out.render(client.raster.stats(layer["id"]))
        return EXIT_OK
    if not args.field:
        ctx.out.error("Which attribute? Pass --field (see `geodeploy layers fields`).")
        return EXIT_GENERIC
    stats = client.vector.field_stats(layer["id"], args.field, classes=args.classes,
                                      method=args.method, ramp=args.ramp)
    if ctx.out.json_mode:
        ctx.out.json(stats)
        return EXIT_OK
    ctx.out.record({k: v for k, v in stats.items() if k not in ("suggestion", "categories")})
    suggestion = stats.get("suggestion") or {}
    if suggestion.get("classes"):
        ctx.out.out("")
        ctx.out.table([{"min": c.get("min"), "max": c.get("max"), "color": c.get("color")}
                       for c in suggestion["classes"]], ["min", "max", "color"])
    elif stats.get("categories"):
        ctx.out.out("")
        ctx.out.table(stats["categories"][:25])
    return EXIT_OK


def cmd_links(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    data = ctx.client().layers.api(layer["layer_type"]).links(layer["id"])
    if ctx.out.json_mode:
        ctx.out.json(data)
        return EXIT_OK
    if not data.get("public"):
        ctx.out.warn("This layer is not public, so these URLs 404 for everyone else. "
                     "`geodeploy layers share {0} --visibility public` opens it up."
                     .format(layer["id"]))
    ctx.out.table(data.get("links") or [], ["label", "url", "hint"])
    return EXIT_OK


def cmd_usage(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    ctx.out.render(ctx.client().layers.api(layer["layer_type"]).usage(layer["id"]),
                   ["id", "title", "published"], empty="Not used by any portal.")
    return EXIT_OK


def cmd_features(ctx, args) -> int:
    layer = resolve_layer(ctx, args, )
    if layer["layer_type"] != "vector":
        ctx.out.error("Features are a vector thing; {0} is a raster.".format(layer["name"]))
        return EXIT_GENERIC
    data = ctx.client().vector.features(layer["id"], bbox=args.bbox, limit=args.limit)
    if args.output:
        write_json_file(args.output, data)
        ctx.out.success("Wrote {0} ({1} features).".format(
            args.output, len(data.get("features") or [])))
        return EXIT_OK
    ctx.out.out(json.dumps(data, ensure_ascii=False))
    return EXIT_OK


def cmd_identify(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    ref = layer.get("uid") or layer["id"]
    ctx.out.render(ctx.client().vector.identify(ref, args.lng, args.lat, args.tolerance,
                                                args.limit))
    return EXIT_OK


def cmd_download(ctx, args) -> int:
    """Two very different mechanisms behind one command.

    A COG or a PMTiles archive already IS a file: it streams straight out of storage. A GeoPackage
    or a CSV has to be built, so the instance does it as a job and hands back a zip. `auto` picks
    the one that gives you the data rather than a rendering of it.
    """
    layer = resolve_layer(ctx, args, public_ok=True)
    client = ctx.client(auth_required=False)
    ref = layer.get("uid") or layer["id"]
    api = client.layers.api(layer["layer_type"])
    kind = args.format
    if kind == "auto":
        kind = "cog" if layer["layer_type"] == "raster" else "gpkg"

    if kind in ("cog", "pmtiles"):
        if kind == "cog" and layer["layer_type"] != "raster":
            ctx.out.error("A COG is a raster thing; {0} is a vector layer.".format(layer["name"]))
            return EXIT_GENERIC
        path = args.output or "{0}.{1}".format(_safe(layer["name"]),
                                               "tif" if kind == "cog" else "pmtiles")
        endpoint = ("/data/raster/{0}/cog" if kind == "cog"
                    else "/data/vector/{0}/pmtiles").format(ref)
        progress = ctx.out.progress(path)
        try:
            with open(path, "wb") as fh:
                client.download(endpoint, _Counting(fh, progress), auth=False)
        except GeoDeployError:
            progress.finish()
            raise
        progress.finish()
        ctx.out.render({"ok": True, "file": path, "format": kind})
        if not ctx.out.json_mode:
            ctx.out.success("Wrote {0}.".format(path))
        return EXIT_OK

    # Built server-side: the result is a zip, because an export can be more than one file (and
    # carries a MANIFEST.txt saying whether the row cap truncated it).
    path = args.output or "{0}.zip".format(_safe(layer["name"]))
    ctx.out.info("Building {0} on the instance{1}…".format(
        kind, " for " + args.bbox if args.bbox else " (whole layer)"))
    api.export_to_file(ref, path, format=kind, bbox=args.bbox, target_crs=args.crs,
                       on_status=lambda state: ctx.out.debug("export: {0}".format(state)))
    ctx.out.render({"ok": True, "file": path, "format": kind})
    if not ctx.out.json_mode:
        ctx.out.success("Wrote {0}.".format(path))
        ctx.out.info("Read MANIFEST.txt inside it if the layer is large — it records the row cap.")
    return EXIT_OK


class _Counting(object):
    """Wraps the output file so a download can show progress without buffering it."""

    def __init__(self, fh, progress):
        self._fh = fh
        self._progress = progress
        self._done = 0

    def write(self, chunk: bytes) -> int:
        self._done += len(chunk)
        self._progress.update(self._done)
        return self._fh.write(chunk)


def cmd_colormaps(ctx, args) -> int:
    ctx.out.render(ctx.client().raster.colormaps())
    return EXIT_OK


# ── write ────────────────────────────────────────────────────────────────────────────────────────

def cmd_rename(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    updated = ctx.client().layers.api(layer["layer_type"]).rename(layer["id"], args.name)
    ctx.out.render(updated, ["id", "name", "status"])
    if not ctx.out.json_mode:
        ctx.out.success("Renamed to {0!r}.".format(args.name))
        ctx.out.info("Published portals keep the old name until they are re-published.")
    return EXIT_OK


def cmd_share(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    updated = ctx.client().layers.api(layer["layer_type"]).share(
        layer["id"], visibility=args.visibility, abstract=args.abstract, license=args.license,
        attribution=args.attribution, keywords=args.keywords)
    ctx.out.render(updated, ["id", "name", "visibility", "is_public", "license", "attribution",
                             "keywords", "abstract"])
    if not ctx.out.json_mode and args.visibility == "public":
        ctx.out.success("{0} is public — it now appears in the STAC catalog and OGC API - Features."
                        .format(updated.get("name")))
        ctx.out.info("`geodeploy layers links {0}` lists the URLs to hand out.".format(layer["id"]))
    return EXIT_OK


def cmd_style(ctx, args) -> int:
    layer = resolve_layer(ctx, args)
    client = ctx.client()
    current = (layer.get("default_style") or {})
    base = {} if args.replace else dict(current.get("style") or {})
    style = style_from_args(args, client, layer.get("uid") or layer["id"], base, ctx.out)

    body = {"style": style,
            "opacity": args.opacity if args.opacity is not None else current.get("opacity", 1.0)}
    fields = parse_fields(args.popup_fields)
    if layer["layer_type"] == "vector":
        body["popup_fields"] = fields if fields is not None else (current.get("popup_fields") or [])
    else:
        # The raster default-style body is flat: colormap/rescale/algorithm live at the top level.
        body = {"opacity": body["opacity"]}
        for key in ("colormap", "rescale", "algorithm", "zfactor", "bidx"):
            if style.get(key) is not None:
                body[key] = style[key]

    updated = client.layers.api(layer["layer_type"]).set_default_style(layer["id"], body)
    ctx.out.render(updated.get("default_style") or body)
    if not ctx.out.json_mode:
        ctx.out.success("Default style saved for {0}.".format(layer["name"]))
        ctx.out.info("Portals already using this layer keep their own styling; re-publish to "
                     "refresh a portal that follows the default.")
    return EXIT_OK


def cmd_tile(ctx, args) -> int:
    return _job_command(ctx, args, "tile")


def cmd_prepare(ctx, args) -> int:
    return _job_command(ctx, args, "prepare")


def cmd_reprocess(ctx, args) -> int:
    return _job_command(ctx, args, "reprocess")


def _job_command(ctx, args, action: str) -> int:
    layer = resolve_layer(ctx, args)
    if layer["layer_type"] != "vector":
        ctx.out.error("`{0}` applies to vector layers.".format(action))
        return EXIT_GENERIC
    client = ctx.client()
    result = getattr(client.vector, action)(layer["id"])
    job_id = (result or {}).get("id") if isinstance(result, dict) else None
    if args.wait and job_id:
        final = client.jobs.wait(job_id, "vector",
                                 on_progress=lambda st: ctx.out.info("  {0:3d}%  {1}".format(
                                     st.get("progress") or 0, st.get("current_step") or "")))
        ctx.out.render(final)
        return EXIT_OK
    ctx.out.render(result)
    if not ctx.out.json_mode:
        ctx.out.success("{0} started for {1}.".format(action.capitalize(), layer["name"]))
    return EXIT_OK


def cmd_delete(ctx, args) -> int:
    client = ctx.client()
    layer = resolve_layer(ctx, args)
    portals = client.layers.api(layer["layer_type"]).usage(layer["id"])
    if portals and not ctx.out.json_mode:
        ctx.out.warn("Used by {0} portal(s): {1}. They will be updated and re-published."
                     .format(len(portals), ", ".join(p.get("title") or "?" for p in portals)))
    if not confirm(ctx.out, "Delete {0} layer {1!r} (id {2})?".format(
            layer["layer_type"], layer["name"], layer["id"]), args.yes):
        ctx.out.info("Nothing deleted.")
        return EXIT_GENERIC
    client.layers.api(layer["layer_type"]).delete(layer["id"])
    ctx.out.render({"ok": True, "deleted": layer["id"], "name": layer["name"]})
    if not ctx.out.json_mode:
        ctx.out.success("Deleted {0}.".format(layer["name"]))
    return EXIT_OK


def _safe(name: str) -> str:
    keep = "-_. abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cleaned = "".join(c if c in keep else "_" for c in (name or "layer")).strip()
    return cleaned or "layer"
