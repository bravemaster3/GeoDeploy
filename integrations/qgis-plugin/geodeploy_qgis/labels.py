"""QGIS labelling ⇄ GeoDeploy's `style.labels`.

## Why labels are their own module and their own style block

A label is a second thing drawn for the same feature. It has its own text, its own font, its own
colour, its own placement and its own scale range, and none of that belongs in a symbol. QGIS keeps
it in `QgsPalLayerSettings`, hung off the layer beside the renderer; GeoDeploy keeps it in
`style.labels` and emits it as its own MapLibre `symbol` layer.

## What travels, and the two places it does not

`QgsPalLayerSettings` publishes **125 data-defined properties**. Most of them describe things
MapLibre has: the text, the font, the size, the colour, the buffer (which is a halo), the offset,
the rotation, the wrap width, the letter spacing, the capitalisation, whether labels may overlap,
the priority, and the scale range. Those are read and written here and round-trip exactly.

Two families do not, and both are named rather than approximated:

* **Shadows and background shapes.** MapLibre draws a halo and nothing else — no drop shadow, no
  rounded rectangle behind the text. Faking a shadow with a second offset label layer would put
  real text under the real text, which breaks selection and collision.
* **Callouts.** The leader line QGIS draws from a displaced label back to its feature is a second
  geometry, and there is nowhere to put it.

## Fonts are the sharp edge

MapLibre renders text from GLYPH PBFs, and a fontstack the glyph source does not have draws
**nothing at all** — no error, no fallback, no text. So a label's font is not carried verbatim: it
is mapped onto the small set of stacks the instance can actually serve (`LABEL_FONTS`), keeping the
weight and slant where they exist. Carrying "Helvetica Neue Condensed" faithfully would produce a
map with no labels on it and nothing to say why.
"""
from __future__ import annotations

from geodeploy import expressions
from geodeploy.styles import zoom_for_scale, scale_for_zoom

try:                                    # a package, inside QGIS
    from . import symbology
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    import symbology

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import (QgsPalLayerSettings, QgsTextFormat, QgsTextBufferSettings,
                           QgsVectorLayerSimpleLabeling)
    QGIS_LABELS = True
except ImportError:                     # pragma: no cover
    QGIS_LABELS = False

#: The faces GeoDeploy SHIPS. Used only when the instance has not been asked — see `set_available`.
LABEL_FONTS = ("Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic")
DEFAULT_FONT = "Noto Sans Regular"

#: What THIS instance can actually draw, filled in from `GET /api/fonts` when the plugin connects.
#: A module-level cache rather than a parameter threaded through six functions: the value belongs to
#: the connection, changes once per connect, and every reader wants the same answer.
_AVAILABLE = list(LABEL_FONTS)

#: Families QGIS users reach for, grouped by what they ARE, so a substitution picks a face of the
#: right kind rather than always landing on the sans. Lowercased; matched as a substring, which is
#: what catches "Times New Roman PS MT" and "DejaVu Serif Condensed".
_SERIF = ("serif", "times", "georgia", "garamond", "book", "roman", "cambria", "palatino",
          "minion", "baskerville", "century", "constantia")
_MONO = ("mono", "courier", "consolas", "menlo", "monaco", "inconsolata", "source code")


def set_available(fonts) -> None:
    """Record what this instance can draw. Called after connecting; safe to call with junk.

    Without this the plugin would map every QGIS font onto the three faces GeoDeploy happens to
    ship, even on an instance where the operator had installed Noto Serif — a substitution the
    server was ready to render correctly and the plugin had already thrown away.
    """
    global _AVAILABLE
    names = [str(f) for f in (fonts or []) if str(f).strip()]
    _AVAILABLE = names or list(LABEL_FONTS)


def available() -> list:
    return list(_AVAILABLE)

#: QGIS renders label sizes in points by default; GeoDeploy states them in CSS pixels like every
#: other size, and `symbology.CSS_PX_TO_POINTS` is the same constant the symbol sizes use.
_PT = symbology.CSS_PX_TO_POINTS


def _value(settings, name, default=None):
    """A `QgsPalLayerSettings` member, whether it is an attribute or a method.

    **`QgsPalLayerSettings` mixes the two**, and the mix is not guessable: `fieldName`,
    `isExpression`, `xOffset`, `priority`, `autoWrapLength`, `scaleVisibility` and friends are plain
    PUBLIC ATTRIBUTES, while `format()` is a method. Calling an attribute raises
    `TypeError: 'str' object is not callable`, which the caller's blanket handler turns into "this
    layer has no labels" — silently, which is exactly how the first version of this module read
    nothing at all from a perfectly well labelled layer.
    """
    if not hasattr(settings, name):
        return default
    member = getattr(settings, name)
    if callable(member):
        try:
            return member()
        except Exception:               # noqa: BLE001
            return default
    return member


