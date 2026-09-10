"""QGIS rule-based rendering ⇄ GeoDeploy's `style.rules`.

## What a rule is, on each side

A QGIS rule is a filter, a symbol, a label and an optional scale range, arranged in a TREE where a
parent's filter applies to everything beneath it. MapLibre has no tree and no rules: it has layers,
each with one filter, one paint and its own zoom range. The two meet exactly — a QGIS rule tree
flattens to a list of layers, one per leaf, each carrying the AND of the filters above it and the
narrowest scale range on its path.

So `style.rules` is that list, and each entry is deliberately *not* raw MapLibre paint:

    {"label":      "Motorway",                              what the legend says
     "expression": "\\"class\\" = 'motorway'",                 the QGIS source, carried verbatim
     "filter":     ["==", ["get", "class"], "motorway"],    what the web renders
     "style":      {...},                                   the friendly single-symbol keys
     "minzoom":    12.0, "maxzoom": 16.0}                   from the rule's scale range

`style` holds the same friendly keys a single-symbol layer uses, so the server builds each rule's
render layer with the code it already has, the portal editor can show and eventually edit them, and
the CLI can set one. Emitting finished MapLibre paint here instead would mean a second renderer in
the plugin, drifting from the four that already have to agree.

**Both the expression and the filter are stored, and that is the point.** The filter is what
MapLibre draws. The expression is what QGIS gets back — a round trip should hand somebody the text
they typed, not a re-rendering of it. When a rule is authored in GeoDeploy instead, there is no
expression to carry and `expressions.from_maplibre` reconstructs one.

## Order

`rules[0]` is drawn FIRST, i.e. underneath — QGIS's own rule order, kept rather than reversed. Note
that this is the opposite of `layer_configs`, where index 0 is the top of the list and the top of
the map; a rule list is read as a drawing order and a layer list as a stacking order, and matching
QGIS here is what keeps a pushed layer looking like the one on screen.
"""
from __future__ import annotations

from geodeploy import expressions
from geodeploy.styles import zoom_for_scale, scale_for_zoom

try:                                    # a package, inside QGIS
    from . import symbology
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    import symbology

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import QgsRuleBasedRenderer
    QGIS_RULES = True
except ImportError:                     # pragma: no cover
    QGIS_RULES = False


def is_rule_based(style) -> bool:
    """True when this style is drawn as a list of rules rather than one symbol or a classification."""
    return bool(isinstance(style, dict) and style.get("rules"))


# ── QGIS → GeoDeploy ─────────────────────────────────────────────────────────────────────────────

def from_qgis(qgis_layer, renderer):
    """`(rules, notes)` for a `QgsRuleBasedRenderer`, or `(None, notes)` when nothing translated.

    `notes` names every rule that did NOT travel and why, which is what a fidelity report prints.
    A rule whose filter falls outside the expression subset is SKIPPED rather than drawn unfiltered:
    a rule that draws everything is not a degraded version of a rule that draws some things, it is a
    different map.
    """
    notes = []
    if not QGIS_RULES or renderer is None:
        return None, notes
    root = getattr(renderer, "rootRule", None)
    if not callable(root):
        return None, notes
    rules = []
    _walk(root(), qgis_layer, None, None, None, rules, notes, depth=0)
    return (rules or None), notes


def _walk(rule, qgis_layer, parent_filter, parent_min, parent_max, out, notes, depth):
    """Depth-first over the rule tree, carrying the parent's filter and scale range down.

    QGIS applies a parent rule's filter to every descendant, so a child's real condition is the AND
    of everything above it. Scale ranges intersect the same way — the narrowest window on the path
    is the one the feature is actually drawn in.
    """
    if depth > 12:                      # a tree this deep is a loop, not a legend
        notes.append("rule nesting deeper than 12 levels was not read")
        return

    children = list(rule.children()) if hasattr(rule, "children") else []

    # Sibling filters are needed before any ELSE among them can be expressed, so translate the whole
    # level first and only then emit — an ELSE is "none of the others", and it cannot be written
    # until the others are known.
    translated = []
    for child in children:
        if hasattr(child, "active") and not child.active():
            continue                    # an unchecked rule draws nothing in QGIS either
        if _is_else(child):
            translated.append((child, "else", None))
            continue
        text = (child.filterExpression() or "").strip()
        if not text:
            translated.append((child, "none", None))
            continue
        node, reason = expressions.try_maplibre(text)
        if reason is not None:
            notes.append("{0}: {1}".format(_label(child), reason))
            continue
        translated.append((child, "filter", node))

    non_else = [node for _c, kind, node in translated if kind == "filter" and node is not None]

    for child, kind, node in translated:
        if kind == "else":
            node = expressions.negate(non_else)
            if node is None and non_else:
                # Every sibling matched everything, so the ELSE draws nothing. Emitting it would
                # cover the map in the catch-all symbol.
                notes.append("{0}: an ELSE rule with no siblings left to exclude".format(
                    _label(child)))
                continue
        combined = _and(parent_filter, node)
        lo, hi = _scales(child, parent_min, parent_max)

        symbol = child.symbol() if hasattr(child, "symbol") else None
        if symbol is not None:
            entry = {"label": _label(child), "filter": combined}
            text = (child.filterExpression() or "").strip()
            if kind == "filter" and text:
                # THE SOURCE, carried. QGIS gets this back verbatim rather than a re-rendering.
                entry["expression"] = text
            try:
                entry["style"] = symbology._style_from_symbol(symbol)
            except Exception as exc:    # noqa: BLE001 - one rule, not the whole renderer
                notes.append("{0}: its symbol could not be read ({1})".format(
                    _label(child), exc))
                entry["style"] = {}
            if lo is not None:
                entry["minzoom"] = lo
            if hi is not None:
                entry["maxzoom"] = hi
            out.append(entry)

        if hasattr(child, "children") and child.children():
            _walk(child, qgis_layer, combined, lo, hi, out, notes, depth + 1)


