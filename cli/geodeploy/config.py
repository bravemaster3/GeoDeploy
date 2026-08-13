"""Where the CLI remembers your instances, and where it does NOT remember your tokens.

Three rules this module exists to keep:

1. **A token never lands in the config file.** `config.json` holds instance URLs, profile names and
   the account each was logged in as — the things worth reading and editing by hand. Secrets go to
   the OS keyring when there is one, and to a separate `credentials.json` (0600, in a 0700 parent)
   when there is not. That separation is what lets someone paste their config into an issue.

2. **Writes are atomic.** temp file in the same directory, chmod, `os.replace`. A CLI that is
   interrupted mid-write must not leave a truncated config that locks the user out of their own
   instance.

3. **Explicit beats remembered.** Flags override the environment, which overrides the active
   profile — so a CI job that sets `GEODEPLOY_URL`/`GEODEPLOY_TOKEN` needs no config file at all,
   and `--url` in a one-off command never silently writes itself down.

The env var names are `GEODEPLOY_URL` and `GEODEPLOY_TOKEN`, unchanged from the reference script in
`examples/`, because people already have them exported.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, Optional, Tuple

from .errors import ConfigError

APP_NAME = "geodeploy"

#: Service name under which tokens are filed in the OS keyring.
KEYRING_SERVICE = "geodeploy-cli"


# ── Locations ────────────────────────────────────────────────────────────────────────────────────

def config_dir() -> str:
    """The per-user config directory, following each platform's own convention.

    Hand-rolled rather than `platformdirs` for the no-dependency rule (see pyproject). The three
    branches below are the whole of what that library would do for us.
    """
    override = os.environ.get("GEODEPLOY_CONFIG_DIR")
    if override:
        return os.path.expanduser(override)
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "GeoDeploy")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/geodeploy")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def credentials_path() -> str:
    return os.path.join(config_dir(), "credentials.json")


# ── Atomic, permission-aware writes ──────────────────────────────────────────────────────────────

def atomic_write(path: str, text: str, mode: int = 0o600, tighten_parent: bool = False) -> None:
    """Write `text` to `path` atomically, with `mode` on the file.

    `tighten_parent` is for the credentials file only: 0700 on the directory keeps another account
    on a shared machine from listing it. It is deliberately NOT applied to ordinary output files —
    tightening a directory a user chose (their home, a project folder) is not ours to do.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if tighten_parent:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass  # Windows: chmod is largely a no-op, and NTFS ACLs already scope %APPDATA%.
    fd, tmp = tempfile.mkstemp(dir=parent, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:  # -sig: a BOM from Notepad is not an error
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError) as exc:
        raise ConfigError("Could not read {0}: {1}".format(path, exc))


