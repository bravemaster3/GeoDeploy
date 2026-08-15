"""Build the installable plugin zip.

plugins.qgis.org takes an archive containing **exactly one top-level directory**, named for the
plugin package, because it is unzipped straight into the user's plugins folder — a stray file at the
root would land beside other people's plugins.

The same zip is what you drag into QGIS's *Install from ZIP* while testing, so this is the only
packaging path: no separate "dev install" that could behave differently from what users get.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent / "geodeploy_qgis"
OUT_DIR = HERE.parent / "dist"

#: Never shipped: caches, the editor's leavings, and anything a reviewer would ask about.
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIX = {".pyc", ".pyo", ".orig", ".rej"}


def version() -> str:
    for line in (PLUGIN_DIR / "metadata.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("metadata.txt has no version=")


def build() -> Path:
    # The vendored client must be current BEFORE packaging: the zip is what people run, and a stale
    # copy inside it is the one thing no reviewer or test would notice.
    check = subprocess.run([sys.executable, str(HERE / "vendor.py"), "--check"],
                           capture_output=True, text=True)
    if check.returncode != 0:
        raise SystemExit(check.stdout + check.stderr)

    OUT_DIR.mkdir(exist_ok=True)
    target = OUT_DIR / f"geodeploy_qgis-{version()}.zip"
    if target.exists():
        target.unlink()

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if path.is_dir():
                continue
            if set(path.parts) & SKIP_DIRS or path.suffix in SKIP_SUFFIX:
                continue
            # arcname keeps the single top-level directory the installer requires.
            zf.write(path, Path(PLUGIN_DIR.name) / path.relative_to(PLUGIN_DIR))

    names = zipfile.ZipFile(target).namelist()
    roots = {n.split("/")[0] for n in names}
    if roots != {PLUGIN_DIR.name}:
        raise SystemExit(f"the archive must hold one top-level directory, found: {sorted(roots)}")
    for required in ("metadata.txt", "__init__.py", "LICENSE"):
        if f"{PLUGIN_DIR.name}/{required}" not in names:
            raise SystemExit(f"missing {required} — plugins.qgis.org requires it")
    return target


if __name__ == "__main__":
    out = build()
    size = out.stat().st_size / 1024
    print(f"{out}  ({size:.0f} KB, {len(zipfile.ZipFile(out).namelist())} files)")
    # Plain ASCII: this prints to a Windows console, where a "▸" raises UnicodeEncodeError
    # under cp1252 — the same trap the CLI hit with an en dash.
    print("Install in QGIS: Plugins > Manage and Install Plugins > Install from ZIP")
