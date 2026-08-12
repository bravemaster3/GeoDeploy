"""`geodeploy portals …` — build, style and publish a portal.

The one thing worth internalising: **editing a portal changes a draft.** The live site keeps
serving its previous bundle until `geodeploy portals publish`. Every command here that changes
something says so, because "I set the colour and nothing happened" is otherwise the first thing
anyone hits.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ...errors import ValidationError
from ...portals import ACCESS_TYPES, ARCHETYPES
from ...styles import describe
from ..main import add_command, group_parser
from ..output import EXIT_GENERIC, EXIT_OK
from ._common import (add_style_args, confirm, parse_fields, read_json_arg, read_text_arg,
                      style_from_args, write_json_file)

LIST_COLUMNS = ["id", "title", "slug", "published", "access_type", "template_id", "created_by"]


def register(subparsers) -> None:
    group = group_parser(subparsers, "portals", "create, arrange, style and publish portals",
                         aliases=["portal"])

    listing = add_command(group, "list", cmd_list, "list portals", aliases=["ls"])
    listing.add_argument("--published", action="store_true", help="only published portals")
    listing.add_argument("--draft", action="store_true", help="only unpublished portals")
    listing.add_argument("--query", dest="search", help="match title or slug")

    show = add_command(group, "show", cmd_show, "a portal's settings and layers")
    show.add_argument("portal", help="portal id, slug or title")

    create = add_command(group, "create", cmd_create, "create a portal",
                         epilog="""\
examples:
  geodeploy portals create "Field sites 2026"
  geodeploy portals create "Catalogue" --experience catalog --access organization
  geodeploy portals create "Story" --experience storymap --template minimal
""")
    create.add_argument("title")
    create.add_argument("--description", help="About text, or @file.md")
    create.add_argument("--template", default="minimal", dest="template_id",
                        help="template id (see `geodeploy catalog templates`)")
    create.add_argument("--experience", choices=ARCHETYPES,
                        help="webmap (default), storymap or catalog")
    create.add_argument("--access", choices=ACCESS_TYPES, default="public",
                        help="who may view the PUBLISHED portal")
    create.add_argument("--password", help="the password, when --access password")
    create.add_argument("--publish", action="store_true", help="publish it immediately")

    update = add_command(group, "update", cmd_update, "change a portal's settings")
    update.add_argument("portal")
    update.add_argument("--title")
    update.add_argument("--description", help="About text, or @file.md (Markdown)")
    update.add_argument("--template", dest="template_id")
    update.add_argument("--access", choices=ACCESS_TYPES)
    update.add_argument("--password")
    update.add_argument("--basemap", help="basemap id (see `geodeploy catalog basemaps`)")
    update.add_argument("--view", help='start view as JSON: {"center":[lng,lat],"zoom":6,'
                                       '"pitch":0,"bearing":0}')
    update.add_argument("--theme", help="theme JSON, e.g. '{\"mode\":\"dark\",\"accent\":\"#22c55e\"}'")
    update.add_argument("--layout", help="layout_config JSON (archetype, regions, panels)")

    export = add_command(group, "export", cmd_export,
                         "write a portal's whole editable configuration to a JSON file")
    export.add_argument("portal")
    export.add_argument("output", nargs="?", help="file to write (default: stdout)")

    imp = add_command(group, "import", cmd_import,
                      "push a configuration file back onto a portal")
    imp.add_argument("portal")
    imp.add_argument("config", help="the JSON file written by `portals export`")

    layers = add_command(group, "layers", cmd_layers, "the layers on a portal, top of the list first")
    layers.add_argument("portal")

    add_layer = add_command(group, "add-layer", cmd_add_layer,
                            "add a data layer to a portal (top of the list = drawn on top)",
                            epilog="""\
examples:
  geodeploy portals add-layer 3 roads
  geodeploy portals add-layer 3 sites --marker star --radius 6 --color '#e11d48'
  geodeploy portals add-layer 3 parcels --color-field population --classify quantile --classes 5
  geodeploy portals add-layer 3 dem --colormap terrain --rescale 0,2400 --bottom