def _is_else(rule) -> bool:
    fn = getattr(rule, "isElse", None)
    try:
        return bool(fn()) if callable(fn) else False
    except Exception:                   # noqa: BLE001  # nosec B110 - an unreadable flag is not an else
        return False


def _label(rule) -> str:
    try:
        return str(rule.label() or rule.filterExpression() or "rule")
    except Exception:                   # noqa: BLE001
        return "rule"


def _and(parent, child):
    """The AND of a parent filter and a child's, flattened, with `None` meaning "no condition"."""
    if parent is None:
        return child
    if child is None:
        return parent
    parts = []
    for node in (parent, child):
        if isinstance(node, list) and node and node[0] == "all":
            parts.extend(node[1:])
        else:
            parts.append(node)
    return ["all"] + parts


def _scales(rule, parent_min, parent_max):
    """`(minzoom, maxzoom)` for a rule, intersected with its parent's.

    QGIS states a scale RANGE and MapLibre a zoom range, and they run in opposite directions — see
    `styles.zoom_for_scale`. Intersecting means taking the LATER minzoom and the EARLIER maxzoom,
    because a nested rule can only narrow what its parent already allowed.
    """
    lo = hi = None
    try:
        if not hasattr(rule, "minimumScale"):
            lo = hi = None
        else:
            lo = zoom_for_scale(rule.minimumScale())
            hi = zoom_for_scale(rule.maximumScale())
    except Exception:                   # noqa: BLE001 - a scale is never worth failing a rule
        lo = hi = None
    lo = parent_min if lo is None else (lo if parent_min is None else max(lo, parent_min))
    hi = parent_max if hi is None else (hi if parent_max is None else min(hi, parent_max))
    return lo, hi


# ── GeoDeploy → QGIS ─────────────────────────────────────────────────────────────────────────────

def to_qgis(qgis_layer, style) -> bool:
    """Give `qgis_layer` a `QgsRuleBasedRenderer` built from `style["rules"]`. True when set."""
    if not QGIS_RULES or not is_rule_based(style):
        return False
    try:
        root = QgsRuleBasedRenderer.Rule(None)
        made = 0
        for entry in style["rules"]:
            if not isinstance(entry, dict):
                continue
            rule_style = dict(style)
            rule_style.pop("rules", None)
            rule_style.update(entry.get("style") or {})
            symbol = symbology._symbol_for(qgis_layer, rule_style.get("color"), rule_style)
            if symbol is None:
                continue
            rule = QgsRuleBasedRenderer.Rule(symbol)
            text = _expression_for(entry)
            if text:
                rule.setFilterExpression(text)
            rule.setLabel(str(entry.get("label") or ""))
            # QGIS wants scale DENOMINATORS, and the two ends swap - see `styles.zoom_for_scale`.
            lo = scale_for_zoom(entry.get("minzoom")) if entry.get("minzoom") is not None else None
            hi = scale_for_zoom(entry.get("maxzoom")) if entry.get("maxzoom") is not None else None
            if lo is not None and hasattr(rule, "setMinimumScale"):
                rule.setMinimumScale(lo)
            if hi is not None and hasattr(rule, "setMaximumScale"):
                rule.setMaximumScale(hi)
            root.appendChild(rule)
            made += 1
        if not made:
            return False
        qgis_layer.setRenderer(QgsRuleBasedRenderer(root))
        qgis_layer.triggerRepaint()
        return True
    except Exception as exc:            # noqa: BLE001 - a style must never stop a layer loading
        symbology._log("Could not build the rule-based renderer: {0}: {1}".format(
            type(exc).__name__, exc))
        return False


def _expression_for(entry) -> str:
    """The QGIS filter text for one rule.

    The CARRIED source wins: a rule that came from QGIS goes back as the text the author wrote,
    punctuation and all. Only a rule authored in GeoDeploy — which has a filter and no source — is
    reconstructed, and a filter that cannot be reconstructed leaves the rule UNFILTERED with a note,
    because a rule drawn too widely is at least visible and correctable, where a dropped one is not.
    """
    text = (entry.get("expression") or "").strip()
    if text:
        return text
    node = entry.get("filter")
    if node is None:
        return ""
    try:
        return expressions.from_maplibre(node)
    except Exception as exc:            # noqa: BLE001
        symbology._log("Rule {0!r} has a filter QGIS cannot be given ({1}); it is shown "
                       "unfiltered.".format(entry.get("label") or "", exc))
        return ""
