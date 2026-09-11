"""The ZIP, judged the way plugins.qgis.org judges it — before it is uploaded rather than after.

WHY THIS IS WORTH A FILE. Every upload is validated by the site, and a rejection costs the version
number: the fix has to go out as a NEW version, because the rejected one is burnt. Worse, some of
what the site checks is not an error anywhere else — a missing `supportsQt6` is a plugin that
simply does not appear in QGIS 4's manager, with nothing wrong in any log.

So this asserts the things their validator asserts, against the built artifact rather than against
the source tree: the archive's shape, the metadata's required fields, the version's form, and the
handful of files that must and must not be inside.

Run it:

    python3 scripts/build.py && python3 scripts/test_package.py
"""
import ast
import configparser
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..")
DIST = os.path.join(PLUGIN, "dist")
PACKAGE = "geodeploy_qgis"

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + str(detail)[:400] if detail else ""))
        FAILURES.append(name)


#: What the site refuses to publish without. `email` is required but never shown publicly.
REQUIRED = ("name", "qgisMinimumVersion", "description", "about", "version", "author", "email",
            "repository")

#: Strongly expected, and each one is a real consequence rather than a nicety.
EXPECTED = {
    "tracker": "without it there is nowhere for a user to report a bug",
    "homepage": "the plugin page links to it",
    "category": "decides which menu QGIS puts the plugin in",
    "icon": "the manager shows a blank tile without one",
    "tags": "how anybody finds this in the manager's search",
    "license": "the page states it; an absent one reads as unlicensed",
}


def newest_zip():
    """The archive `build.py` most recently wrote."""
    if not os.path.isdir(DIST):
        return None
    zips = [os.path.join(DIST, f) for f in os.listdir(DIST) if f.endswith(".zip")]
    if not zips:
        return None
    return max(zips, key=os.path.getmtime)


def metadata_of(archive):
    raw = archive.read(PACKAGE + "/metadata.txt").decode("utf-8")
    parser = configparser.ConfigParser()
    parser.read_string(raw)
    return parser["general"], raw


