"""`geodeploy catalog …` — the public surfaces: OGC API - Features, STAC, templates, basemaps.

These need no credentials, which makes this group the honest way to check what you have actually
published: if a layer is not listed here, nobody outside your organisation can see it either.
"""
from __future__ import annotations

import json

from ..main import add_command, group_parser
from ..output import EXIT_OK


def register(subparsers) -> None:
    group = group_parser(subparsers, "catalog",
                         "what this instance publishes: OGC API - Features, STAC, templates")

    add_command(group, "collections", cmd_collections,
                "OGC API - Features collections (one per public vector layer)")

    items = add_command(group, "items", cmd_items, "features from a collection")
    items.add_argument("collection", help="collection id, e.g. vector-a1b2c3d4e5f6")
    items.add_argument("--bbox", help="minx,miny,maxx,maxy")
    items.add_argument("--limit", type=int, default=10)
    items.add_argument("--offset", type=int, default=0)

    add_command(group, "conformance", cmd_conformance,
                "what the OGC endpoint claims to support")

    stac = add_command(group, "stac", cmd_stac, "the STAC catalog root and its collections")
    stac.add_argument("--collection", help="list items in this collection (vectors | rasters)")
    stac.add_argument("--limit", type=int, default=20)

    search = add_command(group, "search", cmd_search, "STAC search across public layers")
    search.add_argument("--bbox")
    search.add_argument("--collections")
    search.add_argument("--datetime", dest="datetime_", help="instant or start/end (.. is open)")
    search.add_argument("--ids")
    search.add_argument("--limit", type=int, default=20)

    add_command(group, "templates", cmd_templates, "portal templates and the experiences they suit")
    add_command(group, "basemaps", cmd_basemaps, "basemaps available to portals")


def _client(ctx):
    # Public endpoints: a URL is enough, no credential required.
    return ctx.client(auth_required=False)


def cmd_collections(ctx, args) -> int:
    rows = _client(ctx).catalog.collections()
    ctx.out.render([{"id": c.get("id"), "title": c.get("title"),
                     "extent": ((c.get("extent") or {}).get("spatial") or {}).get("bbox")}
                    for c in rows], ["id", "title", "extent"],
                   empty="No public collections — share a vector layer publicly to add one.")
    return EXIT_OK


def cmd_items(ctx, args) -> int:
    data = _client(ctx).catalog.items(args.collection, bbox=args.bbox, limit=args.limit,
                                      offset=args.offset)
    if ctx.out.json_mode:
        ctx.out.json(data)
        return EXIT_OK
    ctx.out.info("{0} returned, {1} matched.".format(data.get("numberReturned"),
                                                     data.get("numberMatched", "unknown")))
    ctx.out.out(json.dumps(data, indent=2, ensure_ascii=False)[:20000])
    return EXIT_OK


def cmd_conformance(ctx, args) -> int:
    ctx.out.render((_client(ctx).catalog.conformance() or {}).get("conformsTo") or [])
    return EXIT_OK


def cmd_stac(ctx, args) -> int:
    catalog = _client(ctx).catalog
    if args.collection:
        ctx.out.render(catalog.stac_items(args.collection, limit=args.limit))
        return EXIT_OK
    ctx.out.render([{"id": c.get("id"), "title": c.get("title"), "description": c.get("description")}
                    for c in catalog.stac_collections()], ["id", "title", "description"])
    return EXIT_OK


def cmd_search(ctx, args) -> int:
    data = _client(ctx).catalog.search(bbox=args.bbox, collections=args.collections,
                                       datetime=args.datetime_, ids=args.ids, limit=args.limit)
    if ctx.out.json_mode:
        ctx.out.json(data)
        return EXIT_OK
    features = data.get("features") or []
    ctx.out.table([{"id": f.get("id"), "collection": f.get("collection"),
                    "assets": ", ".join(sorted((f.get("assets") or {}).keys()))}
                   for f in features], ["id", "collection", "assets"])
    return EXIT_OK


def cmd_templates(ctx, args) -> int:
    ctx.out.render(_client(ctx).catalog.templates(),
                   ["id", "name", "archetypes", "archetype", "tags", "is_official", "version"])
    return EXIT_OK


def cmd_basemaps(ctx, args) -> int:
    ctx.out.render(_client(ctx).catalog.basemaps(), ["id", "name", "type", "attribution"])
    return EXIT_OK
