"""What pushing this group would do, shown before it happens.

Republishing a portal from QGIS is a write with several distinct consequences — layers added,
layers removed, styles changed, and possibly files uploaded — and they are not equally reversible.
Deleting a layer from a portal is a click to undo; uploading a 2 GB file is not, and neither is
discovering afterwards that a layer you were only inspecting has been published.

So the push is never silent. It states each change in the user's own vocabulary (the layer names
they see in the panel), and the two consequential ones — uploading new files, dropping removed
layers — are separate opt-ins rather than one blanket "OK". Unchanged layers are shown too: seeing
"6 unchanged" is what tells you the other three lines are the whole story.
"""
from __future__ import annotations

try:                                    # a package, inside QGIS
    from .compat import enum
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    from compat import enum

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
                                     QTextEdit)
    QGIS = True
except ImportError:                     # pragma: no cover - importable for tests
    QGIS = False


def summarise(plan: dict) -> str:
    """The plan as plain text. Separated from the dialog so it can be tested without a GUI."""
    lines = []
    rename = plan.get("rename")
    if rename:
        lines.append("Rename the portal:")
        lines.append("    {0}  ->  {1}".format(rename[0] or "(untitled)", rename[1]))
        lines.append("")

    def section(title, items):
        if items:
            lines.append("{0} ({1}):".format(title, len(items)))
            lines.extend("    " + name for name in items)
            lines.append("")

    section("Unchanged", plan.get("unchanged") or [])
    section("Restyled", plan.get("restyled") or [])
    # Said out loud, because the alternative is a silent no-op the user reads as a failed restyle.
    # A portal's raster is a server-rendered picture in QGIS — "Singleband color data", with nothing
    # to change — so its styling cannot be read back out. The portal keeps what it had.
    section("Style kept as the portal has it — QGIS's version could not be read "
            "(a raster opened as portal tiles has no bands to restyle; tick “Prefer the real data "
            "over the styled view” to open the GeoTIFF itself, restyle that, and use “Save styling "
            "to GeoDeploy”)", plan.get("kept") or [])
    section("Added — already on the instance", plan.get("added") or [])
    section("New — not on the instance yet", plan.get("uploads") or [])
    section("Removed from the portal", plan.get("removed") or [])
    if not lines:
        lines = ["Nothing would change."]
    return "\n".join(lines).rstrip()


def confirm(parent, portal_title: str, plan: dict, creating: bool):
    """Show the plan. Returns `(go_ahead, upload_new, drop_removed)`.

    The two checkboxes default to ON — they are what the user is asking for by pushing a group they
    have edited — but they are visible and separable, so "publish my restyle without uploading that
    scratch layer" is one click rather than a reorganisation of the project.
    """
    if not QGIS:                        # pragma: no cover - no GUI in tests
        return (False, False, False)

    dialog = QDialog(parent)
    dialog.setWindowTitle(("Create portal " if creating else "Update portal ") + portal_title)
    layout = QVBoxLayout(dialog)

    uploads_n = len(plan.get("uploads") or [])
    existing_n = (len(plan.get("unchanged") or []) + len(plan.get("restyled") or [])
                  + len(plan.get("kept") or []) + len(plan.get("added") or []))
    if creating and uploads_n and not existing_n:
        # The "start in QGIS, end with a URL" case. Everything here is a local file, so the whole
        # group is about to be UPLOADED before the portal can exist — that is a much bigger action
        # than "publish a portal", and the headline should say so before the file sizes do.
        headline = ("None of these {0} layer(s) are on the instance yet. All of them will be "
                    "UPLOADED, then the portal “{1}” will be created and published.").format(
                        uploads_n, portal_title)
    elif creating:
        headline = "This will CREATE the portal “{0}”.".format(portal_title)
    else:
        headline = "This will UPDATE the published portal “{0}”.".format(portal_title)
    label = QLabel(headline)
    label.setWordWrap(True)
    layout.addWidget(label)

    body = QTextEdit()
    body.setReadOnly(True)
    body.setPlainText(summarise(plan))
    body.setMinimumSize(460, 260)
    layout.addWidget(body)

    uploads = plan.get("uploads") or []
    removed = plan.get("removed") or []

    upload_box = QCheckBox("Upload the {0} new layer(s) and add them".format(len(uploads)))
    upload_box.setChecked(True)
    upload_box.setEnabled(bool(uploads))
    if not uploads:
        upload_box.setText("No new layers to upload")
    layout.addWidget(upload_box)

    drop_box = QCheckBox("Remove the {0} layer(s) taken out of the group".format(len(removed)))
    drop_box.setChecked(True)
    drop_box.setEnabled(bool(removed))
    if not removed:
        drop_box.setText("No layers to remove")
    else:
        drop_box.setToolTip("Only removes them FROM THE PORTAL. The layers stay on the instance.")
    layout.addWidget(drop_box)

    note = QLabel("Removing a layer from a portal does not delete it from GeoDeploy.")
    note.setWordWrap(True)
    layout.addWidget(note)

    ok_button = enum(QDialogButtonBox, "StandardButton", "Ok")
    buttons = QDialogButtonBox(ok_button | enum(QDialogButtonBox, "StandardButton", "Cancel"))
    buttons.button(ok_button).setText("Publish" if not creating else "Create and publish")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != enum(QDialog, "DialogCode", "Accepted"):
        return (False, False, False)
    return (True, upload_box.isChecked() and bool(uploads),
            drop_box.isChecked() and bool(removed))
