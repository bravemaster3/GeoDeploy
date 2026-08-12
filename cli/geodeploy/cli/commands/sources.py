"""`geodeploy sources …` — external WMS / XYZ / WFS services used without ingesting them."""
from __future__ import annotations

from ..main import add_command, group_parser
from ..output import EXIT_GENERIC, EXIT_OK
from ._common import confirm

COLUMNS = ["id", "name", "source_type", "kind", "url", "layer_name", "visibility", "created_by"]


def register(subparsers) -> None:
    group = group_parser(subparsers, "sources", "external map services shown alongside your data")

    listing = add_command(group, "list", cmd_list, "list external sources", aliases=["ls"])
    listing.add_argument("--query", dest="search")

    add = add_command(group, "add", cmd_add, "register an external service",
                      epilog="""\
examples:
  geodeploy sources add "OSM" 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' --type xyz
  geodeploy sources add "Orthophoto" https://wms.example.org/wms --type wms \\
      --layer-name ortho_2025 --version 1.3.0
  geodeploy sources add "Municipalities" https://wfs.example.org/ows --type wfs \\
      --layer-name ms:kommun

A WFS is probed when it is registered, so a wrong typeName fails here rather than as an empty
layer on a published map.
""")
    add.add_argument("name")
    add.add_argument("service_url", metavar="url", help="the service endpoint")
    add.add_argument("--type", dest="source_type", required=True, choices=["xyz", "wms", "wfs"])
    add.add_argument("--layer-name", help="WMS `layers` / WFS `typeName` (required for both)")
    add.add_argument("--version", help="WMS (default 1.3.0) or WFS (default 2.0.0) version")
    add.add_argument("--format", dest="image_format", help="WMS image format (default image/png)")
    add.add_argument("--attribution")

    show = add_command(group, "show", cmd_show, "one source")
    show.add_argument("source")

    usage = add_command(group, "usage", cmd_usage, "which portals use this source")
    usage.add_argument("source")

    share = add_command(group, "share", cmd_share, "set visibility (private | organization)")
    share.add_argument("source")
    share.add_argument("visibility", choices=["private", "organization"])

    delete = add_command(group, "delete", cmd_delete, "delete a source", aliases=["rm"])
    delete.add_argument("source")
    delete.add_argument("--yes", action="store_true")


def cmd_list(ctx, args) -> int:
    ctx.out.render(ctx.client().sources.list(query=args.search), COLUMNS,
                   empty="No external sources.")
    return EXIT_OK


def cmd_add(ctx, args) -> int:
    source = ctx.client().sources.create(
        args.name, args.source_type, args.service_url, layer_name=args.layer_name, version=args.version,
        image_format=args.image_format, attribution=args.attribution)
    ctx.out.render(source, COLUMNS + ["bbox", "geometry_type"])
    if not ctx.out.json_mode:
        ctx.out.success("Added {0}. Put it on a portal with "
                        "`geodeploy portals add-layer <portal> {1} --type external`."
                        .format(args.name, source.get("id")))
    return EXIT_OK


def cmd_show(ctx, args) -> int:
    ctx.out.render(ctx.client().sources.get(args.source))
    return EXIT_OK


def cmd_usage(ctx, args) -> int:
    source = ctx.client().sources.get(args.source)
    ctx.out.render(ctx.client().sources.usage(source["id"]), ["id", "title", "published"],
                   empty="Not used by any portal.")
    return EXIT_OK


def cmd_share(ctx, args) -> int:
    source = ctx.client().sources.get(args.source)
    ctx.out.render(ctx.client().sources.share(source["id"], args.visibility), COLUMNS)
    return EXIT_OK


def cmd_delete(ctx, args) -> int:
    client = ctx.client()
    source = client.sources.get(args.source)
    if not confirm(ctx.out, "Delete source {0!r}?".format(source.get("name")), args.yes):
        return EXIT_GENERIC
    client.sources.delete(source["id"])
    ctx.out.render({"ok": True, "deleted": source["id"]})
    if not ctx.out.json_mode:
        ctx.out.success("Deleted {0}.".format(source.get("name")))
    return EXIT_OK
