"""Argument groups and helpers shared by several command modules.

The styling flags live here because THREE commands take the same set — `portals add-layer`,
`portals style` and `layers style` (a layer's own default). Defining them once is not only less
code: it is the only way the three stay consistent, and inconsistency between them is exactly the
kind of thing nobody notices until someone's map is wrong.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from ...errors import ValidationError
from ...styles import (CLASSIFY_METHODS, COLOR_MODES, LINE_TYPES, MARKERS, RAMPS, build_style,
                       parse_categories, parse_classes, parse_number_list,
                       LINE_CAPS, LINE_JOINS, LABEL_FONTS)


def add_style_args(parser, raster: bool = True) -> None:
    """Every styling flag the API accepts, including the v1.1 data-driven symbology."""
    single = parser.add_argument_group(
        "symbology (single symbol)",
        "Only the flags you pass are changed; everything else keeps its current value.")
    single.add_argument("--color", help="main colour: polygon fill, line, or point (hex or name)")
    single.add_argument("--fill-opacity", type=float, help="polygon fill opacity, 0-1")
    single.add_argument("--outline-color",
                        help="outline colour, or 'none' for no outline at all")
    single.add_argument("--outline-width", type=float,
                        help="outline width. On POINTS a fraction of the radius (0-1, default "
                             "0.28) — a wide one on a small marker is how a ring is drawn. On "
                             "POLYGONS a width in px (default 1)")
    single.add_argument("--line-width", type=float, help="line width in px")
    single.add_argument("--radius", type=float, help="point radius in px")
    single.add_argument("--marker", choices=MARKERS, help="point marker shape")
    single.add_argument("--line-type", choices=LINE_TYPES, help="line dash pattern")
    single.add_argument("--opacity", type=float, help="layer opacity, 0-1")

    driven = parser.add_argument_group(
        "symbology (data-driven, v1.1)",
        "Colour or size from a feature property. --classify asks the instance to compute the "
        "breaks with the same code the editor and the published portal use.")
    driven.add_argument("--color-field", help="property to colour by")
    driven.add_argument("--color-mode", choices=COLOR_MODES,
                        help="single | graduated (numeric classes) | categorized (text values)")
    driven.add_argument("--classify", nargs="?", const="quantile", choices=CLASSIFY_METHODS,
                        help="compute classes from the data: quantile (default), equal, jenks")
    driven.add_argument("--classes", type=int, default=None,
                        help="how many classes to compute (2-100, default 5)")
    driven.add_argument("--ramp", choices=RAMPS, help="colour ramp for computed classes")
    driven.add_argument("--reverse-ramp", action="store_true",
                        help="run the ramp the other way (light end for the low values)")
    driven.add_argument("--class-breaks",
                        help="explicit classes instead of computing them: '0-10:#fee,10-50:#f00' "
                             "(* is an open edge)")
    driven.add_argument("--categories",
                        help="explicit categories: 'forest:#2c7,water:#39f'")
    driven.add_argument("--other-color", help="colour for values in no category")
    driven.add_argument("--size-field", help="property to size points/lines by")
    driven.add_argument("--size-stops", help="proportional size: 'value:size,value:size' "
                                             "(at least two, ascending)")
    driven.add_argument("--no-classification", action="store_true",
                        help="drop data-driven colouring and go back to a single symbol")

    three_d = parser.add_argument_group("3D")
    three_d.add_argument("--extrude", action="store_true", default=None,
                         help="extrude by a numeric field (polygons) or draw points as bars")
    three_d.add_argument("--no-extrude", action="store_true", help="turn 3D off")
    three_d.add_argument("--extrude-field", help="numeric property giving the height")
    three_d.add_argument("--extrude-scale", type=float, help="multiply the height by this")
    three_d.add_argument("--extrude-base", help="base height: a number, or a property name")
    three_d.add_argument("--extrude-color", help="override the extrusion colour")
    three_d.add_argument("--extrude-opacity", type=float, help="extrusion opacity, 0-1")
    three_d.add_argument("--extrude-radius", type=float,
                         help="POINT bar footprint radius in metres (default: derived from the "
                              "layer's own extent, because a fixed one is invisible on a world map)")

    if raster:
        rast = parser.add_argument_group("raster")
        rast.add_argument("--colormap", help="TiTiler colormap, e.g. viridis (see `layers colormaps`)")
        rast.add_argument("--reverse-colormap", dest="colormap_reverse", action="store_true",
                          default=None,
                          help="flip the palette (low values take the colour high values had)")
        rast.add_argument("--no-reverse-colormap", dest="colormap_reverse", action="store_false",
                          help="undo --reverse-colormap")
        rast.add_argument("--rescale", help="stretch as 'min,max' (see `layers stats` for a suggestion)")
        rast.add_argument("--algorithm", help="hillshade or contours (single-band)")
        rast.add_argument("--zfactor", type=float, help="hillshade vertical exaggeration")
        # Contours colours the data across its range with a built-in terrain ramp and draws the
        # lines on that, so --rescale is not decoration here: without it the whole raster renders
        # as one flat band. See services/titiler._contour_params.
        rast.add_argument("--increment", type=float,
                          help="contour interval, in the raster's own units (default 35)")
        rast.add_argument("--thickness", type=int,
                          help="contour line width in pixels (default 1)")
        rast.add_argument("--minz", type=float,
                          help="low end of the contour colour range (defaults to --rescale)")
        rast.add_argument("--maxz", type=float,
                          help="high end of the contour colour range (defaults to --rescale)")
        rast.add_argument("--bidx", help="band selection: '1' or '3,2,1' for an RGB composite")

    # The line and marker vocabulary MapLibre draws natively. Each round-trips exactly from QGIS.
    line = parser.add_argument_group("lines and markers")
    line.add_argument("--dash-pattern",
                      help="dash and gap lengths in MULTIPLES OF THE LINE WIDTH, e.g. '3,2' or "
                           "'3,2,1,2' — wins over --line-type")
    line.add_argument("--no-dash-pattern", action="store_true",
                      help="drop a custom dash pattern, leaving --line-type")
    line.add_argument("--line-cap", choices=LINE_CAPS, help="how a line ends")
    line.add_argument("--line-join", choices=LINE_JOINS, help="how a line turns a corner")
    line.add_argument("--line-offset", type=float,
                      help="draw the line this many pixels to one side (negative for the other)")
    line.add_argument("--marker-rotation", type=float, help="turn each marker, in degrees")
    line.add_argument("--marker-offset", help="move each marker, as 'x,y' in pixels")
    line.add_argument("--marker-opacity", type=float, help="the marker's own opacity, 0-1")

    # PICTURES FROM A FILE. These three keys already SURVIVED a CLI restyle — `build_style` merges
    # onto the existing style, so a marker rendered by the QGIS plugin was never dropped — but there
    # was no way to SET one without QGIS. A PNG or SVG on disk is the obvious other source, and the
    # renderers cannot tell the two apart: both arrive as the same data URI.
    pics = parser.add_argument_group("pictures (from a local image file)")
    pics.add_argument("--marker-image", metavar="FILE",
                      help="draw each point as this image instead of a generated shape")
    pics.add_argument("--fill-pattern", metavar="FILE",
                      help="tile this image across each polygon (it must tile seamlessly)")
    pics.add_argument("--line-marker", metavar="FILE",
                      help="repeat this image along each line, rotated with it")
    pics.add_argument("--centroid-marker", metavar="FILE",
                      help="place this image at each polygon's centre")
    for flag, what in (("--no-marker-image", "the point picture"),
                       ("--no-fill-pattern", "the polygon pattern"),
                       ("--no-line-marker", "the markers along the line"),
                       ("--no-centroid-marker", "the centre marker")):
        pics.add_argument(flag, action="store_true", help="remove {0}".format(what))
    pics.add_argument("--line-marker-spacing", type=float,
                      help="pixels between repeated line markers")

    # Scale range and "draws nothing" belong to the LAYER, not to one symbol.
    scope = parser.add_argument_group("where the layer draws")
    scope.add_argument("--min-zoom", type=float,
                       help="hide the layer below this zoom (QGIS's most-zoomed-OUT scale limit)")
    scope.add_argument("--max-zoom", type=float, help="hide the layer above this zoom")
    scope.add_argument("--no-symbol", action="store_true",
                       help="draw nothing, but keep the layer listed — QGIS's No symbols renderer")
    scope.add_argument("--symbol", dest="no_symbol", action="store_false", default=None,
                       help="undo --no-symbol")

    # LABELS. Their own group because a label is a second thing drawn for the same feature — its
    # own text, font, colour and zoom range — and it becomes its own MapLibre layer.
    lab = parser.add_argument_group("labels")
    lab.add_argument("--label-field", help="the attribute to label with — turns labelling ON")
    lab.add_argument("--no-labels", action="store_true", help="stop labelling this layer")
    lab.add_argument("--label-size", type=float, help="label text size in pixels")
    lab.add_argument("--label-color", help="label text colour")
    lab.add_argument("--label-font", choices=LABEL_FONTS,
                     help="a portal can only draw the fonts its glyph set contains")
    lab.add_argument("--label-halo-color", help="colour of the outline behind the text")
    lab.add_argument("--label-halo-width", type=float, help="halo width in pixels (0 for none)")
    lab.add_argument("--label-offset", help="move the text, as 'x,y' in pixels")
    lab.add_argument("--label-placement", choices=("point", "line"),
                     help="place the text at a point, or bend it along the line")
    lab.add_argument("--label-transform", choices=("none", "uppercase", "lowercase"))
    lab.add_argument("--label-max-width", type=float, help="wrap the text at this many ems")
    lab.add_argument("--label-allow-overlap", action="store_true",
                     help="draw every label even where they collide")
    lab.add_argument("--label-priority", type=float,
                     help="0-10, higher wins the space when labels collide")
    lab.add_argument("--label-min-zoom", type=float, help="hide the labels below this zoom")
    lab.add_argument("--label-max-zoom", type=float, help="hide the labels above this zoom")

    # RULES. A rule list is not something anyone types at a shell — it comes out of QGIS, through
    # the plugin — so the CLI's job is to move one around and to get rid of one, not to compose it
    # field by field. `--rules @file.json` is how a rule-based style is scripted into an instance.
    parser.add_argument("--rules",
                        help="a JSON list (or @file.json) of rule objects — "
                             '{label, expression, filter, style, minzoom, maxzoom} — usually '
                             "written by the QGIS plugin")
    parser.add_argument("--no-rules", action="store_true",
                        help="drop the rule list, leaving the layer's own single symbol")

    parser.add_argument("--style-json",
                        help="a JSON object (or @file.json) merged in last — the escape hatch for "
                             "anything these flags do not cover")


def style_from_args(args, client=None, layer_ref: Optional[Any] = None,
                    base: Optional[Dict[str, Any]] = None, out=None) -> Dict[str, Any]:
    """Turn the parsed styling flags into a style dict, classifying against the layer if asked."""
    style = dict(base or {})

    if getattr(args, "classify", None):
        if not getattr(args, "color_field", None):
            raise ValidationError(400, "--classify needs --color-field.")
        if client is None or layer_ref is None:  # pragma: no cover - guarded by the callers
            raise ValidationError(400, "Classification needs a layer to read.")
        from ... import styles as styles_mod
        style, stats = styles_mod.classify(
            client, layer_ref, args.color_field, mode=getattr(args, "color_mode", None),
            classes=getattr(args, "classes", None) or 5, method=args.classify,
            ramp=getattr(args, "ramp", None) or "viridis",
            reverse=bool(getattr(args, "reverse_ramp", False)), base=style)
        if out is not None:
            count = len(style.get("classes") or style.get("categories") or [])
            out.info("Classified {0} on {1}: {2} {3} from {4} values.".format(
                args.color_field, args.classify, count,
                "classes" if style.get("color_mode") == "graduated" else "categories",
                (stats or {}).get("count") or (stats or {}).get("total") or "the"))

    kwargs = {}  # type: Dict[str, Any]
    for name in ("color", "fill_opacity", "outline_color", "outline_width", "line_width",
                 "radius", "marker", "line_type", "colormap", "colormap_reverse",
                 "rescale", "algorithm", "zfactor", "increment", "thickness", "minz", "maxz",
                 "color_field", "color_mode", "size_field", "other_color", "size_stops",
                 "extrude_field", "extrude_scale", "extrude_base", "extrude_color",
                 "extrude_opacity", "extrude_radius",
                 "line_cap", "line_join", "line_offset", "marker_rotation", "marker_opacity",
                 "min_zoom", "max_zoom"):
        kwargs[name] = getattr(args, name, None)
    if getattr(args, "bidx", None):
        kwargs["bidx"] = [int(b) for b in str(args.bidx).replace(" ", "").split(",") if b]
    if getattr(args, "extrude", None):
        kwargs["extrude"] = True
    if getattr(args, "no_extrude", False):
        kwargs["extrude"] = False
    if getattr(args, "class_breaks", None):
        kwargs["classes"] = parse_classes(args.class_breaks)
    if getattr(args, "categories", None):
        kwargs["categories"] = parse_categories(args.categories)
    if getattr(args, "no_classification", False):
        kwargs["clear_classification"] = True
    for arg, key in (("marker_image", "marker_image"), ("fill_pattern", "fill_pattern"),
                     ("line_marker", "line_marker"), ("centroid_marker", "centroid_marker")):
        path = getattr(args, arg, None)
        if path:
            kwargs[key] = path
        if getattr(args, "no_" + arg, False):
            kwargs["no_" + key] = True
    if getattr(args, "line_marker_spacing", None) is not None:
        kwargs["line_marker_spacing"] = args.line_marker_spacing
    for name in ("dash_pattern", "marker_offset", "no_dash_pattern", "no_symbol"):
        value = getattr(args, name, None)
        if value is not None and value is not False:
            kwargs[name] = value
        elif name == "no_symbol" and value is False:
            kwargs[name] = False          # `--symbol` explicitly turns it back on
    labels = {}
    for arg, key in (("label_field", "field"), ("label_size", "size"), ("label_color", "color"),
                     ("label_font", "font"), ("label_halo_color", "halo_color"),
                     ("label_halo_width", "halo_width"), ("label_placement", "placement"),
                     ("label_transform", "transform"), ("label_max_width", "max_width"),
                     ("label_priority", "priority"), ("label_min_zoom", "minzoom"),
                     ("label_max_zoom", "maxzoom")):
        value = getattr(args, arg, None)
        if value is not None:
            labels[key] = value
    if getattr(args, "label_offset", None):
        labels["offset"] = parse_number_list(args.label_offset, "--label-offset", length=2)
    if getattr(args, "label_allow_overlap", False):
        labels["allow_overlap"] = True
    if labels:
        kwargs["labels"] = labels
    if getattr(args, "no_labels", False):
        kwargs["clear_labels"] = True
    if getattr(args, "rules", None):
        kwargs["rules"] = read_json_arg(args.rules, "--rules", allow_list=True)
    if getattr(args, "no_rules", False):
        kwargs["clear_rules"] = True

    style = build_style(style, **kwargs)

    extra = getattr(args, "style_json", None)
    if extra:
        style.update(read_json_arg(extra, "--style-json"))
    return style


def read_json_arg(value: str, label: str, allow_list: bool = False):
    """A JSON object given inline, or `@path` to read it from a file (or `@-` for stdin).

    `utf-8-sig` on the file: PowerShell's `>` writes UTF-16 or a BOM, which is how the reference
    script's `portal-set` used to fail on Windows with an unreadable JSON error.

    `allow_list` is for `--rules`, which is a JSON ARRAY rather than an object — the object check
    below is otherwise the thing that catches a style file with the wrong shape, and loosening it
    for everything would trade a clear message for a confusing one further down.
    """
    text = value
    if value.startswith("@"):
        path = value[1:]
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, "r", encoding="utf-8-sig") as fh:
                text = fh.read()
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValidationError(400, "{0} is not valid JSON: {1}".format(label, exc))
    if allow_list:
        if not isinstance(data, list):
            raise ValidationError(400, "{0} must be a JSON list.".format(label))
        return data
    if not isinstance(data, dict):
        raise ValidationError(400, "{0} must be a JSON object.".format(label))
    return data


def read_text_arg(value: str) -> str:
    """Text, or `@file` to read it from disk (Markdown for an About page, typically)."""
    if value.startswith("@"):
        path = value[1:]
        if path == "-":
            return sys.stdin.read()
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    return value


def write_json_file(path: str, payload: Any) -> None:
    """Write JSON as UTF-8 ourselves rather than letting the shell redirect it.

    PowerShell's `>` writes UTF-16 with a BOM, which then cannot be read back — the reference CLI
    grew an explicit output-file argument for exactly this reason, and so does this one.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")