def has_labels(style) -> bool:
    labels = (style or {}).get("labels")
    return bool(isinstance(labels, dict) and labels.get("enabled"))


# ── QGIS → GeoDeploy ─────────────────────────────────────────────────────────────────────────────

def from_qgis(qgis_layer):
    """`(labels, notes)` for a labelled layer, or `(None, notes)` when it has none."""
    notes = []
    if not QGIS_LABELS or qgis_layer is None:
        return None, notes
    try:
        if not qgis_layer.labelsEnabled():
            return None, notes
        labeling = qgis_layer.labeling()
    except Exception:                   # noqa: BLE001 - a raster or a broken layer has no labelling
        return None, notes
    if labeling is None:
        return None, notes

    kind = type(labeling).__name__
    if kind == "QgsRuleBasedLabeling":
        # RULE-BASED LABELLING is a tree like rule-based rendering, and one MapLibre symbol layer
        # per rule is the same shape — but a label layer also carries text, and the rules usually
        # differ in FONT rather than in which features they label. Reading the first rule's
        # settings is a real approximation, so it says so rather than looking complete.
        settings = _first_rule_settings(labeling)
        if settings is None:
            return None, notes
        notes.append("its labels are rule-based; the first rule's text and font were taken and the "
                     "other rules were not")
    else:
        try:
            settings = labeling.settings()
        except Exception:               # noqa: BLE001
            return None, notes
    if settings is None:
        return None, notes

    labels = {"enabled": True}
    _read_text(settings, labels, notes)
    _read_format(settings, labels, notes)
    _read_placement(settings, labels)
    _read_scope(settings, labels)
    return labels, notes


def _first_rule_settings(labeling):
    try:
        for rule in labeling.rootRule().children():
            if rule.settings() is not None:
                return rule.settings()
    except Exception:                   # noqa: BLE001
        pass
    return None


def _read_text(settings, labels: dict, notes: list) -> None:
    """The `text-field`: a plain attribute, or an expression put through the translator."""
    field = str(_value(settings, "fieldName", "") or "").strip()
    if not field:
        return
    if bool(_value(settings, "isExpression", False)):
        node, reason = expressions.try_maplibre(field)
        if node is None:
            # A label expression that cannot travel must not become a field read of the expression
            # TEXT, which is what a naive fallback would do — every feature labelled with the same
            # unreadable string.
            notes.append("its label expression ({0}) {1}, so the layer was sent unlabelled"
                         .format(field, reason))
            labels.clear()
            return
        labels["expression"] = node
        labels["qgis_expression"] = field
    else:
        labels["field"] = field


def _read_format(settings, labels: dict, notes: list) -> None:
    """Font, size, colour, opacity and the buffer, which is MapLibre's halo."""
    try:
        fmt = settings.format()
    except Exception:                   # noqa: BLE001
        return
    try:
        font = fmt.font()
        family = font.family()
        labels["font"] = _fontstack(family, font.bold(), font.italic(), notes)
        # THE ORIGINAL FAMILY, CARRIED. The portal can only draw the stacks its glyph set contains,
        # so `font` above is a substitution — but QGIS draws with real system fonts and has no such
        # limit, so there is no reason for a round trip to cost somebody their typeface. Stored
        # beside the mapped stack and handed straight back on the way in; the same device as a
        # rule's `expression` and 2.5D's `qgis25d`.
        if family and family.strip():
            labels["qgis_font"] = {"family": family, "bold": bool(font.bold()),
                                   "italic": bool(font.italic())}
    except Exception:                   # noqa: BLE001
        labels["font"] = DEFAULT_FONT
    size = symbology._number(_value(fmt, "size", None), None)
    if size:
        labels["size"] = round(size / _PT, 2)
    try:
        labels["color"] = symbology._hex(fmt.color())
    except Exception:                   # noqa: BLE001
        pass
    opacity = symbology._number(_value(fmt, "opacity", None), None)
    if opacity is not None and opacity < 1.0:
        labels["opacity"] = round(opacity, 3)

    try:
        buffer_settings = fmt.buffer()
        if buffer_settings.enabled():
            width = symbology._number(buffer_settings.size(), 0)
            if width:
                labels["halo_width"] = round(width / _PT, 2)
                labels["halo_color"] = symbology._hex(buffer_settings.color())
    except Exception:                   # noqa: BLE001 - no buffer is not an error
        pass

    for getter, key, note in (("shadow", None, "its label shadow"),
                              ("background", None, "its label background shape")):
        try:
            block = getattr(fmt, getter)()
            if block is not None and block.enabled():
                notes.append("{0} has no MapLibre equivalent and was not carried".format(note))
        except Exception:               # noqa: BLE001
            pass


