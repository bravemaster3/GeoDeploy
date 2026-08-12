"""`geodeploy` — the entry point: global options, dispatch, and the one place errors become exits.

Argparse rather than a framework, for the no-dependency rule. The shape is conventional so it needs
no learning: `geodeploy <group> <command> [args]`, `--json` anywhere, `-h` at every level.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, List, Optional

from .. import __version__
from ..config import Config, resolve
from ..errors import (APIError, AuthError, ConfigError, GeoDeployError, PermissionError_,
                      ServerError, TransportError, ValidationError)
from ..jobs import JobFailed, JobTimeout
from . import output
from .output import (EXIT_AUTH, EXIT_GENERIC, EXIT_NETWORK, EXIT_OK, EXIT_SERVER, EXIT_USAGE,
                     Formatter)

EPILOG = """\
examples:
  geodeploy login https://geodeploy.example.org      log in and remember the instance
  geodeploy upload roads.gpkg sites.csv --wait       upload several files and wait for ingest
  geodeploy layers list --type vector                what is on the instance
  geodeploy portals add-layer 3 roads --color '#e11d48' --marker star
  geodeploy portals style 3 roads --color-field pop --classify quantile --classes 5
  geodeploy portals publish 3                        make the edits live

Every command takes --json for machine-readable output. Exit codes: 0 ok, 1 failed, 2 bad usage,
3 authentication, 4 network, 5 server error.
"""


class Context(object):
    """Everything a command handler needs: the formatter, the resolved instance, a lazy client."""

    def __init__(self, args: argparse.Namespace, fmt: Formatter):
        self.args = args
        self.out = fmt
        self.config = Config.load()
        self._client = None  # type: Optional[Any]
        self._resolved = None  # type: Optional[Any]

    @property
    def resolved(self):
        if self._resolved is None:
            self._resolved = resolve(url=getattr(self.args, "url", None),
                                     token=getattr(self.args, "token", None),
                                     profile=getattr(self.args, "profile", None),
                                     config=self.config)
        return self._resolved

    def client(self, auth_required: bool = True, session: bool = False):
        """The API client for this invocation, built once.

        `auth_required=False` is for the public surfaces (STAC, OGC, templates): those work with a
        URL alone, and demanding a token to read what the whole internet can read would be silly.

        `session=True` marks a command that hits a route which REJECTS API tokens by design
        (`/admin/*`, `/tokens`, ownership transfer). It prefers a stored password session; with
        only a token available it still sends it, so the user gets the server's own 403 — which
        `admin.py` rewrites into "run `geodeploy login --password`" — rather than a client-side
        guess about what the instance would have said.
        """
        if self._client is None:
            info = self.resolved
            if not info.url:
                raise AuthError(401, "No instance configured.", "")
            token, jwt = info.token, info.jwt
            if session and jwt:
                token = None           # a session outranks a token for these routes
            if auth_required and not token and not jwt:
                raise AuthError(401, "No credentials for {0}.".format(info.url), "")
            from ..client import Client
            self._client = Client(
                info.url, token=token, jwt=jwt,
                timeout=getattr(self.args, "timeout", None) or 120.0,
                verify_tls=not getattr(self.args, "insecure", False),
                on_request=(lambda method, url: self.out.debug("{0} {1}".format(method, url)))
                if self.out.verbose else None)
            self.out.debug("instance {0} (from {1}), credential from {2}".format(
                info.url, info.source_url, info.source_token))
        return self._client


def _global_flags() -> argparse.ArgumentParser:
    """The flags every command accepts, as a PARENT parser.

    Attached to the root *and* to every leaf command, because `geodeploy layers list --json` is
    what people type — argparse would otherwise only accept `geodeploy --json layers list`, which
    nobody does twice. `SUPPRESS` as the default is what makes that safe: an unmentioned flag on
    the subparser leaves the root's value alone instead of resetting it.
    """
    parent = argparse.ArgumentParser(add_help=False)
    common = parent.add_argument_group("connection")
    common.add_argument("-p", "--profile", default=argparse.SUPPRESS,
                        help="use a saved profile (see `geodeploy profile`)")
    common.add_argument("--url", default=argparse.SUPPRESS,
                        help="instance URL, overriding the profile and GEODEPLOY_URL")
    common.add_argument("--token", default=argparse.SUPPRESS,
                        help="API token, overriding the stored one and GEODEPLOY_TOKEN")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="seconds to wait for an API call (default 120)")
    common.add_argument("--insecure", action="store_true", default=argparse.SUPPRESS,
                        help="skip TLS verification (self-signed instances only)")

    fmt = parent.add_argument_group("output")
    fmt.add_argument("--json", action="store_true", dest="json_mode", default=argparse.SUPPRESS,
                     help="machine-readable JSON on stdout, and nothing else")
    fmt.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                     help="only errors")
    fmt.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                     help="log each request to stderr")
    return parent


GLOBAL_FLAGS = _global_flags()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodeploy",
        description="Upload data, build portals and operate a GeoDeploy instance from the shell.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[GLOBAL_FLAGS])
    parser.add_argument("--version", action="version", version="geodeploy {0}".format(__version__))
    # NO `set_defaults` for the global flags here. `parents=` shares ACTION OBJECTS between this
    # parser and every leaf, and `set_defaults` rewrites `action.default` in place — which would
    # replace the SUPPRESS above with a real default on every leaf, so `geodeploy --json layers
    # list` would have its --json reset to False by the leaf parser. Callers read these with
    # `getattr(args, name, default)` instead.

    subparsers = parser.add_subparsers(dest="_group", metavar="<command>")

    from .commands import admin, auth, catalog, imports, jobs, layers, portals, sources, upload
    for module in (auth, upload, layers, portals, sources, imports, jobs, catalog, admin):
        module.register(subparsers)
    return parser


def _tolerate_legacy_console() -> None:
    """Never let a code-page limitation turn help text into a traceback.

    `Formatter` degrades typographic characters it knows about, but argparse writes help and usage
    straight to the stream, and an em dash in a `--help` epilog would raise UnicodeEncodeError on a
    Windows console still running code page 437. Mojibake is a cosmetic problem; a crash printing
    help is not.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - a stream that cannot be reconfigured
                pass


