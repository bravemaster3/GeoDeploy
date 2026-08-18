"""Which layers to upload — asked, not assumed.

Selecting several layers in QGIS and pressing Upload used to send all of them, and a layer that
could not be sent (a basemap, anything served from elsewhere) only announced itself as an error
afterwards. Both are the same mistake: deciding on the user's behalf and reporting later.

So the list is shown first, with the sendable ones ticked and the rest greyed out with the reason
beside them. Nothing here is clever; it exists so that "upload these two but not that one" is a
click rather than a rearrangement of the project.
"""
from __future__ import annotations

try:                                    # a package, inside QGIS
    from .compat import enum
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    from compat import enum

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QLabel,
                                     QListWidget, QListWidgetItem, QVBoxLayout)
    QGIS = True
except ImportError:                     # pragma: no cover - importable for tests
    QGIS = False


def summarise(candidates) -> str:
    """`[(name, reason_or_None)]` as plain text — separated from the dialog so it can be tested."""
    ok = [n for n, why in candidates if not why]
    blocked = [(n, why) for n, why in candidates if why]
    lines = []
    if ok:
        lines.append("Will upload ({0}):".format(len(ok)))
        lines.extend("    " + n for n in ok)
    if blocked:
        if lines:
            lines.append("")
        lines.append("Cannot upload ({0}):".format(len(blocked)))
        lines.extend("    {0} — {1}".format(n, why) for n, why in blocked)
    return "\n".join(lines) or "Nothing to upload."


def choose(parent, candidates):
    """Show the list. Returns the names to upload, or None if cancelled.

    `candidates` is `[(name, reason_or_None)]`; a reason means it cannot be sent.
    """
    if not QGIS:                        # pragma: no cover - no GUI in tests
        return None

    dialog = QDialog(parent)
    dialog.setWindowTitle("Upload to GeoDeploy")
    layout = QVBoxLayout(dialog)
    label = QLabel("Choose what to send. Anything greyed out cannot be uploaded from here.")
    label.setWordWrap(True)
    layout.addWidget(label)

    listing = QListWidget()
    listing.setSelectionMode(enum(QAbstractItemView, "SelectionMode", "NoSelection"))
    listing.setMinimumSize(420, 240)
    for name, why in candidates:
        item = QListWidgetItem(name if not why else "{0}  —  {1}".format(name, why))
        item.setData(enum(Qt, "ItemDataRole", "UserRole"), name)
        if why:
            # Visible but unusable: knowing WHY a layer is absent beats it silently not being there.
            item.setFlags(enum(Qt, "ItemFlag", "ItemIsUserCheckable"))
            item.setCheckState(enum(Qt, "CheckState", "Unchecked"))
        else:
            item.setFlags(enum(Qt, "ItemFlag", "ItemIsUserCheckable") | enum(Qt, "ItemFlag", "ItemIsEnabled"))
            item.setCheckState(enum(Qt, "CheckState", "Checked"))
        listing.addItem(item)
    layout.addWidget(listing)

    # Resolved once: the scoped spelling is long, and it is the same button twice.
    ok_button = enum(QDialogButtonBox, "StandardButton", "Ok")
    buttons = QDialogButtonBox(ok_button | enum(QDialogButtonBox, "StandardButton", "Cancel"))
    buttons.button(ok_button).setText("Upload")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != enum(QDialog, "DialogCode", "Accepted"):
        return None
    return [listing.item(i).data(enum(Qt, "ItemDataRole", "UserRole")) for i in range(listing.count())
            if listing.item(i).checkState() == enum(Qt, "CheckState", "Checked")]