def _fontstack(family: str, bold: bool, italic: bool, notes: list) -> str:
    """A QGIS font family onto a face this instance can actually draw.

    NOT carried verbatim, deliberately: MapLibre draws NOTHING for a face its glyphs do not contain,
    so a faithful "Helvetica Neue Condensed" would produce a map with no labels on it and nothing to
    say why. The original family is kept in `labels.qgis_font` and handed straight back to QGIS, so
    this substitution costs the WEB rendering only.

    Matched in three passes, most specific first: the exact face, then the same FAMILY at the right
    weight and slant, then the right KIND — a serif stays a serif and a monospace stays a monospace,
    which is most of what a typeface choice communicates in a label.
    """
    have = available()
    wanted_weight = "Bold" if bold else ("Italic" if italic else "Regular")
    name = (family or "").strip()

    # 1. The face itself, under the name the instance uses for it.
    for candidate in ("{0} {1}".format(name, wanted_weight), name):
        if candidate in have:
            return candidate

    # 2. The same family, at the weight and slant asked for.
    lowered = name.lower()
    same_family = [f for f in have if lowered and f.lower().startswith(lowered.split()[0].lower())]
    for face in same_family:
        if face.endswith(wanted_weight):
            return face
    if same_family:
        return same_family[0]

    # 3. The right KIND of face — serif, monospace or sans.
    kind = ("Serif" if any(k in lowered for k in _SERIF)
            else "Mono" if any(k in lowered for k in _MONO) else "Sans")
    of_kind = [f for f in have if kind.lower() in f.lower()] or list(have)
    chosen = next((f for f in of_kind if f.endswith(wanted_weight)), None) or of_kind[0]

    if name and chosen.lower() != lowered:
        notes.append("its label font ({0}) was drawn as {1} — a portal can only draw the faces its "
                     "glyph set contains, and QGIS keeps the original either way".format(
                         family, chosen))
    return chosen


def _read_placement(settings, labels: dict) -> None:
    """Offset, rotation, wrapping, capitalisation, overlap and priority."""
    x = symbology._number(_value(settings, "xOffset", 0), 0)
    y = symbology._number(_value(settings, "yOffset", 0), 0)
    if x or y:
        # QGIS's Y grows DOWNWARD for a label offset and MapLibre's `text-offset` does too, so the
        # sign is carried straight through — unlike the line offset, which is mirrored.
        labels["offset"] = [round(x / _PT, 3), round(y / _PT, 3)]

    angle = symbology._number(_value(settings, "angleOffset", 0), 0)
    if angle:
        labels["rotation"] = round(angle % 360, 3)

    wrap = symbology._number(_value(settings, "autoWrapLength", 0), 0)
    if wrap:
        # QGIS wraps at a CHARACTER count and MapLibre at a width in ems; one em is roughly one
        # character's advance for a proportional face, which makes this close and not exact.
        labels["max_width"] = float(wrap)

    try:
        cap = int(settings.format().capitalization())
        labels["transform"] = {1: "uppercase", 2: "lowercase"}.get(cap, "none")
        if labels["transform"] == "none":
            labels.pop("transform")
    except Exception:                   # noqa: BLE001
        pass

    try:
        spacing = symbology._number(settings.format().font().letterSpacing(), 0)
        if spacing:
            labels["letter_spacing"] = round(spacing / 100.0, 3)
    except Exception:                   # noqa: BLE001
        pass

    if _value(settings, "displayAll", False):
        labels["allow_overlap"] = True
    priority = symbology._number(_value(settings, "priority", None), None)
    if priority is not None:
        labels["priority"] = priority

    placement = str(_value(settings, "placement", "")).lower()
    if "line" in placement or "curved" in placement or "perimeter" in placement:
        labels["placement"] = "line"


def _read_scope(settings, labels: dict) -> None:
    """A label's OWN scale range, which QGIS keeps separately from the layer's."""
    try:
        if not _value(settings, "scaleVisibility", False):
            return
        lo = zoom_for_scale(_value(settings, "minimumScale", 0))
        hi = zoom_for_scale(_value(settings, "maximumScale", 0))
        if lo is not None:
            labels["minzoom"] = lo
        if hi is not None:
            labels["maxzoom"] = hi
    except Exception:                   # noqa: BLE001 - a scale range is never worth failing a read
        pass


# ── GeoDeploy → QGIS ─────────────────────────────────────────────────────────────────────────────

