"""`geodeploy browse` — point at an instance and see what it offers.

The one command that needs no account: give it a URL and it prints the public portals and the
public layers, grouped by how they are stored, with the URL each one is best consumed by. With a
token it also shows what that token can see — which is the same two-mode behaviour a desktop
plugin needs, and the reason this exists in the client rather than only in a plugin.
"""
from __future__ import annotations

from ...config import split_portal_url
from ...errors import GeoDeployError, NotFoundError
from ..main import add_command
from ..output import EXIT_GENERIC, EXIT_OK

KIND_LABEL = {"postgis": "Vector (PostGIS)", "geoparquet": "Vector (GeoParquet)",
              "raster": "Raster (COG)"}


def register(subparsers) -> None:
    parser = add_command(
        subparsers, "browse", cmd_browse,
        "see what an instance publishes — no account needed",
        epilog="""\
examples:
  geodeploy browse https://geodeploy.example.org      anyone can run this
  geodeploy browse                                    the instance you are logged in to
  geodeploy browse --portal field-sites-2026          what one portal contains
  geodeploy browse https://gd.example.org/portals/5b5c627cfd/    a portal URL, pasted whole
  geodeploy browse --json | jq '.layers.raster'       machine-readable

With a token stored (or --token), it also reports what that token can see beyond the public
surface, which is usually the point of having one.
""")
    parser.add_argument("instance", nargs="?",
                        help="instance URL, or a published portal's URL "
                             "(default: the active profile)")
    parser.add_argument("--portal", metavar="SLUG_OR_URL",
                        help="show one portal's layers, read from its published style")
    parser.add_argument("--kind", choices=["raster", "postgis", "geoparquet"],
                        help="only this kind of layer")
    parser.add_argument("--links", action="store_true",
                        help="print every access URL for each layer, not just a summary")


def cmd_browse(ctx, args) -> int:
    instance = args.instance
    if instance and not args.portal and "/portals/" in instance:
        # A portal's own URL, pasted whole. It names both the instance and the portal, so it does
        # not need --portal and must not be read as an instance URL.
        instance, args.portal = split_portal_url(instance)
    elif args.portal:
        origin, args.portal = split_portal_url(args.portal)
        instance = instance or origin     # a full portal URL brings its instance with it
    if instance:
        args.url = instance               # the positional is just a friendlier --url
        ctx._resolved = None              # re-resolve against it
    client = ctx.client(auth_required=False)
    out = ctx.out

    if args.portal:
        return _one_portal(ctx, client, args)

    try:
        index = client.catalog.public()
    except NotFoundError:
        out.error("{0} does not publish a public index.".format(client.url),
                  hint="An admin can turn it on in Settings, or you can list what your token can "
                       "see with `geodeploy layers list` and `geodeploy portals list`.")
        return EXIT_GENERIC

    if out.json_mode:
        payload = dict(index)
        private = _private_view(ctx)
        if private:
            payload["with_your_token"] = private
        out.json(payload)
        return EXIT_OK

    counts = index.get("counts") or {}
    out.out(out.bold("{0}".format(client.url)))
    out.out(out.dim("{0} public portal(s), {1} public layer(s)".format(
        counts.get("portals", 0),
        sum(v for k, v in counts.items() if k != "portals"))))

    portals = index.get("portals") or []
    if portals:
        out.out("")
        out.out(out.bold("Portals"))
        out.table([{"slug": p.get("slug"), "title": p.get("title"),
                    "experience": p.get("experience"), "layers": p.get("layer_count"),
                    "url": p.get("url")} for p in portals],
                  ["slug", "title", "experience", "layers", "url"])

    layers = index.get("layers") or {}
    for kind in ("postgis", "geoparquet", "raster"):
        rows = layers.get(kind) or []
        if not rows or (args.kind and args.kind != kind):
            continue
        out.out("")
        out.out(out.bold("{0} — {1}".format(KIND_LABEL[kind], len(rows))))
        out.table([{"id": r.get("id"), "name": r.get("name"),
                    "geometry": r.get("geometry_type") or ("{0} band(s)".format(r.get("band_count"))
                                                           if r.get("band_count") else None),
                    "features": r.get("feature_count"), "crs": r.get("crs"),
                    "licence": r.get("license")} for r in rows],
                  ["id", "name", "geometry", "features", "crs", "licence"])
        if args.links:
            for row in rows:
                out.out("")
                out.out("  " + out.bold(row.get("name") or "?"))
                for link in row.get("links") or []:
                    out.out("    {0}  {1}".format(link.get("label"), link.get("url")))

    catalogs = index.get("catalogs") or {}
    out.out("")
    out.info("Standards: OGC API - Features {0} · STAC {1}".format(
        catalogs.get("ogc_features"), catalogs.get("stac")))

    private = _private_view(ctx)
    if private:
        out.info("With your token: {0} layer(s) and {1} portal(s) in total — "
                 "`geodeploy layers list` shows them.".format(private["layers"], private["portals"]))
    elif not ctx.resolved.token:
        out.info("No token in use: this is the anonymous view. `geodeploy login <url> --token …` "
                 "to see everything you have access to.")
    return EXIT_OK


def _private_view(ctx):
    """What the caller's own credential can see, when there is one. Best-effort by design:
    browsing must not fail because a token expired."""
    if not (ctx.resolved.token or ctx.resolved.jwt):
        return None
    try:
        client = ctx.client(auth_required=False)
        return {"layers": len(client.layers.list()), "portals": len(client.portals.list())}
    except GeoDeployError:
        return None


def _one_portal(ctx, client, args) -> int:
    """Read a published portal's own style.json — the whole portal, anonymously."""
    out = ctx.out
    slug = args.portal
    style_url = "{0}/portals/{1}/style.json".format(client.url, slug)
    try:
        style = client.catalog.portal_style(style_url)
    except GeoDeployError as exc:
        out.error("Could not read portal {0!r}: {1}".format(slug, exc),
                  hint="Public portals only — a password or members-only portal will not open "
                       "without a session.")
        return EXIT_GENERIC

    meta = style.get("geodeploy") or {}
    if out.json_mode:
        out.json(style)
        return EXIT_OK

    out.out(out.bold(meta.get("title") or slug))
    out.out(out.dim("{0}/portals/{1}/".format(client.url, slug)))
    out.out("")
    rows = []
    for layer in style.get("layers") or []:
        source = layer.get("source")
        if not source or source in ("basemap", "composite"):
            continue        # the basemap the template ships, not the author's data
        rows.append({"id": layer.get("id"), "type": layer.get("type"), "source": source})
    for deck in meta.get("deckLayers") or []:
        rows.append({"id": deck.get("id") or deck.get("name"), "type": "deck.gl (GeoParquet)",
                     "source": deck.get("url") or deck.get("manifest")})
    out.table(rows, ["id", "type", "source"], )
    if meta.get("bounds"):
        out.info("Extent: {0}".format(meta["bounds"]))
    return EXIT_OK
