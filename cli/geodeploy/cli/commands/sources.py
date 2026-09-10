"""`geodeploy sources …` — somebody else's service, used in a portal without ingesting it.

XYZ, WMS, WMTS, WFS, OGC API - Features, vector tiles and PMTiles. See
`geodeploy/sources.py` for what each one is and which of them are fetched through the
instance rather than straight from the provider (the answer is CORS).
"""
from __future__ import annotations

from ...sources import SOURCE_TYPES
from ..main import add_command, group_parser
from ..output import EXIT_GENERIC, EXIT_OK
from ._common import confirm

COLUMNS = ["id", "name", "source_type", "kind", "url", "layer_name", "source_layer",
           "visibility", "created_by"]


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
  geodeploy sources add "Roads" https://example.org/ogc/collections/roads --type ogcapi
  geodeploy sources add "Basemap" https://tiles.example.org/tiles.json --type vectortile
  geodeploy sources add "Buildings" https://files.example.org/b.pmtiles --type pmtiles

Everything that can be checked is checked when it is registered — a WFS and an OGC API
collection are fetched, a TileJSON is read, a PMTiles header is parsed — so a wrong name or
an unreachable host fails here rather than as an empty layer on a published map. That is
also where the layer inside a tile set, its zoom range and its extent come from.
""")
    add.add_argument("name")
    add.add_argument("service_url", metavar="url", help="the service endpoint")
    add.add_argument("--type", dest="source_type", required=True, choices=list(SOURCE_TYPES))
    add.add_argument("--layer-name",
                     help="which layer of the service: WMS/WMTS `layers`, WFS `typeName`, or an "
                          "OGC API collection id (optional when the URL already names it)")
    add.add_argument("--source-layer",
                     help="the layer INSIDE a vector tile or PMTiles archive. Read from the "
                          "service where it publishes one (TileJSON, PMTiles metadata); give it "
                          "when it does not, because a style that names none draws nothing")
    add.add_argument("--matrix-set", help="WMTS TileMatrixSet (default GoogleMapsCompatible)")
    add.add_argument("--version", help="WMS (default 1.3.0) or WFS (default 2.0.0) version")
    add.add_argument("--format", dest="image_format", help="WMS/WMTS image format (default image/png)")
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
        args.name, args.source_type, args.service_url, layer_name=args.layer_name,
        source_layer=args.source_layer, matrix_set=args.matrix_set, version=args.version,
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
