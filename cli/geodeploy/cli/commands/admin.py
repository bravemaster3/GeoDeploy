"""`geodeploy admin …` and `geodeploy users …` — operating an instance.

Everything under `admin` needs a **password session** (`geodeploy login --password`), because the
API rejects API tokens on those routes so that a leaked token cannot restart your database or read
your storage credentials. `users` is different: it is scope-gated, so a token with `users:admin`
works.
"""
from __future__ import annotations

from ...errors import GeoDeployError
from ..main import add_command, group_parser
from ..output import EXIT_GENERIC, EXIT_OK, human_size
from ._common import confirm


def register(subparsers) -> None:
    group = group_parser(subparsers, "admin", "health, services, updates, backups, activity log")

    add_command(group, "health", cmd_health, "status of every service in the stack")

    services = add_command(group, "service", cmd_service, "start, stop or restart a service")
    services.add_argument("name", help="postgres | minio | redis | martin | titiler | nginx | "
                                       "celery | ui")
    services.add_argument("action", choices=["start", "stop", "restart"])
    services.add_argument("--yes", action="store_true")

    logs = add_command(group, "logs", cmd_logs, "recent log lines from one service")
    logs.add_argument("name")
    logs.add_argument("-n", "--tail", type=int, default=200)
    logs.add_argument("--no-timestamps", action="store_true")

    add_command(group, "storage", cmd_storage, "how much space each store is using")

    updates = add_command(group, "updates", cmd_updates,
                          "what this instance could update to (main, releases, branches)")
    updates.add_argument("--refresh", action="store_true",
                         help="bypass the 10-minute cache and ask GitHub now")

    update = add_command(group, "update", cmd_update, "update the instance")
    update.add_argument("target", nargs="?", help="main, a release tag, or a branch "
                                                  "(default: whatever the instance follows)")
    update.add_argument("--yes", action="store_true")
    update.add_argument("--watch", action="store_true", help="follow the update to completion")

    add_command(group, "reload-martin", cmd_reload_martin,
                "regenerate the vector tile server's config from the ready PostGIS layers")

    audit = add_command(group, "audit", cmd_audit, "the activity log (paginated, filtered server-side)")
    audit.add_argument("-n", "--limit", type=int, default=20)
    audit.add_argument("--offset", type=int, default=0)
    audit.add_argument("--action", help="exact action, or a family prefix like 'portal'")
    audit.add_argument("--resource-type")
    audit.add_argument("--resource-id")
    audit.add_argument("--actor", type=int, dest="actor_id")
    audit.add_argument("--query", dest="search")
    audit.add_argument("--since", help="ISO instant, e.g. 2026-08-01T00:00:00Z")
    audit.add_argument("--until")

    backups = add_command(group, "backups", cmd_backups, "backup history and destination status")
    backups.add_argument("--stored", action="store_true",
                         help="list what is actually AT the destination, not our own log")
    backups.add_argument("--run", action="store_true", help="start a backup now")
    backups.add_argument("-n", "--limit", type=int, default=10)

    listing = add_command(group, "public-index", cmd_public_index,
                          "show or change whether this instance is publicly browsable",
                          epilog="""examples:
  geodeploy admin public-index            is it listed?
  geodeploy admin public-index --off      stop listing (links keep working)
  geodeploy admin public-index --on

Listing controls whether anyone can DISCOVER your published public portals and public layers from
the instance URL — what `geodeploy browse` and the QGIS plugin read. It does not change who may
open them: a public portal stays reachable by its link either way.
""")
    state = listing.add_mutually_exclusive_group()
    state.add_argument("--on", dest="enabled", action="store_true", default=None)
    state.add_argument("--off", dest="enabled", action="store_false", default=None)

    add_command(group, "credentials", cmd_credentials,
                "connection details for the managed PostGIS and MinIO (owner only, audited)")

    # ── users ───────────────────────────────────────────────────────────────────────────────────
    users = group_parser(subparsers, "users", "members, roles and invitations")
    add_command(users, "list", cmd_users_list, "list members", aliases=["ls"])
    invite = add_command(users, "invite", cmd_users_invite, "invite someone (returns a link once)")
    invite.add_argument("email")
    invite.add_argument("--role", choices=["viewer", "editor", "admin"], default="viewer")
    add_command(users, "invitations", cmd_users_invitations, "pending invitations")
    role = add_command(users, "role", cmd_users_role, "change a member's role")
    role.add_argument("user_id", type=int)
    role.add_argument("role", choices=["viewer", "editor", "admin"])
    remove = add_command(users, "remove", cmd_users_remove,
                         "remove a member (their data is reassigned to the owner)", aliases=["rm"])
    remove.add_argument("user_id", type=int)
    remove.add_argument("--yes", action="store_true")


