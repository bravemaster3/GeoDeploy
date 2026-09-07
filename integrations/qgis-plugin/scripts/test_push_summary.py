"""What the push dialog TELLS you before you press OK.

`diffdialog.summarise` is deliberately separated from the dialog — its docstring says so, "separated
from the dialog so it can be tested without a GUI" — and then nothing tested it. This is that test.

It is not decoration. The dialog is the only place the plugin can explain WHERE a restyle lands:
"Push group to portal" writes that portal's `layer_configs`, while the layer's own default style —
what its page shows, and what every other portal starts from — is written only by "Save styling to
GeoDeploy". A bare "Restyled (3)" reads as though the layers themselves are being changed, and
somebody who restyles inside a group, pushes, then opens the layer's page and finds the old colours
has no way to tell that was intended. That sentence is a feature, so it gets a test.

Run it:

    python3 scripts/test_push_summary.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis"))

import diffdialog                                                                # noqa: E402

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + str(detail)[:400] if detail else ""))
        FAILURES.append(name)


FULL = {
    "unchanged": ["Contours"],
    "restyled": ["Probable path evidence", "1840s Tithe"],
    "added": ["Weirs"],
    "uploads": ["Mapping extent"],
    "removed": ["Old sketch"],
    "kept": ["Degfert DEM"],
    "rename": ("Canal", "D+S Canal"),
}


def lines_of(text):
    return text.split("\n")


def section_body(text, heading):
    """The lines under a heading, up to the blank line that ends it."""
    out, collecting = [], False
    for line in lines_of(text):
        if line.startswith(heading):
            collecting = True
            continue
        if collecting:
            if not line.strip():
                break
            out.append(line)
    return out


def main():
    print("The push dialog's summary")
    text = diffdialog.summarise(FULL)

    # ── every list is shown, and counted ─────────────────────────────────────────────────────────
    for heading, count in (("Unchanged", 1), ("Restyled", 2), ("Added", 1), ("New", 1),
                           ("Removed", 1), ("Style kept", 1)):
        found = [ln for ln in lines_of(text) if ln.startswith(heading)]
        check("{0} is a section".format(heading), bool(found), text)
        if found:
            check("{0} carries its count next to the title".format(heading),
                  found[0].rstrip().endswith("({0}):".format(count)), found[0])

    check("every layer name appears exactly once",
          all(text.count(name) == 1 for group in ("unchanged", "restyled", "added", "uploads",
                                                  "removed", "kept")
              for name in FULL[group]), text)

    # ── THE SENTENCE THIS FILE EXISTS FOR ────────────────────────────────────────────────────────
    restyled = section_body(text, "Restyled")
    check("the restyled section says the change is to THIS portal",
          any("THIS portal only" in ln for ln in restyled), restyled)
    check("...and that the layer's own default is untouched",
          any("default style is left alone" in ln for ln in restyled), restyled)
    check("...and names the command that would change it",
          any("Save styling to GeoDeploy" in ln for ln in restyled), restyled)

    uploads = section_body(text, "New")
    check("the new-layers section says their default DOES get set",
          any("becomes their default" in ln for ln in uploads), uploads)

    # ── the note must not read as one of the layers ──────────────────────────────────────────────
    names = [ln for ln in restyled if ln.startswith("    ")]
    notes = [ln for ln in restyled if ln.startswith("  ") and not ln.startswith("    ")]
    check("the note is indented LESS than the names, so it is not read as one",
          len(notes) == 1 and len(names) == 2, restyled)
    check("the names are the layers and nothing else",
          [n.strip() for n in names] == FULL["restyled"], names)

    # ── a section with nothing in it is not shown at all ─────────────────────────────────────────
    quiet = diffdialog.summarise({"unchanged": ["Contours"]})
    check("an empty section is omitted rather than shown as (0)",
          "Restyled" not in quiet and "Removed" not in quiet, quiet)
    check("...and so is its note", "THIS portal only" not in quiet, quiet)

    # ── nothing to do says so, rather than showing an empty box ──────────────────────────────────
    check("an empty plan says nothing would change",
          diffdialog.summarise({}) == "Nothing would change.", repr(diffdialog.summarise({})))

    # ── a rename is the first thing said, because it renames the thing being described ───────────
    check("a rename leads", lines_of(text)[0].startswith("Rename the portal"), lines_of(text)[0])
    check("...and shows both names", "Canal  ->  D+S Canal" in text, text)
    check("a plan with no rename does not mention one",
          "Rename" not in diffdialog.summarise({"unchanged": ["a"]}), quiet)

    # ── an untitled portal is described, not left blank ──────────────────────────────────────────
    renamed = diffdialog.summarise({"rename": (None, "New name"), "unchanged": ["a"]})
    check("a portal with no previous title reads as (untitled)", "(untitled)" in renamed, renamed)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