# ── URL handling ─────────────────────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Canonical instance origin: scheme + host (+ port), no trailing slash, no `/api`.

    Everything downstream builds `<origin>/api/...`, so `https://gd.example.org/api/` and
    `gd.example.org` have to resolve to the same string — otherwise a token saved under one spelling
    is invisible to the other, which is the single most confusing thing a multi-instance CLI can do.
    """
    raw = (url or "").strip()
    if not raw:
        raise ConfigError("An instance URL is required (e.g. https://geodeploy.example.org).")
    if "://" not in raw:
        # A bare host is what people type. Default to https — the wrong guess is a TLS error the
        # user can read, whereas defaulting to http would silently send their token in clear.
        raw = "https://" + raw
    scheme, _, rest = raw.partition("://")
    if scheme not in ("http", "https"):
        raise ConfigError("Instance URL must be http:// or https:// (got {0}://).".format(scheme))
    rest = rest.rstrip("/")
    for suffix in ("/api/docs", "/api/openapi.json", "/api"):
        if rest.endswith(suffix):
            rest = rest[: -len(suffix)]
            break
    if not rest:
        raise ConfigError("Instance URL has no host: {0}".format(url))
    return "{0}://{1}".format(scheme, rest.rstrip("/"))


def split_portal_url(value: str) -> Tuple[Optional[str], str]:
    """A portal reference → `(instance URL or None, slug)`.

    Accepts a bare slug (`field-sites-2026`) or the URL a person copies out of the address bar
    (`https://gd.example.org/portals/field-sites-2026/`, with or without a trailing
    `style.json`). The URL already names its instance, so demanding the slug be cut out of it by
    hand — and the instance be supplied a second time — is friction with nothing behind it.
    """
    raw = (value or "").strip()
    if not raw:
        raise ConfigError("A portal is required: a slug, or the URL of a published portal.")
    if "://" not in raw:
        if "/portals/" not in raw:
            return None, raw.strip("/")          # a plain slug, which is the common case
        raw = "https://" + raw                   # `host/portals/slug` pasted without the scheme
    head, sep, tail = raw.partition("/portals/")
    if not sep:
        raise ConfigError(
            "That does not look like a portal URL: {0}\n"
            "Expected something like https://gd.example.org/portals/<slug>/".format(value))
    slug = tail.strip("/").split("/")[0]
    if not slug:
        raise ConfigError("That portal URL has no slug: {0}".format(value))
    return normalize_url(head), slug


# ── The config file ──────────────────────────────────────────────────────────────────────────────

class Config:
    """`config.json` — profiles and which one is active. No secrets, ever."""

    def __init__(self, data: Optional[Dict[str, Any]] = None, path: Optional[str] = None):
        self.path = path or config_path()
        data = data or {}
        self.profiles = dict(data.get("profiles") or {})   # name -> {url, email, ...}
        self.current = data.get("current") or None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        p = path or config_path()
        return cls(_read_json(p), p)

    def save(self) -> None:
        payload = {"current": self.current, "profiles": self.profiles}
        atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n", mode=0o600)

    # -- profiles --------------------------------------------------------------------------------

    def set_profile(self, name: str, url: str, email: Optional[str] = None,
                    make_current: bool = True, **extra: Any) -> Dict[str, Any]:
        entry = dict(self.profiles.get(name) or {})
        entry["url"] = normalize_url(url)
        if email is not None:
            entry["email"] = email
        for key, value in extra.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        self.profiles[name] = entry
        if make_current or not self.current:
            self.current = name
        return entry

    def remove_profile(self, name: str) -> bool:
        existed = self.profiles.pop(name, None) is not None
        if self.current == name:
            self.current = next(iter(self.profiles), None)
        return existed

    def get(self, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        key = name or self.current
        if not key:
            return None
        entry = self.profiles.get(key)
        if entry is None:
            return None
        out = dict(entry)
        out["name"] = key
        return out

    def resolve_name(self, name: Optional[str]) -> Optional[str]:
        if name:
            if name not in self.profiles:
                raise ConfigError(
                    "No profile named {0!r}. Known profiles: {1}".format(
                        name, ", ".join(sorted(self.profiles)) or "(none)"))
            return name
        return self.current


# ── Credentials ──────────────────────────────────────────────────────────────────────────────────
# Keyed by the NORMALIZED instance URL rather than the profile name, so two profiles pointing at
# one instance share the credential and deleting a profile does not orphan a secret.

def _keyring():
    """The `keyring` module if the host has it, else None.

    Optional on purpose: an install that has it (most desktops, and QGIS on Windows/macOS) gets the
    OS credential store; a bare server without it gets the 0600 file. Refusing to run without
    keyring would make the CLI unusable in exactly the headless case it is most needed.
    """
    if os.environ.get("GEODEPLOY_NO_KEYRING"):
        return None
    try:
        import keyring  # type: ignore
        # A keyring with no usable backend raises only on USE, so probe the backend up front.
        from keyring.backends import fail as _fail  # type: ignore
        if isinstance(keyring.get_keyring(), _fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def save_credential(url: str, token: Optional[str] = None, jwt: Optional[str] = None,
                    email: Optional[str] = None, use_keyring: bool = True) -> str:
    """Store credentials for `url`. Returns where they went: "keyring" or the file path.

    TWO kinds live side by side because the API deliberately treats them differently: an API token
    (`gdp_…`) is scoped and cannot touch `/admin/*` or mint more tokens, while a password login
    yields a session JWT that can. Someone who does both should not have to choose, so a `login
    --password` does not wipe a stored token.
    """
    origin = normalize_url(url)
    entry = load_credential(origin)
    if token is not None:
        entry["token"] = token
    if jwt is not None:
        entry["jwt"] = jwt
    if email is not None:
        entry["email"] = email
    return _store_credential(origin, entry, use_keyring)


def _store_credential(origin: str, entry: Dict[str, Any], use_keyring: bool = True) -> str:
    """Write `entry` as the WHOLE credential for `origin` — no merge.

    Separate from `save_credential` because deletion needs it: pruning a key and then saving
    through the merging path re-reads the stored entry and puts the key straight back, which is a
    "logged out" that leaves you logged in.
    """
    if use_keyring:
        kr = _keyring()
        if kr is not None:
            try:
                kr.set_password(KEYRING_SERVICE, origin, _json_dumps(entry))
                # Drop any older file copy, so a secret lives in ONE place, not two.
                _write_credentials({k: v for k, v in _read_credentials().items() if k != origin})
                return "keyring"
            except Exception:
                pass  # Locked/denied keyring: fall back to the file rather than losing the login.
    creds = _read_credentials()
    creds[origin] = entry
    _write_credentials(creds)
    return credentials_path()


def load_credential(url: str) -> Dict[str, Any]:
    """`{token?, jwt?, email?}` for an instance — empty when nothing is stored."""
    origin = normalize_url(url)
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYRING_SERVICE, origin)
            if value:
                try:
                    data = json.loads(value)
                    if isinstance(data, dict):
                        return dict(data)
                except ValueError:
                    # Pre-1.3 entries stored the bare token string. Read it rather than making a
                    # working login look like no login at all.
                    return {"token": value}
        except Exception:
            pass
    entry = _read_credentials().get(origin) or {}
    return dict(entry) if isinstance(entry, dict) else {}


def save_token(url: str, token: str, use_keyring: bool = True) -> str:
    """Back-compat shim for the common case of storing just an API token."""
    return save_credential(url, token=token, use_keyring=use_keyring)


def load_token(url: str) -> Optional[str]:
    return load_credential(url).get("token") or None


def delete_token(url: str, kind: Optional[str] = None) -> bool:
    """Forget stored credentials for an instance. `kind` limits it to "token" or "jwt"."""
    origin = normalize_url(url)
    entry = load_credential(origin)
    if not entry:
        return False
    if kind:
        if entry.pop(kind, None) is None:
            return False
        if any(entry.get(k) for k in ("token", "jwt")):
            _store_credential(origin, entry)
            return True
        # Nothing left worth keeping — fall through and remove the instance entirely.

    removed = False
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(KEYRING_SERVICE, origin):
                kr.delete_password(KEYRING_SERVICE, origin)
                removed = True
        except Exception:
            pass
    creds = _read_credentials()
    if creds.pop(origin, None) is not None:
        _write_credentials(creds)
        removed = True
    return removed


def _json_dumps(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True)


def _read_credentials() -> Dict[str, Any]:
    data = _read_json(credentials_path())
    return dict(data.get("instances") or {})


def _write_credentials(instances: Dict[str, Any]) -> None:
    atomic_write(credentials_path(),
                 json.dumps({"instances": instances}, indent=2, sort_keys=True) + "\n",
                 mode=0o600, tighten_parent=True)


# ── Resolution ───────────────────────────────────────────────────────────────────────────────────

class Resolved(object):
    """The instance + credential one command will actually use, and where each came from.

    `source_*` is not decoration: "why is it talking to the wrong server" is the commonest CLI
    support question there is, and `geodeploy profile show` answers it by printing these.
    """

    __slots__ = ("url", "token", "jwt", "profile", "source_url", "source_token", "email")

    def __init__(self, url: Optional[str], token: Optional[str], profile: Optional[str],
                 source_url: str, source_token: str, email: Optional[str] = None,
                 jwt: Optional[str] = None):
        self.url = url
        self.token = token
        #: A session JWT from `login --password`, used for the routes that refuse API tokens.
        self.jwt = jwt
        self.profile = profile
        self.source_url = source_url
        self.source_token = source_token
        self.email = email


def resolve(url: Optional[str] = None, token: Optional[str] = None,
            profile: Optional[str] = None, config: Optional[Config] = None) -> Resolved:
    """Flags → environment → active profile, for the URL and the token independently.

    Independently matters: `GEODEPLOY_TOKEN=… geodeploy layers list` against the profile's URL is a
    normal thing to do, and so is `--url` at a staging instance using the token already stored
    for it.
    """
    cfg = config or Config.load()
    name = cfg.resolve_name(profile)
    entry = cfg.get(name) if name else None

    if url:
        final_url, src_url = normalize_url(url), "flag"
    elif os.environ.get("GEODEPLOY_URL"):
        final_url, src_url = normalize_url(os.environ["GEODEPLOY_URL"]), "env"
    elif entry and entry.get("url"):
        final_url, src_url = entry["url"], "profile:" + str(name)
    else:
        final_url, src_url = None, "none"

    # An API TOKEN is the normal credential — scoped, revocable, and what the docs tell people to
    # mint. A session JWT only appears when someone has run `login --password`, and is kept for the
    # routes that refuse tokens on purpose (`/admin/*`, `/tokens`); it never displaces a token.
    jwt = None
    if token:
        final_token, src_token = token, "flag"
    elif os.environ.get("GEODEPLOY_TOKEN"):
        final_token, src_token = os.environ["GEODEPLOY_TOKEN"], "env"
    elif final_url:
        stored = load_credential(final_url)
        jwt = stored.get("jwt") or None
        if stored.get("token"):
            final_token, src_token = stored["token"], "stored"
        elif jwt:
            final_token, src_token = None, "stored session"
        else:
            final_token, src_token = None, "none"
    else:
        final_token, src_token = None, "none"

    return Resolved(final_url, final_token, name, src_url, src_token,
                    (entry or {}).get("email"), jwt)