def _admin(ctx):
    return ctx.client(session=True)


# ── health & services ────────────────────────────────────────────────────────────────────────────

def cmd_health(ctx, args) -> int:
    rows = _admin(ctx).admin.health()
    ctx.out.render(rows, ["name", "status", "controllable", "message"])
    if not ctx.out.json_mode:
        bad = [r for r in rows if (r.get("status") or "") not in ("healthy", "running")]
        if bad:
            ctx.out.warn("{0} service(s) not healthy: {1}".format(
                len(bad), ", ".join(r.get("name") for r in bad)))
    return EXIT_OK


def cmd_service(ctx, args) -> int:
    if args.action in ("stop", "restart") and not confirm(
            ctx.out, "{0} the {1} service?".format(args.action.capitalize(), args.name), args.yes):
        return EXIT_GENERIC
    ctx.out.render(_admin(ctx).admin.service(args.name, args.action))
    if not ctx.out.json_mode:
        ctx.out.success("{0} {1}ed.".format(args.name, args.action.rstrip("e")))
    return EXIT_OK


def cmd_logs(ctx, args) -> int:
    data = _admin(ctx).admin.logs(args.name, tail=args.tail, timestamps=not args.no_timestamps)
    if ctx.out.json_mode:
        ctx.out.json(data)
        return EXIT_OK
    text = data.get("logs") if isinstance(data, dict) else data
    ctx.out.out(text if isinstance(text, str) else str(text))
    return EXIT_OK


def cmd_storage(ctx, args) -> int:
    stats = _admin(ctx).admin.storage_stats()
    if ctx.out.json_mode:
        ctx.out.json(stats)
        return EXIT_OK
    rows = []
    for key, label in (("postgis_bytes", "PostGIS tables"), ("raster_bytes", "Raster COGs"),
                       ("geoparquet_bytes", "GeoParquet + PMTiles"),
                       ("portal_bundle_bytes", "Published portals"), ("used_bytes", "Total")):
        value = stats.get(key)
        # None means "could not be measured", which is NOT zero — saying 0 for an unreachable
        # database would read as "empty" and send someone looking for missing data.
        rows.append({"store": label,
                     "size": human_size(value) if value is not None else "not measured"})
    ctx.out.table(rows, ["store", "size"])
    ctx.out.info("{0} vector layers, {1} rasters, {2} portals.".format(
        stats.get("vector_layers"), stats.get("raster_layers"), stats.get("portals")))
    return EXIT_OK


# ── updates ──────────────────────────────────────────────────────────────────────────────────────

def cmd_updates(ctx, args) -> int:
    data = _admin(ctx).admin.updates(refresh=args.refresh)
    if ctx.out.json_mode:
        ctx.out.json(data)
        return EXIT_OK
    main = data.get("main") or {}
    ctx.out.record({"channel": data.get("channel"), "current": data.get("current_ref"),
                    "main": main.get("status"), "commits behind": main.get("commits"),
                    "latest release": (data.get("latest_release") or {}).get("tag")},
                   ["channel", "current", "main", "commits behind", "latest release"])
    releases = data.get("releases") or []
    if releases:
        ctx.out.out("")
        ctx.out.table([{"tag": r.get("tag"), "published": r.get("published_at"),
                        "title": r.get("name")} for r in releases[:10]],
                      ["tag", "published", "title"])
    return EXIT_OK


def cmd_update(ctx, args) -> int:
    client = _admin(ctx)
    preflight = client.admin.preflight()
    if not ctx.out.json_mode and preflight:
        ctx.out.record(preflight)
    if preflight.get("blocked") or preflight.get("ok") is False:
        ctx.out.error(preflight.get("reason") or "The instance refused: work is in progress.")
        return EXIT_GENERIC
    if not confirm(ctx.out, "Update this instance to {0}?".format(args.target or "its channel"),
                   args.yes):
        return EXIT_GENERIC
    started = client.admin.update(args.target)
    ctx.out.render(started)
    if args.watch:
        import time
        while True:
            time.sleep(4)
            try:
                status = client.admin.update_status()
            except GeoDeployError as exc:
                # Expected: the API container is recreated BY the update, so the poll that hits
                # that moment fails. That is progress, not an error.
                ctx.out.info("  (instance restarting: {0})".format(exc))
                continue
            phase = status.get("phase") or status.get("status")
            ctx.out.info("  {0}".format(phase))
            if phase in ("done", "complete", "completed", "error", "failed"):
                ctx.out.render(status)
                return EXIT_OK if phase.startswith(("done", "complete")) else EXIT_GENERIC
    if not ctx.out.json_mode:
        ctx.out.success("Update started. `geodeploy admin updates` will show the new version once "
                        "the API comes back.")
    return EXIT_OK


