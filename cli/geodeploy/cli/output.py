"""Printing, tables, progress and exit codes — the only module allowed to write to a terminal.

Three rules:

* **stdout is the answer, stderr is the commentary.** `geodeploy layers list --json | jq` must
  receive JSON and nothing else, so progress, warnings and "publish to make this live" hints all go
  to stderr. This is also why a progress bar can never appear on stdout.
* **`--json` is a contract.** With it, stdout is exactly one JSON document — including for errors,
  which come back as `{"ok": false, "error": …}` so a script can read the failure instead of
  scraping it.
* **The exit code is the other contract.** A CI job branches on it long before it parses anything.
"""
from __future__ import annotations

import json as _json
import os
import shutil
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

# ── Exit codes ───────────────────────────────────────────────────────────────────────────────────
# Same matrix the docs publish. Separating AUTH from USAGE from SERVER is what lets a nightly job
# alert on "your token expired" without alerting on "the instance is restarting".
EXIT_OK = 0
EXIT_GENERIC = 1      # the operation failed (a 4xx that is not auth, a bad file, a failed job)
EXIT_USAGE = 2        # the command line itself was wrong — argparse's own convention
EXIT_AUTH = 3         # 401/403: no credential, expired token, missing scope, or too low a role
EXIT_NETWORK = 4      # never got an answer: DNS, TLS, connection reset, timeout
EXIT_SERVER = 5       # 5xx: the instance is up but broke


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("GEODEPLOY_FORCE_COLOR"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if sys.platform.startswith("win"):
        # Windows 10+ terminals understand ANSI once VT processing is on; enabling it is cheap and
        # failing to enable it just means no colour, never mojibake.
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


#: Typographic characters, and what to write instead on a console that cannot encode them.
#: A Windows console still running code page 437 raises UnicodeEncodeError on "✓" — which would
#: turn a SUCCESSFUL command into a traceback. Degrading the glyph is the only acceptable failure.
#: `–` (EN dash) is not the same character as `—` (EM dash) and both reach a console: the em dash
#: from our own output, the en dash from the SERVER, inside graduated legend labels ("10 – 90").
_FALLBACKS = {"✓": "OK", "→": "->", "—": "-", "–": "-", "…": "...", "·": "-",
              "≥": ">=", "≤": "<="}


def _encodable(stream) -> bool:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "".join(_FALLBACKS).encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class Formatter(object):
    """Writes everything the CLI shows, honouring --json / --quiet / --verbose / NO_COLOR."""

    def __init__(self, json_mode: bool = False, quiet: bool = False, verbose: bool = False,
                 stdout=None, stderr=None):
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._color = _supports_color(self.stdout) and not json_mode
        self._color_err = _supports_color(self.stderr) and not json_mode
        self._plain = not (_encodable(self.stdout) and _encodable(self.stderr))

    def _text(self, text: str) -> str:
        if not self._plain:
            return text
        for fancy, plain in _FALLBACKS.items():
            text = text.replace(fancy, plain)
        return text

    # -- styling ---------------------------------------------------------------------------------

    def paint(self, text: str, code: str, err: bool = False) -> str:
        enabled = self._color_err if err else self._color
        return "\033[{0}m{1}\033[0m".format(code, text) if enabled else text

    def dim(self, text: str, err: bool = False) -> str:
        return self.paint(text, "2", err)

    def bold(self, text: str, err: bool = False) -> str:
        return self.paint(text, "1", err)

    def green(self, text: str, err: bool = False) -> str:
        return self.paint(text, "32", err)

    def red(self, text: str, err: bool = False) -> str:
        return self.paint(text, "31", err)

    def yellow(self, text: str, err: bool = False) -> str:
        return self.paint(text, "33", err)

    # -- messages --------------------------------------------------------------------------------

    def out(self, text: str = "") -> None:
        """A line of the ANSWER — the thing the command was asked for."""
        self.stdout.write(self._text(text) + "\n")

    def info(self, text: str) -> None:
        """Commentary: what happened, what to do next. Silent under --json/--quiet."""
        if self.json_mode or self.quiet:
            return
        self.stderr.write(self._text(text) + "\n")

    def success(self, text: str) -> None:
        if self.json_mode or self.quiet:
            return
        self.stderr.write(self._text(self.green("✓ ", err=True) + text) + "\n")

    def warn(self, text: str) -> None:
        if self.json_mode or self.quiet:
            return
        self.stderr.write(self._text(self.yellow("warning: ", err=True) + text) + "\n")

    def error(self, text: str, hint: Optional[str] = None) -> None:
        """Errors always print, even under --quiet — silence on failure is how a CI job lies."""
        if self.json_mode:
            payload = {"ok": False, "error": text}
            if hint:
                payload["hint"] = hint
            self.stdout.write(_json.dumps(payload, indent=2) + "\n")
            return
        self.stderr.write(self._text(self.red("error: ", err=True) + text) + "\n")
        if hint:
            self.stderr.write(self._text(self.dim("  " + hint, err=True)) + "\n")

    def debug(self, text: str) -> None:
        if self.verbose and not self.json_mode:
            self.stderr.write(self._text(self.dim("· " + text, err=True)) + "\n")

    # -- structured output -----------------------------------------------------------------------

    def json(self, payload: Any) -> None:
        self.stdout.write(_json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n")

    def render(self, payload: Any, columns: Optional[Sequence[Any]] = None,
               empty: str = "Nothing to show.") -> None:
        """The default way a command answers: JSON under --json, otherwise a table or a record."""
        if self.json_mode:
            self.json(payload)
            return
        if isinstance(payload, list):
            if not payload:
                self.info(empty)
                return
            self.table(payload, columns)
        elif isinstance(payload, dict):
            self.record(payload, columns)
        elif payload is not None:
            self.out(str(payload))

    def table(self, rows: List[Dict[str, Any]], columns: Optional[Sequence[Any]] = None) -> None:
        """A plain aligned table. `columns` is a list of keys, or (key, heading) pairs."""
        if not rows:
            return
        if columns:
            cols = [(c, c.replace("_", " ")) if isinstance(c, str) else c for c in columns]
        else:
            keys = []  # type: List[str]
            for row in rows:
                for key in row:
                    if key not in keys:
                        keys.append(key)
            cols = [(k, k.replace("_", " ")) for k in keys]

        cells = [[_cell(row.get(key)) for key, _ in cols] for row in rows]
        widths = [len(str(head)) for _, head in cols]
        for line in cells:
            for i, value in enumerate(line):
                widths[i] = max(widths[i], len(value))
        # Keep the table inside the terminal: trim the widest column rather than wrapping, since a
        # wrapped table is unreadable and the full value is one --json away.
        budget = (shutil.get_terminal_size((100, 24)).columns
                  if hasattr(self.stdout, "isatty") and self.stdout.isatty() else 1000)
        while sum(widths) + 2 * (len(widths) - 1) > budget and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1

        self.out(self.dim("  ".join(
            str(head).upper().ljust(widths[i]) for i, (_, head) in enumerate(cols))))
        for line in cells:
            self.out("  ".join(_fit(value, widths[i]) for i, value in enumerate(line)))

    def record(self, row: Dict[str, Any], keys: Optional[Sequence[str]] = None) -> None:
        """One object as `key: value` lines — a portal, a layer, a job."""
        items = [(k, row.get(k)) for k in keys] if keys else list(row.items())
        width = max((len(str(k)) for k, _ in items), default=0)
        for key, value in items:
            if value is None and keys is None:
                continue
            self.out("{0}  {1}".format(self.dim(str(key).ljust(width)), _cell(value, wide=True)))

    def progress(self, label: str, total: Optional[int] = None) -> "Progress":
        return Progress(self, label, total)


class Progress(object):
    """A one-line progress bar on stderr, and a no-op when nobody is watching.

    Rate and remaining bytes are shown rather than a spinner because the question during a 4 GB
    upload is always "will this finish before I have to leave", and only a rate answers it.
    """

    def __init__(self, fmt: Formatter, label: str, total: Optional[int] = None):
        self._f = fmt
        self.label = label
        self.total = total
        self._last = 0.0
        self._started = time.time()
        self._active = (not fmt.json_mode and not fmt.quiet
                        and hasattr(fmt.stderr, "isatty") and fmt.stderr.isatty())
        self._done = False

    def update(self, done: int, total: Optional[int] = None) -> None:
        if not self._active:
            return
        if total:
            self.total = total
        now = time.time()
        complete = self.total and done >= self.total
        if not complete and now - self._last < 0.1:   # ~10 fps is plenty and keeps the CPU idle
            return
        self._last = now
        elapsed = max(now - self._started, 1e-6)
        rate = done / elapsed
        if self.total:
            fraction = min(max(done / float(self.total), 0.0), 1.0)
            filled = int(round(fraction * 24))
            bar = "#" * filled + "-" * (24 - filled)
            text = "{0} [{1}] {2:3.0f}%  {3} / {4}  {5}/s".format(
                self.label, bar, fraction * 100, human_size(done), human_size(self.total),
                human_size(rate))
        else:
            text = "{0}  {1}  {2}/s".format(self.label, human_size(done), human_size(rate))
        self._f.stderr.write("\r\033[K" + text)
        self._f.stderr.flush()

    def finish(self, message: Optional[str] = None) -> None:
        if self._done:
            return
        self._done = True
        if self._active:
            self._f.stderr.write("\r\033[K")
            self._f.stderr.flush()
        if message:
            self._f.info(message)

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.finish()


# ── value formatting ─────────────────────────────────────────────────────────────────────────────

def human_size(num: float) -> str:
    """Bytes as something readable. Binary units, because storage tooling reports binary units."""
    value = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return "{0:.0f} {1}".format(value, unit) if unit == "B" else "{0:.1f} {1}".format(value, unit)
        value /= 1024.0
    return "{0:.1f} TB".format(value)  # pragma: no cover - unreachable, loop returns


def human_count(num: Any) -> str:
    try:
        return "{0:,}".format(int(num))
    except (TypeError, ValueError):
        return "—"


def _cell(value: Any, wide: bool = False) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        if not value:
            return "—"
        if all(isinstance(v, (int, float)) for v in value):
            return ", ".join("{0:g}".format(v) for v in value)
        return _json.dumps(value) if wide else "{0} item{1}".format(
            len(value), "" if len(value) == 1 else "s")
    if isinstance(value, dict):
        return _json.dumps(value) if wide else "{0} key{1}".format(
            len(value), "" if len(value) == 1 else "s")
    text = str(value)
    if len(text) == 19 and text[4] == "-" and text[10] == "T":      # an ISO timestamp
        return text.replace("T", " ")
    return text


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text.ljust(width)
    return text[: max(1, width - 1)] + "…"


def iter_rows(payload: Any) -> Iterable[Dict[str, Any]]:  # pragma: no cover - convenience
    return payload if isinstance(payload, list) else [payload]
