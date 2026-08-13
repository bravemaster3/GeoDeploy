"""login / logout / whoami / profile / token — who you are, and against which instance.

**An API token is the normal credential.** `geodeploy login <url> --token gdp_…` (or the
`GEODEPLOY_TOKEN` environment variable) is what scripts, CI and the QGIS plugin use, and it is all
the CLI needs for data, portals and publishing.

`login --password` exists for one reason: the API rejects API tokens on `/admin/*`, `/backups/*`,
`/tokens` and ownership transfer, so that a leaked token cannot reconfigure an instance or mint
more tokens. Those commands therefore need a *session*. The password is never stored and never
accepted as a flag value — it is prompted for (or read from stdin, for CI), exchanged once for the
same 7-day JWT the browser gets, and only that is kept.
"""
from __future__ import annotations

import getpass
import sys
from typing import Any

from ... import config as cfg
from ...errors import AuthError, ConfigError
from ..main import add_command, group_parser
from ..output import EXIT_AUTH, EXIT_GENERIC, EXIT_OK, EXIT_USAGE


def register(subparsers) -> None:
    login = add_command(
        subparsers, "login", cmd_login, "log in to an instance and remember it",
        epilog="""\
examples:
  geodeploy login https://gd.example.org --token gdp_xxx   # normal: a scoped API token
  geodeploy login https://gd.example.org                   # prompts for the token
  geodeploy login https://gd.example.org --password        # a session too, for admin commands
  echo "$PW" | geodeploy login https://gd.example.org --email me@x.org --password-stdin
""")
    login.add_argument("url", help="instance URL, e.g. https://geodeploy.example.org")
    # NOTE: the API token comes from the GLOBAL --token flag, so `geodeploy login <url> --token
    # gdp_…` reads the same as every other command and there is only one spelling to remember.
    login.add_argument("--token-stdin", action="store_true", help="read the API token from stdin")
    login.add_argument("--password", action="store_true",
                       help="ALSO start a password session, for the admin commands that refuse "
                            "API tokens (prompts; the password is never stored)")
    login.add_argument("--password-stdin", action="store_true",
                       help="read the password from stdin instead of prompting (for CI)")
    login.add_argument("--email", help="account email for the password session")
    login.add_argument("--name", help="profile name (default: the instance host)")
    login.add_argument("--no-keyring", action="store_true",
                       help="store credentials in the 0600 file rather than the OS keyring")

    logout = add_command(subparsers, "logout", cmd_logout,
                         "forget the credentials stored for an instance")
    logout.add_argument("--all", action="store_true", help="every instance, not just the active one")
    logout.add_argument("--session-only", action="store_true",
                        help="drop the password session but keep the API token")

    add_command(subparsers, "whoami", cmd_whoami, "show the authenticated account and its role")

    profile = group_parser(subparsers, "profile", "manage saved instances", aliases=["profiles"])
    add_command(profile, "list", cmd_profile_list, "list saved profiles")
    use = add_command(profile, "use", cmd_profile_use, "make a profile the active one")
    use.add_argument("name")
    add_command(profile, "show", cmd_profile_show,
                "show the active profile and where each setting came from")
    remove = add_command(profile, "remove", cmd_profile_remove, "delete a saved profile")
    remove.add_argument("name")

    token = group_parser(subparsers, "token", "manage API tokens (needs a password session)")
    add_command(token, "list", cmd_token_list, "list your API tokens")
    create = add_command(token, "create", cmd_token_create, "mint an API token (shown once)")
    create.add_argument("name", help="what this token is for — it appears in Settings")
    create.add_argument("--scopes", default="data:read,data:write,portal:read,portal:write,portal:publish",
                        help="comma-separated: data:read, data:write, portal:read, portal:write, "
                             "portal:publish, users:admin")
    create.add_argument("--expires", type=int, default=90, choices=[30, 90, 365],
                        help="days until it expires (default 90)")
    create.add_argument("--save", action="store_true",
                        help="store the new token as this instance's credential")
    revoke = add_command(token, "revoke", cmd_token_revoke, "revoke an API token")
    revoke.add_argument("id", type=int)