def cmd_reload_martin(ctx, args) -> int:
    ctx.out.render(_admin(ctx).admin.reload_martin())
    return EXIT_OK


# ── audit & backups ──────────────────────────────────────────────────────────────────────────────

def cmd_audit(ctx, args) -> int:
    page = _admin(ctx).admin.audit(limit=args.limit, offset=args.offset, action=args.action,
                                   resource_type=args.resource_type, resource_id=args.resource_id,
                                   actor_id=args.actor_id, query=args.search, since=args.since,
                                   until=args.until)
    if ctx.out.json_mode:
        ctx.out.json(page)
        return EXIT_OK
    ctx.out.table(page.get("items") or [],
                  ["created_at", "actor_name", "action", "resource_type", "resource_id"])
    ctx.out.info("{0}-{1} of {2}".format(page.get("offset", 0) + 1,
                                         page.get("offset", 0) + len(page.get("items") or []),
                                         page.get("total")))
    return EXIT_OK


def cmd_backups(ctx, args) -> int:
    client = _admin(ctx)
    if args.run:
        ctx.out.render(client.admin.backup_run())
        if not ctx.out.json_mode:
            ctx.out.success("Backup started — it runs in the worker, so this returns immediately.")
        return EXIT_OK
    if args.stored:
        ctx.out.render(client.admin.backup_stored(), ["key", "size", "created_at"],
                       empty="Nothing at the destination.")
        return EXIT_OK
    settings = client.admin.backup_settings()
    if not ctx.out.json_mode:
        ctx.out.record(settings, ["enabled", "schedule", "hour", "keep", "bucket", "prefix",
                                  "endpoint", "include_postgis", "include_objects",
                                  "include_state"])
        ctx.out.out("")
    ctx.out.render(client.admin.backup_runs(limit=args.limit),
                   ["id", "started_at", "status", "trigger", "size_bytes", "error_message"],
                   empty="No backup runs recorded.")
    return EXIT_OK


def cmd_public_index(ctx, args) -> int:
    client = _admin(ctx)
    if args.enabled is None:
        state = client.admin.public_index()
    else:
        state = client.admin.set_public_index(args.enabled)
    ctx.out.render(state, ["enabled"])
    if not ctx.out.json_mode:
        if state.get("enabled"):
            ctx.out.success("This instance is listed — `geodeploy browse {0}` shows what anyone "
                            "can see.".format(ctx.resolved.url))
        else:
            ctx.out.success("Not listed. Published public portals stay reachable by their links; "
                            "they are simply not enumerated.")
    return EXIT_OK


def cmd_credentials(ctx, args) -> int:
    ctx.out.render(_admin(ctx).admin.credentials())
    return EXIT_OK


# ── users ────────────────────────────────────────────────────────────────────────────────────────

def cmd_users_list(ctx, args) -> int:
    ctx.out.render(ctx.client().users.list(),
                   ["id", "name", "email", "role", "vector_count", "raster_count", "portal_count",
                    "created_at"])
    return EXIT_OK


def cmd_users_invite(ctx, args) -> int:
    invite = ctx.client().users.invite(args.email, args.role)
    ctx.out.render(invite, ["id", "email", "role", "expires_at", "invite_url", "token",
                            "email_sent"])
    if not ctx.out.json_mode:
        ctx.out.warn("The invitation link is shown once — regenerating is the only way to get it "
                     "again.")
    return EXIT_OK


def cmd_users_invitations(ctx, args) -> int:
    ctx.out.render(ctx.client().users.invitations(),
                   ["id", "email", "role", "purpose", "expires_at", "created_at"],
                   empty="No pending invitations.")
    return EXIT_OK


def cmd_users_role(ctx, args) -> int:
    ctx.out.render(ctx.client().users.set_role(args.user_id, args.role), ["id", "email", "role"])
    return EXIT_OK


def cmd_users_remove(ctx, args) -> int:
    if not confirm(ctx.out, "Remove user {0}? Their layers and portals move to the owner.".format(
            args.user_id), args.yes):
        return EXIT_GENERIC
    ctx.client().users.delete(args.user_id)
    ctx.out.render({"ok": True, "removed": args.user_id})
    return EXIT_OK