""")
    add_layer.add_argument("portal")
    add_layer.add_argument("layer", help="layer id, uid or name")
    add_layer.add_argument("--type", dest="layer_type", choices=["vector", "raster"])
    add_layer.add_argument("--bottom", action="store_true", help="add at the bottom of the list")
    add_layer.add_argument("--hidden", action="store_true", help="start with the layer switched off")
    add_layer.add_argument("--popup-fields", help="comma-separated attributes to show in popups")
    add_layer.add_argument("--replace", action="store_true",
                           help="update the entry if the layer is already on the portal")
    add_layer.add_argument("--publish", action="store_true", help="publish afterwards")
    add_style_args(add_layer)

    remove = add_command(group, "remove-layer", cmd_remove_layer, "take a layer off a portal")
    remove.add_argument("portal")
    remove.add_argument("layer")
    remove.add_argument("--type", dest="layer_type", choices=["vector", "raster"])
    remove.add_argument("--publish", action="store_true")

    style = add_command(group, "style", cmd_style, "restyle a layer ON a portal")
    style.add_argument("portal")
    style.add_argument("layer")
    style.add_argument("--type", dest="layer_type", choices=["vector", "raster"])
    style.add_argument("--visible", dest="visible", action="store_true", default=None)
    style.add_argument("--hidden", dest="visible", action="store_false",
                       help="hide the layer without removing it")
    style.add_argument("--popup-fields", help="comma-separated attributes for popups")
    style.add_argument("--replace", action="store_true", help="replace rather than merge the style")
    style.add_argument("--publish", action="store_true")
    add_style_args(style)

    move = add_command(group, "move-layer", cmd_move_layer, "reorder a layer (0 = top of the list)")
    move.add_argument("portal")
    move.add_argument("layer")
    move.add_argument("position", help="top | bottom | up | down | an index")
    move.add_argument("--type", dest="layer_type", choices=["vector", "raster"])
    move.add_argument("--publish", action="store_true")

    groups = add_command(group, "folders", cmd_folders,
                         "read or replace the layer folder tree (V-13 groups)")
    groups.add_argument("portal")
    groups.add_argument("--set", dest="tree", help="a JSON array, or @file.json")
    groups.add_argument("--clear", action="store_true", help="remove all folders (layers stay)")
    groups.add_argument("--publish", action="store_true")

    describe_p = add_command(group, "set-description", cmd_description,
                             "set the About text (Markdown; drives the published About page)")
    describe_p.add_argument("portal")
    describe_p.add_argument("text", help="the text, or @file.md")
    describe_p.add_argument("--publish", action="store_true")

    asset = add_command(group, "asset", cmd_asset, "upload a logo or About-page image")
    asset.add_argument("portal")
    asset.add_argument("file")

    publish = add_command(group, "publish", cmd_publish, "publish (or re-publish) a portal")
    publish.add_argument("portal")

    unpublish = add_command(group, "unpublish", cmd_unpublish, "take a portal offline")
    unpublish.add_argument("portal")

    url = add_command(group, "url", cmd_url, "print the public URL of a portal")
    url.add_argument("portal")

    delete = add_command(group, "delete", cmd_delete, "delete a portal", aliases=["rm"])
    delete.add_argument("portal")
    delete.add_argument("--yes", action="store_true")

    download = add_command(group, "download-area", cmd_download_area,
                           "download portal layers clipped to a bounding box")
    download.add_argument("portal")
    download.add_argument("bbox", help="minx,miny,maxx,maxy in EPSG:4326")
    download.add_argument("-o", "--output", default="export.zip")
    download.add_argument("--format", default="geojson",
                          choices=["geojson", "gpkg", "csv", "tif"],
                          help="vector format (rasters always come as tif)")
    download.add_argument("--layers", help="comma-separated layer ids; default every layer")
    download.add_argument("--crs", default="4326", choices=["4326", "native"])


# ── read ─────────────────────────────────────────────────────────────────────────────────────────

def cmd_list(ctx, args) -> int:
    published = True if args.published else (False if args.draft else None)
    rows = ctx.client().portals.list(published=published, query=args.search)
    ctx.out.render(rows, LIST_COLUMNS, empty="No portals yet — `geodeploy portals create <title>`.")
    return EXIT_OK


def cmd_show(ctx, args) -> int:
    portal = ctx.client().portals.get(args.portal)
    if ctx.out.json_mode:
        ctx.out.json(portal)
        return EXIT_OK
    ctx.out.record(portal, ["id", "title", "slug", "template_id", "access_type", "basemap",
                            "published", "published_at", "created_by"])
    ctx.out.out("")
    _print_layers(ctx, portal)
    ctx.out.out("")
    ctx.out.info("URL: {0}".format(ctx.client().portals.url(portal)))
    return EXIT_OK


def cmd_layers(ctx, args) -> int:
    portal = ctx.client().portals.get(args.portal)
    if ctx.out.json_mode:
        ctx.out.json(portal.get("layer_configs") or [])
        return EXIT_OK
    _print_layers(ctx, portal)
    return EXIT_OK


def _print_layers(ctx, portal: Dict[str, Any]) -> None:
    configs = portal.get("layer_configs") or []
    if not configs:
        ctx.out.info("No layers on this portal yet.")
        return
    names = {}
    try:
        for row in ctx.client().layers.list():
            names[(row["layer_type"], row["id"])] = row.get("name")
    except Exception:  # noqa: BLE001 - names are decoration; never fail the listing for them
        pass
    rows = []
    for index, entry in enumerate(configs):
        rows.append({
            "#": index,
            "layer_id": entry.get("layer_id"),
            "type": entry.get("layer_type"),
            "name": names.get((entry.get("layer_type"), entry.get("layer_id")), "—"),
            "visible": entry.get("visible", True),
            "opacity": entry.get("opacity", 1.0),
            "style": describe(entry.get("style") or {}),
        })
    ctx.out.table(rows, ["#", "layer_id", "type", "name", "visible", "opacity", "style"])
    ctx.out.info("Index 0 is the top of the layer list, and draws on top.")


def cmd_export(ctx, args) -> int:
    portal = ctx.client().portals.get(args.portal)
    if args.output:
        write_json_file(args.output, portal)
        ctx.out.success("Wrote {0}.".format(args.output))
        ctx.out.info("Edit it and push it back with `geodeploy portals import {0} {1}`."
                     .format(args.portal, args.output))
        return EXIT_OK
    ctx.out.out(json.dumps(portal, indent=2, ensure_ascii=False, default=str))
    return EXIT_OK


def cmd_import(ctx, args) -> int:
    config = read_json_arg("@" + args.config, "the configuration file")
    portal = ctx.client().portals.get(args.portal)
    updated = ctx.client().portals.set_config(portal["id"], config)
    ctx.out.render(updated, LIST_COLUMNS)
    if not ctx.out.json_mode:
        ctx.out.success("Configuration applied to {0}.".format(updated.get("title")))
        _publish_hint(ctx, updated)
    return EXIT_OK


def cmd_url(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    url = client.portals.url(portal)
    if ctx.out.json_mode:
        ctx.out.json({"id": portal["id"], "slug": portal.get("slug"), "url": url,
                      "published": portal.get("published")})
        return EXIT_OK
    ctx.out.out(url)
    if not portal.get("published"):
        ctx.out.warn("Not published yet, so that URL is not live.")
    return EXIT_OK


# ── write ────────────────────────────────────────────────────────────────────────────────────────

def cmd_create(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.create(
        args.title, description=read_text_arg(args.description) if args.description else None,
        template_id=args.template_id, access_type=args.access, access_password=args.password,
        archetype=args.experience)
    if args.publish:
        portal = client.portals.publish(portal["id"])
    ctx.out.render(portal, LIST_COLUMNS)
    if not ctx.out.json_mode:
        ctx.out.success("Created portal {0} ({1}).".format(portal["id"], portal.get("slug")))
        if args.publish:
            ctx.out.info("Published: {0}".format(client.portals.url(portal)))
        else:
            ctx.out.info("Add layers with `geodeploy portals add-layer {0} <layer>`, then "
                         "`geodeploy portals publish {0}`.".format(portal["id"]))
    return EXIT_OK


def cmd_update(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    fields = {"title": args.title, "template_id": args.template_id, "access_type": args.access,
              "access_password": args.password, "basemap": args.basemap}
    if args.description is not None:
        fields["description"] = read_text_arg(args.description)
    if args.view:
        fields["initial_view"] = read_json_arg(args.view, "--view")
    if args.theme:
        fields["theme"] = read_json_arg(args.theme, "--theme")
    if args.layout:
        fields["layout_config"] = read_json_arg(args.layout, "--layout")
    updated = client.portals.update(portal["id"], **fields)
    ctx.out.render(updated, LIST_COLUMNS)
    if not ctx.out.json_mode:
        ctx.out.success("Updated {0}.".format(updated.get("title")))
        _publish_hint(ctx, updated)
    return EXIT_OK


def cmd_description(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    text = read_text_arg(args.text)
    updated = client.portals.update(portal["id"], description=text)
    _maybe_publish(ctx, updated, args)
    ctx.out.render({"id": updated["id"], "title": updated["title"],
                    "description_chars": len(text)})
    if not ctx.out.json_mode:
        ctx.out.success("About text set ({0} characters).".format(len(text)))
        _publish_hint(ctx, updated, args)
    return EXIT_OK


def cmd_add_layer(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    layer = client.layers.resolve(args.layer, args.layer_type)
    style = style_from_args(args, client, layer.get("uid") or layer["id"], {}, ctx.out)
    if not style and (layer.get("default_style") or {}).get("style"):
        style = dict(layer["default_style"]["style"])   # inherit the layer's own default

    updated = client.portals.add_layer(
        portal["id"], layer["id"], layer["layer_type"], style=style,
        visible=not args.hidden, opacity=args.opacity if args.opacity is not None else 1.0,
        popup_fields=parse_fields(args.popup_fields), bottom=args.bottom, replace=args.replace)
    _maybe_publish(ctx, updated, args)
    if ctx.out.json_mode:
        ctx.out.json(updated.get("layer_configs") or [])
        return EXIT_OK
    ctx.out.success("Added {0} ({1}) to {2}.".format(layer["name"], layer["layer_type"],
                                                     updated.get("title")))
    _print_layers(ctx, updated)
    _publish_hint(ctx, updated, args)
    return EXIT_OK


def cmd_remove_layer(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    layer = client.layers.resolve(args.layer, args.layer_type)
    updated = client.portals.remove_layer(portal["id"], layer["id"], layer["layer_type"])
    _maybe_publish(ctx, updated, args)
    if ctx.out.json_mode:
        ctx.out.json(updated.get("layer_configs") or [])
        return EXIT_OK
    ctx.out.success("Removed {0} from {1}.".format(layer["name"], updated.get("title")))
    _publish_hint(ctx, updated, args)
    return EXIT_OK


def cmd_style(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    layer = client.layers.resolve(args.layer, args.layer_type)
    current = {}
    for entry in portal.get("layer_configs") or []:
        if entry.get("layer_id") == layer["id"] and entry.get("layer_type") == layer["layer_type"]:
            current = dict(entry.get("style") or {})
            break
    style = style_from_args(args, client, layer.get("uid") or layer["id"],
                            {} if args.replace else current, ctx.out)
    updated = client.portals.set_layer_style(
        portal["id"], layer["id"], style, layer_type=layer["layer_type"], merge=False,
        visible=args.visible, opacity=args.opacity, popup_fields=parse_fields(args.popup_fields))
    _maybe_publish(ctx, updated, args)
    if ctx.out.json_mode:
        ctx.out.json(style)
        return EXIT_OK
    ctx.out.success("{0}: {1}".format(layer["name"], describe(style)))
    _publish_hint(ctx, updated, args)
    return EXIT_OK


def cmd_move_layer(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    layer = client.layers.resolve(args.layer, args.layer_type)
    updated = client.portals.move_layer(portal["id"], layer["id"], args.position,
                                        layer["layer_type"])
    _maybe_publish(ctx, updated, args)
    if ctx.out.json_mode:
        ctx.out.json(updated.get("layer_configs") or [])
        return EXIT_OK
    _print_layers(ctx, updated)
    _publish_hint(ctx, updated, args)
    return EXIT_OK


def cmd_folders(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    if args.clear:
        updated = client.portals.set_groups(portal["id"], [])
    elif args.tree:
        tree = json.loads(read_text_arg(args.tree))
        if not isinstance(tree, list):
            raise ValidationError(400, "The folder tree is a JSON array of group objects.")
        updated = client.portals.set_groups(portal["id"], tree)
    else:
        ctx.out.render(portal.get("layer_groups") or [], empty="No folders — the layer list is flat.")
        return EXIT_OK
    _maybe_publish(ctx, updated, args)
    ctx.out.render(updated.get("layer_groups") or [], empty="Folders cleared.")
    _publish_hint(ctx, updated, args)
    return EXIT_OK


def cmd_asset(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    result = client.portals.upload_asset(portal["id"], args.file)
    ctx.out.render(result)
    return EXIT_OK


def cmd_publish(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    published = client.portals.publish(portal["id"])
    ctx.out.render(published, LIST_COLUMNS)
    if not ctx.out.json_mode:
        ctx.out.success("Published: {0}".format(client.portals.url(published)))
    return EXIT_OK


def cmd_unpublish(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    result = client.portals.unpublish(portal["id"])
    ctx.out.render(result, LIST_COLUMNS)
    if not ctx.out.json_mode:
        ctx.out.success("{0} is offline. The draft is untouched.".format(portal.get("title")))
    return EXIT_OK


def cmd_delete(ctx, args) -> int:
    client = ctx.client()
    portal = client.portals.get(args.portal)
    if not confirm(ctx.out, "Delete portal {0!r} (id {1})? Its layers are NOT deleted.".format(
            portal.get("title"), portal["id"]), args.yes):
        ctx.out.info("Nothing deleted.")
        return EXIT_GENERIC
    client.portals.delete(portal["id"])
    ctx.out.render({"ok": True, "deleted": portal["id"], "title": portal.get("title")})
    if not ctx.out.json_mode:
        ctx.out.success("Deleted {0}.".format(portal.get("title")))
    return EXIT_OK


def cmd_download_area(ctx, args) -> int:
    import time

    client = ctx.client()
    portal = client.portals.get(args.portal)
    wanted = None
    if args.layers:
        wanted = {int(x) for x in args.layers.split(",") if x.strip()}

    items = []  # type: List[Dict[str, Any]]
    for entry in portal.get("layer_configs") or []:
        if wanted is not None and entry.get("layer_id") not in wanted:
            continue
        raster = entry.get("layer_type") == "raster"
        items.append({"layer_id": entry.get("layer_id"), "layer_type": entry.get("layer_type"),
                      "format": "tif" if raster else args.format})
    if not items:
        ctx.out.error("No layers on this portal match.")
        return EXIT_GENERIC

    slug = portal.get("slug")
    job = client.portals.export_bundle(slug, args.bbox, items, target_crs=args.crs)
    job_id = job.get("job_id") or job.get("id")
    ctx.out.info("Clipping {0} layer(s) to {1}…".format(len(items), args.bbox))
    while True:
        status = client.portals.export_status(slug, job_id)
        state = (status.get("status") or "").lower()
        if state == "ready":
            break
        if state in ("error", "failed"):
            ctx.out.error(status.get("error") or status.get("message") or "Export failed.")
            return EXIT_GENERIC
        time.sleep(2)

    with open(args.output, "wb") as fh:
        client.portals.export_download(slug, job_id, fh)
    ctx.out.render({"ok": True, "file": args.output, "layers": len(items)})
    if not ctx.out.json_mode:
        ctx.out.success("Wrote {0}.".format(args.output))
    return EXIT_OK


# ── shared bits ──────────────────────────────────────────────────────────────────────────────────

def _maybe_publish(ctx, portal: Dict[str, Any], args) -> None:
    if getattr(args, "publish", False):
        ctx.client().portals.publish(portal["id"])


def _publish_hint(ctx, portal: Dict[str, Any], args: Optional[Any] = None) -> None:
    """Say what is needed to make the change visible — and nothing when it already is."""
    if ctx.out.json_mode:
        return
    if getattr(args, "publish", False):
        ctx.out.info("Published: {0}".format(ctx.client().portals.url(portal)))
        return
    if portal.get("published"):
        ctx.out.info("The live portal still shows the previous version — "
                     "`geodeploy portals publish {0}` to update it.".format(portal["id"]))
    else:
        ctx.out.info("Draft saved. `geodeploy portals publish {0}` puts it online."
                     .format(portal["id"]))
