"""Copy `cli/geodeploy/` into the plugin's vendor folder.

The plugin CANNOT pip-install anything — a QGIS user has no say over the interpreter's site-packages
— so the client ships inside the zip. That copy is CHECKED IN rather than produced at build time for
two reasons: plugins.qgis.org expects the uploaded zip to correspond to browsable repository code,
and a copy that only exists in CI is a copy nobody reviews.

Run this after changing the client, and `--check` in CI so the two cannot drift. That drift is not
hypothetical here: the Python and JavaScript symbology twins disagreed for months because nothing
compared them.
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "cli" / "geodeploy"
DST = ROOT / "integrations" / "qgis-plugin" / "geodeploy_qgis" / "vendor" / "geodeploy"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "cli")   # the argparse CLI is not needed


def differences() -> list[str]:
    """Paths that differ, ignoring what `IGNORE` drops."""
    if not DST.exists():
        return ["<vendor copy missing>"]
    out: list[str] = []
    for src_file in SRC.rglob("*.py"):
        rel = src_file.relative_to(SRC)
        if "__pycache__" in rel.parts or rel.parts[0] == "cli":
            continue
        dst_file = DST / rel
        if not dst_file.exists() or not filecmp.cmp(src_file, dst_file, shallow=False):
            out.append(str(rel))
    for dst_file in DST.rglob("*.py"):
        rel = dst_file.relative_to(DST)
        if not (SRC / rel).exists():
            out.append(f"{rel} (removed from cli/)")
    return sorted(set(out))


def main() -> int:
    check = "--check" in sys.argv
    if check:
        diff = differences()
        if diff:
            print("vendored client is out of date:")
            for d in diff:
                print("  ", d)
            print("\nrun: python integrations/qgis-plugin/scripts/vendor.py")
            return 1
        print("vendored client matches cli/geodeploy")
        return 0

    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST, ignore=IGNORE)
    print(f"copied {SRC} -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