def to_qgis(qgis_layer, style) -> bool:
    """Label `qgis_layer` the way `style.labels` describes. True when labelling was set.

    A style with no labels turns labelling OFF rather than leaving it standing: switching labels off
    in GeoDeploy and reopening the layer has to actually switch them off, or the two disagree and
    the next push argues about which is right. Same reasoning as `apply_3d`.
    """
    if not QGIS_LABELS or qgis_layer is None:
        return False
    if not hasattr(qgis_layer, "setLabelsEnabled"):
        return False
    if not has_labels(style):
        try:
            qgis_layer.setLabelsEnabled(False)
        except Exception:               # noqa: BLE001  # nosec B110
            pass
        return False

    labels = style["labels"]
    try:
        from qgis.PyQt.QtGui import QColor, QFont
        settings = QgsPalLayerSettings()

        expression = (labels.get("qgis_expression") or "").strip()
        if expression:
            settings.fieldName = expression
            settings.isExpression = True
        elif labels.get("expression") is not None:
            # Authored in GeoDeploy: rebuild QGIS text from the MapLibre expression, the same way
            # a rule's filter is rebuilt when it has no carried source.
            try:
                settings.fieldName = expressions.from_maplibre(labels["expression"])
                settings.isExpression = True
            except Exception:           # noqa: BLE001 - fall back to the plain field, if any
                settings.fieldName = str(labels.get("field") or "")
        else:
            settings.fieldName = str(labels.get("field") or "")
        if not settings.fieldName:
            return False

        fmt = QgsTextFormat()
        # THE CARRIED FAMILY WINS. A label that came from QGIS goes back in the typeface its author
        # chose, not in the stack the portal had to substitute to draw it. Only a label authored in
        # GeoDeploy — which has no carried font — falls back to the stack name.
        carried = labels.get("qgis_font")
        if isinstance(carried, dict) and carried.get("family"):
            font = QFont(str(carried["family"]))
            font.setBold(bool(carried.get("bold")))
            font.setItalic(bool(carried.get("italic")))
        else:
            font = QFont(_family_of(labels.get("font")))
            font.setBold("Bold" in str(labels.get("font") or ""))
            font.setItalic("Italic" in str(labels.get("font") or ""))
        spacing = symbology._number(labels.get("letter_spacing"), None)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing
                                  if hasattr(QFont, "SpacingType") else 0, 100 + spacing * 100)
        fmt.setFont(font)
        fmt.setSize(symbology._number(labels.get("size"), 12) * _PT)
        if labels.get("color"):
            fmt.setColor(QColor(labels["color"]))
        opacity = symbology._number(labels.get("opacity"), None)
        if opacity is not None:
            fmt.setOpacity(max(0.0, min(1.0, opacity)))

        halo = symbology._number(labels.get("halo_width"), 0)
        if halo:
            buffer_settings = QgsTextBufferSettings()
            buffer_settings.setEnabled(True)
            buffer_settings.setSize(halo * _PT)
            buffer_settings.setColor(QColor(labels.get("halo_color") or "#ffffff"))
            fmt.setBuffer(buffer_settings)
        settings.setFormat(fmt)

        offset = labels.get("offset")
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            settings.xOffset = symbology._number(offset[0], 0) * _PT
            settings.yOffset = symbology._number(offset[1], 0) * _PT
        rotation = symbology._number(labels.get("rotation"), None)
        if rotation:
            settings.angleOffset = rotation
        width = symbology._number(labels.get("max_width"), None)
        if width:
            settings.autoWrapLength = int(width)
        if labels.get("allow_overlap"):
            settings.displayAll = True
        priority = symbology._number(labels.get("priority"), None)
        if priority is not None:
            settings.priority = int(max(0, min(10, priority)))

        lo, hi = labels.get("minzoom"), labels.get("maxzoom")
        if lo is not None or hi is not None:
            settings.scaleVisibility = True
            if lo is not None:
                settings.minimumScale = scale_for_zoom(lo)
            if hi is not None:
                settings.maximumScale = scale_for_zoom(hi)

        qgis_layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        qgis_layer.setLabelsEnabled(True)
        qgis_layer.triggerRepaint()
        return True
    except Exception as exc:            # noqa: BLE001 - labelling must never stop a layer loading
        symbology._log("Could not apply the labels: {0}: {1}".format(type(exc).__name__, exc))
        return False


def _family_of(stack) -> str:
    """The family name out of a fontstack — `"Noto Sans Bold"` is the Noto Sans family, bold."""
    name = str(stack or DEFAULT_FONT)
    for suffix in (" Regular", " Bold", " Italic"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name
