"""Owner-editable environment variables — the curated subset of `.env`.

`.env` is bind-mounted into this container at `/geodeploy/.env` (docker-compose.yml), so the file the
API reads and writes IS the file on the host. Two consequences drive everything here:

1. **Write IN PLACE. Never `os.replace`.** A single-FILE bind mount binds an inode. Replacing the file
   gives the host a new inode while the container keeps the old one, so the container silently goes on
   reading a file nobody else can see. That exact failure cost a long debugging session with nginx's
   config (notes_for_future.md), and it would be worse here — the update would look applied and simply
   not be.

2. **Docker reads `.env` when a container is CREATED, not when it restarts.** Saving a value changes
   nothing until the affected services are recreated, which is why applying is a separate, explicit
   step rather than something a save does invisibly.

ALLOW-LIST, not a filter. A variable is editable because it is listed in `EDITABLE` — never because it
happens to exist in the file. Anything else is invisible to the API and unwritable through it, so a
variable added to `.env` later is safe by default rather than exposed by default. Deliberately absent:

  * `GEODEPLOY_SECRET_KEY` — the key for secrets encrypted at rest is derived from it. Changing it
    makes every stored secret (SMTP password, OIDC client secret, storage keys) permanently
    undecryptable. That is data loss, not a setting.
  * `POSTGIS_*`, `STORAGE_*` — owned by the setup wizard, and self-healing. Hand-editing fights it.
"""
from __future__ import annotations

import os
import re

from ..config import get_settings
from dataclasses import dataclass, field

ENV_PATH = os.getenv("GEODEPLOY_ENV_FILE", "/geodeploy/.env")

# `services` = which containers must be RECREATED for the value to take effect. Empty means the
# setting is read per-request and needs nothing.
@dataclass(frozen=True)
class EnvVar:
    key: str
    label: str
    help: str
    default: str
    kind: str = "text"                       # text | bool | choice
    choices: tuple[str, ...] = ()
    services: tuple[str, ...] = ("geodeploy-api", "celery")
    danger: bool = False
    # Only listed when the instance is ALREADY a demo. Keeps sandbox settings out of a production
    # owner's way, and — the real reason — keeps them from being discoverable there at all.
    demo_only: bool = False


EDITABLE: tuple[EnvVar, ...] = (
    EnvVar("GEODEPLOY_ENABLE_TERMINAL", "Container terminal",
           "Enables the Terminal tab in Infrastructure. It is a root shell inside a container and is "
           "owner-only even when on — leave it off unless you are actively debugging.",
           default="false", kind="bool", services=("geodeploy-api",), danger=True),
    EnvVar("PMTILES_TILE_MEMORY_LIMIT", "Tiler memory limit",
           "Caps the memory the tiling step may use before it spills to disk. Lower it only if you see "
           "a tiling run being killed on a very small server.",
           default="1GB", services=("celery",)),
    EnvVar("PMTILES_MAXZOOM", "Tile max zoom",
           "Maximum zoom baked into tiles. Left blank it is chosen automatically from the layer's "
           "feature count, which is almost always what you want. Lower values tile faster.",
           default="", services=("celery",)),
    EnvVar("PMTILES_TILE_THREADS", "Tiler threads",
           "Threads for the geometry-conversion step. The main tiling pass uses all cores regardless.",
           default="2", services=("celery",)),
    EnvVar("PMTILES_KEEP_ALL_FEATURES", "Keep every feature when zoomed in",
           "Guarantees no feature is dropped in dense areas at high zoom, at the cost of slower tiling "
           "and a larger archive.",
           default="1", kind="bool", services=("celery",)),
    # ── Demo-only. Note what is ABSENT: GEODEPLOY_DEMO_MODE itself is deliberately NOT editable
    # here, on any install. It arms an hourly wipe that deletes everything the snapshot does not
    # contain, so arming it must cost a deliberate edit to .env over SSH — friction is the point.
    # These two only TUNE a sandbox that is already running, so they are safe to expose there.
    EnvVar("GEODEPLOY_DEMO_SNAPSHOT", "Demo seed snapshot",
           "The backup the hourly reset restores. Take a backup once the demo is seeded the way you "
           "want visitors to find it, then name it here.",
           default="", services=("celery",), demo_only=True),
    EnvVar("GEODEPLOY_DEMO_MAX_UPLOAD_MB", "Demo upload limit (MB)",
           "Largest file a demo visitor may upload. Lower it if visitors fill the disk between "
           "resets.",
           default="500", demo_only=True),
    EnvVar("MAX_LARGE_UPLOAD_BYTES", "Large-upload ceiling",
           "Upper bound on a single direct-to-storage upload, in bytes. Files above the chunking "
           "threshold bypass the API entirely, so this is about your storage, not your server.",
           default=str(10 * 1024 * 1024 * 1024)),
)