# ── login ────────────────────────────────────────────────────────────────────────────────────────

def cmd_login(ctx, args) -> int:
    from ...client import Client

    url = cfg.normalize_url(args.url)
    out = ctx.out

    token = getattr(args, "token", None)
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    if not token and not args.password and not args.password_stdin:
        if not sys.stdin.isatty():
            raise ConfigError("Pass --token, --token-stdin, or --password-stdin with --email.")
        out.info("Create a token in {0} → Settings → API tokens. It is shown once.".format(url))
        token = getpass.getpass("API token (input hidden): ").strip()

    client = Client(url, token=token or None,
                    verify_tls=not getattr(args, "insecure", False))

    jwt = None
    if args.password or args.password_stdin:
        email = args.email
        if not email:
            if not sys.stdin.isatty():
                raise ConfigError("--password-stdin needs --email.")
            email = input("Email: ").strip()
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass("Password for {0} (input hidden): ".format(email))
        jwt = client.login(email, password)
        del password  # nothing keeps it: not the config, not this process any longer than needed
        client.token = token or None   # restore the token; login() cleared it on the client
        client.jwt = jwt

    identity = client.whoami()

    name = args.name or _profile_name(url)
    ctx.config.set_profile(name, url, email=identity.get("email"))
    ctx.config.save()
    where = cfg.save_credential(url, token=token or None, jwt=jwt,
                                email=identity.get("email"),
                                use_keyring=not getattr(args, "no_keyring", False))

    if ctx.out.json_mode:
        ctx.out.json({"ok": True, "instance": url, "profile": name, "stored": where,
                      "user": identity, "session": bool(jwt)})
        return EXIT_OK
    out.success("Logged in to {0} as {1} ({2}).".format(url, identity.get("email"),
                                                        identity.get("role")))
    out.info("Profile {0!r} is now active; credentials stored in {1}.".format(name, where))
    if jwt:
        out.info("A password session was stored too — that is what the admin commands use. "
                 "It expires in 7 days, and a password change revokes it immediately.")
    elif token:
        out.info("Administration commands (`geodeploy admin …`) need `--password`, because the "
                 "API refuses API tokens on those routes by design.")
    return EXIT_OK


def _profile_name(url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0]
    return host.split(":")[0] or "default"


def cmd_logout(ctx, args) -> int:
    kind = "jwt" if args.session_only else None
    if args.all:
        removed = []
        for name, entry in list(ctx.config.profiles.items()):
            if entry.get("url") and cfg.delete_token(entry["url"], kind):
                removed.append(entry["url"])
        ctx.out.render({"ok": True, "forgotten": removed})
        if not ctx.out.json_mode:
            ctx.out.success("Forgot credentials for {0} instance(s).".format(len(removed)))
        return EXIT_OK

    info = ctx.resolved
    if not info.url:
        ctx.out.error("No instance to log out of.")
        return EXIT_USAGE
    removed = cfg.delete_token(info.url, kind)
    if ctx.out.json_mode:
        ctx.out.json({"ok": True, "instance": info.url, "forgotten": removed})
        return EXIT_OK
    if removed:
        ctx.out.success("Forgot {0} for {1}.".format(
            "the password session" if args.session_only else "the stored credentials", info.url))
    else:
        ctx.out.info("Nothing was stored for {0}.".format(info.url))
    return EXIT_OK


# ── whoami / profiles ────────────────────────────────────────────────────────────────────────────

def cmd_whoami(ctx, args) -> int:
    info = ctx.resolved
    if not info.url:
        ctx.out.error("Not logged in to any instance.",
                      hint="Run `geodeploy login <url>`, or set GEODEPLOY_URL and GEODEPLOY_TOKEN.")
        return EXIT_AUTH
    identity = ctx.client().whoami()
    payload = dict(identity)
    payload.update({"instance": info.url, "profile": info.profile,
                    "credential": _credential_kind(info)})
    ctx.out.render(payload, ["instance", "profile", "credential", "email", "name", "role", "id"])
    return EXIT_OK