def confirm(out, question: str, assume_yes: bool = False, expect: Optional[str] = None) -> bool:
    """Ask before something irreversible. `--yes` skips it; a non-interactive shell refuses.

    Refusing rather than assuming yes is deliberate: a script piping into this has not consented to
    a delete, and `--yes` is one character to add when it has.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        out.error("Refusing to {0} without --yes when not attached to a terminal.".format(question))
        return False
    if expect:
        answer = input("{0}\nType {1!r} to confirm: ".format(question, expect)).strip()
        return answer == expect
    answer = input("{0} [y/N] ".format(question)).strip().lower()
    return answer in ("y", "yes")


def layer_ref_arg(parser, name: str = "layer", help_text: Optional[str] = None) -> None:
    parser.add_argument(name, help=help_text or
                        "layer id, uid, or name (a unique part of the name is enough)")
    parser.add_argument("--type", dest="layer_type", choices=["vector", "raster"],
                        help="disambiguate when a vector and a raster share a name")


def resolve_layer(ctx, args, ref_attr: str = "layer", public_ok: bool = False) -> Dict[str, Any]:
    """Find the layer a command was pointed at, by id, uid or name.

    `public_ok` marks a command that works on public data alone (downloads, links to shared
    artifacts). With no credential those resolve through the instance's PUBLIC INDEX instead of the
    authenticated layer list, so `geodeploy --url … layers download roads` works for someone who
    has no account — which is the whole point of a public layer.
    """
    ref = getattr(args, ref_attr)
    kind = getattr(args, "layer_type", None)
    info = ctx.resolved
    if public_ok and not (info.token or info.jwt):
        return ctx.client(auth_required=False).layers.resolve_public(ref, kind)
    return ctx.client().layers.resolve(ref, kind)


def parse_fields(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    return [f.strip() for f in value.split(",") if f.strip()]
