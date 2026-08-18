"""Enum access that works on Qt5 and Qt6 alike.

QGIS 4 moves to Qt6, where enum members are only reachable through their enclosing scope:
`Qt.NoPen` becomes `Qt.PenStyle.NoPen`, `Qgis.Warning` becomes `Qgis.MessageLevel.Warning`. The
plugin has to run on both — our declared floor is QGIS 3.28, which is Qt5 — so it cannot simply be
rewritten to the new spelling and it cannot stay on the old one.

`enum()` asks for the scoped name and falls back to the flat one, which is exactly the overlap:
every Qt5 build that has `Qt.PenStyle.NoPen` gives the identical value as `Qt.NoPen`, and the few
that do not still answer the flat name. Verified against PyQt5 5.15 for every name this plugin uses.

TWO TRAPS THIS EXISTS TO AVOID, both found rather than imagined:

* **The scope is not always the class you are calling.** QGIS's Qt6 checker reports
  `QLineEdit.Password` as "add 'EchoMode' before 'Password'", which reads as `Qt.EchoMode.Password`
  — a name that does not exist. `EchoMode` belongs to `QLineEdit`. Applying the checker's advice
  literally breaks the connect dialog, so every scope here was resolved against the real class.
* **Not everything capitalised is an enum.** `QgsVectorFileWriter.SaveVectorOptions` and
  `QgsColorRampShader.ColorRampItem` are CLASSES. Scoping those would be a silent AttributeError.
"""
from __future__ import annotations


def enum(owner, scope: str, name: str):
    """`owner.scope.name` when this Qt has scoped enums, else `owner.name`.

    Resolved at the call site rather than cached in a module constant, because most of the QGIS
    classes involved are imported lazily inside functions — a module-level constant would have to
    import them at load time, which is the one thing `__init__.py` must not do.
    """
    holder = getattr(owner, scope, None)
    if holder is not None:
        value = getattr(holder, name, None)
        if value is not None:
            return value
    return getattr(owner, name)