def _credential_kind(info) -> str:
    if info.token:
        return "API token ({0})".format(info.source_token)
    if info.jwt:
        return "password session (stored)"
    return "none"


def cmd_profile_list(ctx, args) -> int:
    rows = []
    for name, entry in sorted(ctx.config.profiles.items()):
        stored = cfg.load_credential(entry.get("url") or "") if entry.get("url") else {}
        rows.append({"active": name == ctx.config.current, "name": name,
                     "url": entry.get("url"), "email": entry.get("email"),
                     "token": bool(stored.get("token")), "session": bool(stored.get("jwt"))})
    ctx.out.render(rows, ["active", "name", "url", "email", "token", "session"],
                   empty="No profiles yet — run `geodeploy login <url>`.")
    return EXIT_OK


def cmd_profile_use(ctx, args) -> int:
    if args.name not in ctx.config.profiles:
        ctx.out.error("No profile named {0!r}.".format(args.name),
                      hint="`geodeploy profile list` shows what there is.")
        return EXIT_GENERIC
    ctx.config.current = args.name
    ctx.config.save()
    ctx.out.render({"ok": True, "current": args.name,
                    "url": ctx.config.profiles[args.name].get("url")})
    if not ctx.out.json_mode:
        ctx.out.success("Now using {0} ({1}).".format(
            args.name, ctx.config.profiles[args.name].get("url")))
    return EXIT_OK


def cmd_profile_show(ctx, args) -> int:
    info = ctx.resolved
    ctx.out.render({"profile": info.profile, "instance": info.url,
                    "instance_from": info.source_url, "credential": _credential_kind(info),
                    "credential_from": info.source_token, "email": info.email,
                    "config_file": cfg.config_path(),
                    "credentials_file": cfg.credentials_path()},
                   ["profile", "instance", "instance_from", "credential", "credential_from",
                    "email", "config_file", "credentials_file"])
    return EXIT_OK


def cmd_profile_remove(ctx, args) -> int:
    entry = ctx.config.profiles.get(args.name)
    if not ctx.config.remove_profile(args.name):
        ctx.out.error("No profile named {0!r}.".format(args.name))
        return EXIT_GENERIC
    ctx.config.save()
    if entry and entry.get("url"):
        cfg.delete_token(entry["url"])
    ctx.out.render({"ok": True, "removed": args.name})
    if not ctx.out.json_mode:
        ctx.out.success("Removed profile {0!r} and its stored credentials.".format(args.name))
    return EXIT_OK


# ── API tokens ───────────────────────────────────────────────────────────────────────────────────

def cmd_token_list(ctx, args) -> int:
    rows = ctx.client(session=True).tokens()
    ctx.out.render(rows, ["id", "name", "prefix", "scopes", "expires_at", "last_used_at"],
                   empty="No API tokens.")
    return EXIT_OK


def cmd_token_create(ctx, args) -> int:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    created = ctx.client(session=True).create_token(args.name, scopes, args.expires)
    if ctx.out.json_mode:
        ctx.out.json(created)
    else:
        ctx.out.render(created, ["id", "name", "prefix", "scopes", "expires_at", "token"])
        ctx.out.warn("Copy the token now — it is stored hashed and cannot be shown again.")
    if args.save and created.get("token"):
        where = cfg.save_credential(ctx.resolved.url, token=created["token"])
        ctx.out.info("Saved as this instance's credential ({0}).".format(where))
    return EXIT_OK


def cmd_token_revoke(ctx, args) -> int:
    ctx.client(session=True).revoke_token(args.id)
    ctx.out.render({"ok": True, "revoked": args.id})
    if not ctx.out.json_mode:
        ctx.out.success("Token {0} revoked.".format(args.id))
    return EXIT_OK