_BY_KEY = {v.key: v for v in EDITABLE}
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _quote(value: str) -> str:
    """Quote only when the value would otherwise be misread. Docker's `.env` parser has no escaping
    worth relying on, so a value containing a quote or a newline is rejected by `validate` rather than
    escaped into something that parses differently than it reads."""
    return f'"{value}"' if (value == "" or re.search(r"[\s#]", value)) else value


def read_all(path: str | None = None) -> dict[str, str]:
    """Every key/value in the file — INTERNAL use (the write path needs it). Never return this to a
    caller; it contains the secrets the allow-list exists to keep out of the API surface."""
    out: dict[str, str] = {}
    try:
        with open(path or ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                if line.lstrip().startswith("#"):
                    continue
                m = _LINE.match(line)
                if m:
                    out[m.group(1)] = _unquote(m.group(2))
    except FileNotFoundError:
        pass
    return out


def list_editable(path: str | None = None) -> list[dict]:
    """The allow-listed variables with their current values, for the UI.

    Demo-only entries are omitted on a normal install — not merely hidden in the template, so they
    never reach the browser at all.
    """
    current = read_all(path)
    demo = get_settings().geodeploy_demo_mode
    return [{
        "key": v.key, "label": v.label, "help": v.help, "kind": v.kind,
        "choices": list(v.choices), "default": v.default, "danger": v.danger,
        "services": list(v.services),
        "value": current.get(v.key, ""),
        "effective": current.get(v.key) or v.default,
        "is_default": v.key not in current or current[v.key] == v.default,
    } for v in EDITABLE if demo or not v.demo_only]


def validate(key: str, value: str) -> str:
    """Raise ValueError unless `key` is allow-listed and `value` is storable. Returns the value."""
    spec = _BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"{key} is not an editable setting.")
    if spec.demo_only and not get_settings().geodeploy_demo_mode:
        # The UI omitting it is presentation; this is the gate. A hand-crafted request must not be
        # able to configure a sandbox on a production instance.
        raise ValueError(f"{key} is only editable on a demo instance.")
    if "\n" in value or "\r" in value or '"' in value or "'" in value:
        raise ValueError("Value may not contain quotes or line breaks.")
    if spec.kind == "bool" and value.lower() not in ("true", "false", "1", "0", ""):
        raise ValueError("Expected true or false.")
    if spec.kind == "choice" and value and value not in spec.choices:
        raise ValueError(f"Expected one of: {', '.join(spec.choices)}")
    return value


def write(updates: dict[str, str], path: str | None = None) -> list[str]:
    """Apply `updates` to the file and return the services needing recreation.

    Rewrites IN PLACE (truncate + write through the same file object) so the bind-mounted inode is
    preserved — see the module docstring. An empty value REMOVES the line, so "reset to default" is a
    real state rather than an empty string that overrides the default with nothing.
    """
    target = path or ENV_PATH
    for k, v in updates.items():
        validate(k, v)

    try:
        with open(target, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = []

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        m = _LINE.match(line) if not line.lstrip().startswith("#") else None
        if m and m.group(1) in remaining:
            key = m.group(1)
            val = remaining.pop(key)
            if val != "":
                out.append(f"{key}={_quote(val)}")
            # else: drop the line entirely → the default applies again
            continue
        out.append(line)

    added = [f"{k}={_quote(v)}" for k, v in remaining.items() if v != ""]
    if added:
        if out and out[-1].strip():
            out.append("")
        out.append("# Set from Settings → Infrastructure → Environment")
        out.extend(added)

    body = "\n".join(out).rstrip("\n") + "\n"
    # `r+` when it exists — NOT `w`, and never `os.replace`: both would give the host a new inode and
    # break the bind mount. `w` only for the case where there is no file yet (nothing is mounted to
    # preserve), which keeps this usable outside a container.
    if os.path.exists(target):
        with open(target, "r+", encoding="utf-8") as fh:
            fh.write(body)
            fh.truncate()
    else:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)

    services: list[str] = []
    for k in updates:
        for svc in _BY_KEY[k].services:
            if svc not in services:
                services.append(svc)
    return services