def main(argv: Optional[List[str]] = None) -> int:
    _tolerate_legacy_console()
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    fmt = Formatter(json_mode=getattr(args, "json_mode", False),
                    quiet=getattr(args, "quiet", False),
                    verbose=getattr(args, "verbose", False))

    handler = getattr(args, "_handler", None)
    if handler is None:
        # A bare `geodeploy`, or a group with no command: show that group's help, not a traceback.
        sub = getattr(args, "_subparser", None)
        (sub or parser).print_help(sys.stderr)
        return EXIT_USAGE

    ctx = Context(args, fmt)
    try:
        return handler(ctx, args) or EXIT_OK
    except KeyboardInterrupt:
        fmt.error("Interrupted.")
        return EXIT_GENERIC
    except AuthError as exc:
        fmt.error(exc.detail or "Authentication failed.", hint=_auth_hint(ctx, exc))
        return EXIT_AUTH
    except PermissionError_ as exc:
        scope = exc.missing_scope
        fmt.error(exc.detail or "Not allowed.",
                  hint=("Mint a token with the {0} scope (Settings → API tokens)."
                        .format(scope) if scope else
                        "Your role may be too low for this — an editor cannot administer an "
                        "instance, and administration is session-only anyway."))
        return EXIT_AUTH
    except ServerError as exc:
        fmt.error(exc.detail or "The instance returned an error.",
                  hint="Check `geodeploy admin health`, or the service logs.")
        return EXIT_SERVER
    except TransportError as exc:
        fmt.error(str(exc), hint="If the host is right, check the instance is up and reachable.")
        return EXIT_NETWORK
    except JobTimeout as exc:
        fmt.error(str(exc))
        return EXIT_GENERIC
    except JobFailed as exc:
        fmt.error("Ingest failed: {0}".format(exc),
                  hint="`geodeploy layers reprocess <layer>` restarts it without re-uploading.")
        return EXIT_GENERIC
    except ValidationError as exc:
        # A ValidationError raised before any request has no URL: that is the CLI rejecting the
        # arguments, which is a usage error (2). One that came back from the instance is a failed
        # operation (1) — the arguments were fine, the data or the state was not.
        fmt.error(exc.detail or str(exc))
        return EXIT_GENERIC if exc.url else EXIT_USAGE
    except APIError as exc:
        fmt.error(exc.detail or str(exc))
        return EXIT_GENERIC
    except ConfigError as exc:
        fmt.error(str(exc))
        return EXIT_USAGE
    except GeoDeployError as exc:
        fmt.error(str(exc))
        return EXIT_GENERIC
    except BrokenPipeError:  # `geodeploy … | head` — not an error worth a message
        return EXIT_OK
    except OSError as exc:
        fmt.error(str(exc))
        return EXIT_GENERIC


def _auth_hint(ctx: Context, exc: AuthError) -> str:
    info = ctx.resolved
    if not info.url:
        return "Run `geodeploy login <instance-url>`, or set GEODEPLOY_URL."
    if not info.token:
        return ("Run `geodeploy login {0}` with a token from Settings → API tokens, "
                "or set GEODEPLOY_TOKEN.".format(info.url))
    return ("The credential for {0} was refused — it may be revoked or expired. "
            "`geodeploy login {0}` stores a new one.".format(info.url))


def add_command(group, name: str, handler, help_text: str, aliases=(), epilog: Optional[str] = None):
    """Register one command on a group's subparser set, wiring its handler and help."""
    parser = group.add_parser(name, help=help_text, description=help_text, aliases=list(aliases),
                              epilog=epilog, parents=[GLOBAL_FLAGS],
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.set_defaults(_handler=handler)
    return parser


def group_parser(subparsers, name: str, help_text: str, aliases=()):
    """Register a command GROUP (`geodeploy layers …`) and return its own subparser set.

    `_subparser` is stashed so that `geodeploy layers` with no command prints the group's help
    rather than the root's — the root's help is a wall, and the user has already narrowed down.
    """
    parser = subparsers.add_parser(name, help=help_text, description=help_text,
                                   aliases=list(aliases),
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.set_defaults(_subparser=parser)
    return parser.add_subparsers(dest="_command", metavar="<subcommand>")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