def main():
    print("The packaged plugin, judged the way plugins.qgis.org judges it\n")

    path = newest_zip()
    if not path:
        print("  no archive in dist/ — run `python3 scripts/build.py` first")
        sys.exit(1)
    print("archive:", os.path.basename(path))
    archive = zipfile.ZipFile(path)
    names = archive.namelist()

    # ── the shape of the archive ────────────────────────────────────────────────────────────────
    print("\nThe archive")
    roots = {n.split("/")[0] for n in names}
    check("exactly one top-level directory", roots == {PACKAGE}, sorted(roots))
    check("...named as a valid Python package",
          re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", PACKAGE) is not None, PACKAGE)
    for required in ("metadata.txt", "__init__.py"):
        check("holds {0}".format(required), PACKAGE + "/" + required in names)
    # The site rejects an archive carrying build litter, and a `.pyc` for the wrong Python is worse
    # than useless — QGIS may import it in preference to the source.
    litter = [n for n in names
              if n.endswith(".pyc") or "__pycache__" in n or n.startswith("__MACOSX")
              or "/.git" in n or n.endswith(".pyo")]
    check("carries no compiled or build litter", not litter, litter[:5])
    check("carries its licence", PACKAGE + "/LICENSE" in names)
    # 25 MB is the site's own ceiling; this plugin is a fraction of it, and a sudden jump means
    # something was packaged that should not have been.
    size_mb = os.path.getsize(path) / (1024 * 1024)
    check("is under the 25 MB the site accepts", size_mb < 25, "{0:.1f} MB".format(size_mb))
    check("...and has not suddenly grown", size_mb < 5, "{0:.1f} MB".format(size_mb))

    # ── the metadata ────────────────────────────────────────────────────────────────────────────
    print("\nmetadata.txt")
    meta, raw = metadata_of(archive)
    for field in REQUIRED:
        value = (meta.get(field) or "").strip()
        check("required: {0}".format(field), bool(value), "empty or missing")
    for field, why in EXPECTED.items():
        value = (meta.get(field) or "").strip()
        check("expected: {0} — {1}".format(field, why), bool(value), "empty or missing")

    version = (meta.get("version") or "").strip()
    check("the version is a plain number, no spaces or letters",
          re.match(r"^\d+(\.\d+)*$", version) is not None, version)
    check("...and is not the placeholder 0.0.0", version != "0.0.0", version)

    # QGIS VERSION RANGE. The minimum is what the manager filters on; the maximum is what stops a
    # plugin being offered on a QGIS it was never tried against.
    minimum = (meta.get("qgisminimumversion") or "").strip()
    maximum = (meta.get("qgismaximumversion") or "").strip()
    check("qgisMinimumVersion looks like a QGIS version",
          re.match(r"^\d+\.\d+", minimum) is not None, minimum)
    check("qgisMaximumVersion is stated", bool(maximum), "absent")
    check("...and covers QGIS 4", maximum.startswith("4."), maximum)

    # SUPPORTS Qt6. QGIS 4 is a Qt6 build, and its plugin manager will not enable a plugin that has
    # not said so — which is an invisible failure: the plugin is simply not there. Claiming
    # `qgisMaximumVersion=4.99` without this tells a human it runs on QGIS 4 and tells the
    # installer nothing.
    qt6 = (meta.get("supportsqt6") or "").strip().lower()
    check("supportsQt6 is declared", qt6 in ("true", "yes", "1"), qt6 or "absent")

    for flag in ("experimental", "deprecated"):
        value = (meta.get(flag) or "").strip().lower()
        check("{0} is a boolean the site understands".format(flag),
              value in ("true", "false", ""), value)

    # The icon has to BE there, not merely be named — the manager shows a blank tile otherwise.
    icon = (meta.get("icon") or "").strip()
    if icon:
        check("the icon named in the metadata is in the archive",
              PACKAGE + "/" + icon in names, icon)

    # A changelog is what the manager shows next to an update. Ours carries the version's own
    # entry, so the newest one must mention the version being shipped.
    changelog = meta.get("changelog") or ""
    check("the changelog mentions this version", version in changelog.split("\n")[0],
          changelog.split("\n")[0][:80])

    # A BARE PER-CENT SIGN BREAKS THE FILE. `metadata.txt` is read with `configparser`, where `%`
    # begins an interpolation — so "set to 0%" in a changelog entry makes the whole file
    # unparseable, and the plugin has no metadata at all. Caught here once, writing exactly that.
    check("no bare per-cent sign anywhere in the metadata", "%" not in raw,
          [ln for ln in raw.splitlines() if "%" in ln][:2])

    tags = [t.strip() for t in (meta.get("tags") or "").split(",") if t.strip()]
    check("the tags are a comma-separated list", len(tags) >= 3, tags)
    check("...and none of them is empty or absurdly long",
          all(0 < len(t) <= 40 for t in tags), [t for t in tags if len(t) > 40])

    # ── the entry point ─────────────────────────────────────────────────────────────────────────
    print("\nThe entry point")
    init = archive.read(PACKAGE + "/__init__.py").decode("utf-8")
    tree = ast.parse(init)
    check("__init__.py defines classFactory",
          any(isinstance(node, ast.FunctionDef) and node.name == "classFactory"
              for node in tree.body))
    # ANYTHING HEAVIER AT IMPORT TIME DISABLES THE PLUGIN with a traceback the user cannot act on:
    # QGIS imports this package to find `classFactory`, and an exception there is fatal before the
    # plugin has drawn anything. Read from the AST rather than by looking for the word "import",
    # which is also in the docstring explaining exactly this.
    top_level = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                 and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
    check("...and imports nothing at module level",
          not top_level, [ast.unparse(node) for node in top_level])

    # ── the vendored client travels with it ─────────────────────────────────────────────────────
    print("\nThe vendored client")
    vendored = [n for n in names if n.startswith(PACKAGE + "/vendor/geodeploy/")]
    check("the client is inside the archive", len(vendored) > 5, len(vendored))
    check("...including its __init__", PACKAGE + "/vendor/geodeploy/__init__.py" in names)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
