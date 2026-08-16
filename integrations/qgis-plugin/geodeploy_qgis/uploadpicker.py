"""Which layers to upload — asked, not assumed.

Selecting several layers in QGIS and pressing Upload used to send all of them, and a layer that
could not be sent (a basemap, anything served from elsewhere) only announced itself as an error
afterwards. Both are the same mistake: deciding on the user's behalf and reporting later.

So the list is shown first, with the sendable ones ticked and the rest greyed out with the reason
beside them. Nothing here is clever; it exists so that "upload these two but not that one" is a
click rather than a rearrangement of the project.
"""
from __future__ import annotations

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
    listing.setSelectionMode(QAbstractItemView.NoSelection)
    listing.setMinimumSize(420, 240)
    for name, why in candidates:
        item = QListWidgetItem(name if not why else "{0}  —  {1}".format(name, why))
        item.setData(Qt.UserRole, name)
        if why:
            # Visible but unusable: knowing WHY a layer is absent beats it silently not being there.
            item.setFlags(Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
        else:
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked)
        listing.addItem(item)
    layout.addWidget(listing)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText("Upload")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec_() != QDialog.Accepted:
        return None
    return [listing.item(i).data(Qt.UserRole) for i in range(listing.count())
            if listing.item(i).checkState() == Qt.Checked]
