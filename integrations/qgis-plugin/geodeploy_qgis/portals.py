"""A portal as a QGIS layer group, and back again.

A portal IS an ordered, styled list of layers — which is exactly what a QGIS group is. So the two
map onto each other directly, and the useful workflow falls out: open a portal, restyle it with
QGIS's tools, push it back. No web editor in the middle.

Three things make the round trip trustworthy rather than approximate:

* **Identity is carried, not guessed.** Every layer added from a portal remembers which GeoDeploy
  layer it is, in a custom property. Matching by NAME would break the moment someone renames a
  layer in QGIS — which is the first thing anyone does — and would silently push the wrong style
  onto the wrong layer. A layer without that property was not added from the instance, and is
  refused by name rather than uploaded behind the user's back.
* **The group remembers its portal.** Pushing again UPDATES the portal it came from instead of
  creating a duplicate. A group built from scratch has no portal, so it makes one.
* **Order is preserved in both directions.** `layer_configs[0]` is the top of the portal's list and
  draws on top; QGIS's group index 0 is likewise the top. They correspond exactly, and the
  conversion is a straight pass rather than a reversal — the kind of detail that is invisible until
  a published map comes out upside down.
"""
from __future__ import annotations

from .connection import GeoDeployError


def _note(message: str) -> None:
    """Say something in the QGIS log. Imported lazily because `symbology` imports nothing from here
    but this module must not require QGIS to be importable at all — the tests load it standalone."""
    try:
        from .symbology import _log
        _log(message)
    except Exception:                   # noqa: BLE001 - a log line is never worth an exception
        pass

# Namespaced so nothing else in a QGIS project collides with them.
P_LAYER_ID = "geodeploy/layer_id"
P_LAYER_TYPE = "geodeploy/layer_type"
P_INSTANCE = "geodeploy/instance"
P_PORTAL_ID = "geodeploy/portal_id"
P_PORTAL_TITLE = "geodeploy/portal_title"
P_FOLDER_ID = "geodeploy/folder_id"


class PortalError(Exception):
    """Explains, in words a user can act on, why a portal cannot be opened or pushed."""


def tag_layer(qgis_layer, instance_url: str, layer_id, layer_type: str) -> None:
    """Record which GeoDeploy layer this is, so a later push knows what it is looking at."""
    qgis_layer.setCustomProperty(P_LAYER_ID, str(layer_id))
    qgis_layer.setCustomProperty(P_LAYER_TYPE, layer_type)
    qgis_layer.setCustomProperty(P_INSTANCE, instance_url)


def layer_identity(qgis_layer):
    """`(layer_id, layer_type)` for a layer added from an instance, or None."""
    lid = qgis_layer.customProperty(P_LAYER_ID)
    ltype = qgis_layer.customProperty(P_LAYER_TYPE)
    if lid in (None, "") or ltype in (None, ""):
        return None
    try:
        return (int(lid), str(ltype))
    except (TypeError, ValueError):
        return None


def group_portal(group):
    """`(portal_id, title)` when this group came from a portal, else `(None, title)`."""
    pid = group.customProperty(P_PORTAL_ID)
    title = group.customProperty(P_PORTAL_TITLE) or group.name()
    try:
        return (int(pid), title) if pid not in (None, "") else (None, title)
    except (TypeError, ValueError):
        return (None, title)


def _folder_id(node) -> str:
    """A stable id for a folder, reused across pushes so the portal's own tree keeps its identity.

    Stored on the QGIS group the first time it is needed: regenerating it on every push would make
    the folder look new to anything downstream that tracks it.
    """
    existing = node.customProperty(P_FOLDER_ID) if hasattr(node, "customProperty") else None
    if existing:
        return str(existing)
    import uuid
    made = "g" + uuid.uuid4().hex[:8]
    try:
        node.setCustomProperty(P_FOLDER_ID, made)
    except Exception:                   # noqa: BLE001 - an unstorable id is still usable once
        pass
    return made


def _expanded(node) -> bool:
    try:
        return bool(node.isExpanded())
    except Exception:                   # noqa: BLE001 - older API; assume open
        return True


def flatten_tree(tree) -> list:
    """Every leaf of a folder tree, depth-first, in order — the portal's drawing order."""
    out = []
    for node in (tree or []):
        if isinstance(node, dict) and node.get("children") is not None:
            out.extend(flatten_tree(node.get("children")))
        elif isinstance(node, dict) and node.get("layer_id") is not None:
            out.append(node)
    return out


def configs_from_published_style(style_doc: dict, style_from_legend) -> list[dict]:
    """A published portal's layer_configs, rebuilt from its PUBLIC style.json.

    Reading a portal through the API needs a token. Looking at one does not — a published portal is
    public by definition — and being unable to open a public portal in QGIS without an account
    would contradict the whole point of anonymous browsing. `/portals/<slug>/style.json` is served
    to anyone and carries what the portal actually draws.

    Two things this has to get right:

    * **Order is REVERSED.** MapLibre draws later layers on top, so the style lists bottom-to-top,
      while `layer_configs[0]` is the TOP of the portal's list. Measured on a live portal: configs
      [18, 16, 11, 15] are baked as [15, 11, 16, 18]. Reconstructing without reversing would open
      every anonymous portal upside down — and then push it back that way.
    * **One layer can bake into SEVERAL MapLibre layers** (a fill plus its outline). Only the first
      carries the full metadata, so entries are de-duplicated by layer id, keeping the first.
    """
    configs, seen = [], set()
    for ml in (style_doc.get("layers") or []):
        meta = ml.get("metadata") or {}
        layer_id = meta.get("geodeploy:layer_id")
        # KEYED BY ID **AND** TYPE. Vectors and rasters are numbered in separate sequences, so a
        # portal holding vector 1 and raster 1 — an ordinary thing — had the second one swallowed
        # as a duplicate of the first, and the group opened missing a layer with no error anywhere.
        # Measured on a live portal: 7 layers in, 5 out.
        key = (layer_id, meta.get("geodeploy:type") or "vector")
        if layer_id is None or key in seen:
            continue
        seen.add(key)
        legend = {"color_mode": _mode_of(meta), "field": meta.get("geodeploy:legendField"),
                  "entries": meta.get("geodeploy:legend") or [],
                  "size": _size_of(meta.get("geodeploy:sizeLegend"))}
        configs.append({
            "layer_id": layer_id,
            "layer_type": meta.get("geodeploy:type") or "vector",
            "name": meta.get("geodeploy:name"),
            # POINT / LINE / POLYGON, and it matters more than it looks. A vector-tile renderer
            # takes one symbol PER GEOMETRY TYPE, so getting it wrong does not mean a wrong colour
            # — it means a polygon drawn with a marker symbol, i.e. a dot at every vertex, or
            # nothing at all. The published style records it and the plugin was not reading it.
            "geometry_type": _geometry_of(ml, meta),
            # MapLibre omits `visibility` when a layer is shown, so absent means visible.
            "visible": ((ml.get("layout") or {}).get("visibility") or "visible") != "none",
            "opacity": meta.get("geodeploy:opacity", 1.0),
            "style": _baked_style(ml, meta, legend, style_from_legend),
            "popup_fields": [],
            # WHERE THE DATA COMES FROM, taken from the portal's own style.
            #
            # A public portal may include layers that are not themselves published — that is a
            # normal thing to do, and the portal serves them because the PORTAL is public. Looking
            # each one up in the public layer index therefore found nothing and the group opened
            # empty. The style already names the source the portal itself draws from, and it is
            # readable by anyone who can read the portal.
            "source": _source_of(style_doc, ml),
        })
    configs.reverse()
    return configs


#: What is actually IN the tiles, by the MapLibre layer type that draws them. This is the authority,
#: not `geodeploy:geometry` — which describes the SOURCE. A 3D point layer is drawn as
#: `fill-extrusion` from a `pillars` function source that serves POLYGONS (the points, buffered), so
#: the source says "point" while the tiles hold polygons; trusting the source there draws nothing.
_GEOMETRY_BY_ML_TYPE = {
    "fill": "polygon", "fill-extrusion": "polygon",
    "line": "line",
    "circle": "point", "symbol": "point", "heatmap": "point",
}


def _geometry_of(ml_layer: dict, meta: dict) -> str | None:
    """The geometry these tiles hold, preferring how they are DRAWN over what the source says."""
    return _GEOMETRY_BY_ML_TYPE.get(ml_layer.get("type")) or meta.get("geodeploy:geometry")


def _baked_style(ml_layer: dict, meta: dict, legend: dict, style_from_legend) -> dict:
    """The style a PUBLISHED portal actually draws a layer with.

    THE LEGEND IS ONLY HALF THE STORY, and the half that is usually empty. `geodeploy:legend` lists
    the CLASSES of a graduated or categorized layer — for a single-symbol layer, which most are, it
    is `[]`, so building the style from it alone produced `{}` and the plugin fell back to the
    layer's own default. Measured on a live portal: `example` is drawn `#10b981` at 45% with a
    `#1d4ed8` outline while its default style is plain `#3b82f6`, and `shapefiles_dresden` is drawn
    `#3b82f6` against a default of `#d1ba23`. Two layers, two wrong colours, both public — which is
    why "the portal looks different in QGIS" had nothing to do with permissions.

    Everything needed is already in the document: the MapLibre `paint` block, and the
    `geodeploy:*` metadata that carries what paint cannot express as a single value (the marker
    shape, the dash pattern, and — for points baked as icons — the colour and radius themselves).
    """
    style = dict(style_from_legend(legend) or {})
    paint = ml_layer.get("paint") or {}
    layer_opacity = meta.get("geodeploy:opacity")
    try:
        layer_opacity = float(layer_opacity) if layer_opacity is not None else 1.0
    except (TypeError, ValueError):
        layer_opacity = 1.0

    def plain(value):
        """A literal paint value, or None when it is an EXPRESSION.

        A classified layer bakes `["step", …]` / `["match", …]` into the colour. Those classes are
        exactly what the legend carries, so they are read from there instead of parsed twice —
        and a list must never be handed on as if it were a colour.
        """
        return value if isinstance(value, (str, int, float)) else None

    # Shape and dash are metadata whichever geometry this is.
    for key, prop in (("geodeploy:marker", "marker"), ("geodeploy:lineType", "lineType")):
        if meta.get(key):
            style.setdefault(prop, meta[key])

    kind = ml_layer.get("type")
    if kind == "fill-extrusion":
        # A 3D layer. QGIS draws it flat, which is honest — the colour is the part that carries.
        style.setdefault("color", plain(paint.get("fill-extrusion-color")) or style.get("color"))
        baked = plain(paint.get("fill-extrusion-opacity"))
        if baked is not None and layer_opacity:
            style.setdefault("fill_opacity", min(1.0, float(baked) / layer_opacity))
    elif kind == "fill":
        style.setdefault("color", plain(paint.get("fill-color")) or style.get("color"))
        outline = plain(paint.get("fill-outline-color"))
        if outline:
            style.setdefault("outline_color", outline)
        baked = plain(paint.get("fill-opacity"))
        if baked is not None and layer_opacity:
            # The portal bakes `opacity * fill_opacity` into one number, and the layer opacity is
            # applied separately in QGIS — so divide it back out or it would be applied twice.
            style.setdefault("fill_opacity", min(1.0, float(baked) / layer_opacity))
    elif kind == "line":
        style.setdefault("color", plain(paint.get("line-color")) or style.get("color"))
        width = plain(paint.get("line-width"))
        if width is not None:
            style.setdefault("line_width", width)
    elif kind == "circle":
        style.setdefault("color", plain(paint.get("circle-color")) or style.get("color"))
        radius = plain(paint.get("circle-radius"))
        if radius is not None:
            style.setdefault("radius", radius)
    elif kind == "symbol":
        # Points drawn as ICONS: the paint block holds only `icon-opacity`, because the colour and
        # size went into the generated image. The generator records both as metadata for exactly
        # this reason — they ARE the style's `color` and `radius`.
        if meta.get("geodeploy:markerColor"):
            style.setdefault("color", meta["geodeploy:markerColor"])
        if meta.get("geodeploy:markerSize") is not None:
            style.setdefault("radius", meta["geodeploy:markerSize"])

    return {k: v for k, v in style.items() if v is not None}


def _source_of(style_doc: dict, ml_layer: dict):
    """`{kind, url, source_layer}` for one baked layer, or None.

    Only the shapes the generator actually emits: PMTiles for a tiled GeoParquet layer, an XYZ
    template for PostGIS vector tiles and for rasters.
    """
    src_id = ml_layer.get("source")
    src = ((style_doc.get("sources") or {}).get(src_id) or {}) if src_id else {}
    if not src:
        return None
    url = (src.get("url") or "").strip()
    tiles = src.get("tiles") or []
    if src.get("type") == "vector":
        if url.startswith("pmtiles://"):
            return {"kind": "pmtiles", "url": url[len("pmtiles://"):],
                    "source_layer": ml_layer.get("source-layer")}
        if tiles:
            return {"kind": "vector-tiles", "url": tiles[0],
                    "source_layer": ml_layer.get("source-layer")}
    if src.get("type") == "raster" and tiles:
        return {"kind": "raster-xyz", "url": tiles[0], "source_layer": None}
    return None


def _mode_of(meta: dict) -> str:
    """Which symbology kind the baked legend describes.

    The style.json does not state the mode, but the legend's own shape does: entries carrying a
    `value` are categories, entries carrying `min`/`max` are classes, and no entries at all is a
    single symbol.
    """
    entries = meta.get("geodeploy:legend") or []
    if not entries:
        return "single"
    first = entries[0] if isinstance(entries[0], dict) else {}
    if "min" in first or "max" in first:
        return "graduated"
    if "value" in first:
        return "categorized"
    return "single"


def _size_of(size_legend):
    """The baked size legend in the shape `style_from_legend` reads."""
    if not isinstance(size_legend, dict) or not size_legend.get("field"):
        return None
    return {"field": size_legend.get("field"),
            "stops": [[size_legend.get("min_value"), size_legend.get("min_size")],
                      [size_legend.get("max_value"), size_legend.get("max_size")]]}


def plan_push(group, style_for, current_configs) -> dict:
    """What pushing this group would do, as five named lists plus the configs to send.

    Compared by IDENTITY, never by name or position, so renaming a layer in QGIS or dragging it up
    the list is not mistaken for removing one thing and adding another.

    * `unchanged` — on the portal already, same style;
    * `restyled`  — on the portal, style differs. This is the whole point of the round trip, so it
                    is called out rather than folded into "unchanged";
    * `added`     — in the group, already on the instance, not yet on the portal;
    * `uploads`   — in the group but NOT on the instance: local files, with nothing to reference
                    until they are uploaded. `(name, qgis_layer)` so the caller can send them;
    * `removed`   — on the portal, no longer in the group;
    * `kept`      — on the portal, and its QGIS styling could not be read, so the portal's own is
                    left exactly as it was. Named so the dialog can say that out loud instead of
                    reporting the layer as "unchanged", which would be true only by accident.
    """
    before = {}
    for cfg in (current_configs or []):
        before[(int(cfg.get("layer_id")), str(cfg.get("layer_type")))] = cfg

    configs, uploads, unchanged, restyled, added, kept = [], [], [], [], [], []
    seen = set()

    def walk(node):
        """Depth-first, in panel order, building the folder TREE as it goes.

        A portal's folders are a real structure, not decoration, so a QGIS sub-group has to become
        a GeoDeploy folder rather than being flattened away — the reader loses the grouping the
        author built, and the next pull would come back without it.
        """
        tree = []
        for child in node.children():
            qgis_layer = getattr(child, "layer", lambda: None)()
            if qgis_layer is None:
                # A sub-group: a folder, with whatever is inside it.
                folder = {"id": _folder_id(child), "name": child.name(),
                          "collapsed": not _expanded(child), "exclusive": False,
                          "description": "", "children": walk(child)}
                tree.append(folder)
                continue
            identity = layer_identity(qgis_layer)
            if identity is None:
                uploads.append((qgis_layer.name(), qgis_layer, child))
                continue
            layer_id, layer_type = identity
            seen.add((layer_id, layer_type))
            was = before.get((layer_id, layer_type))
            style = style_for(qgis_layer, layer_type) or {}
            if not style and (was or {}).get("style"):
                # AN UNREADABLE STYLE MUST NOT ERASE THE PORTAL'S.
                #
                # A portal's raster is opened as server-rendered tiles — a picture — because that is
                # the only way it looks like the portal. QGIS calls that "Singleband color data" and
                # offers nothing to change: there are no bands to stretch, only RGBA. So
                # `raster_from_qgis` has nothing to translate and returns {}, and pushing that
                # REPLACED the portal's colormap and stretch with nothing. The raster then rendered
                # unstretched, i.e. usually blank — a restyle attempt that silently destroyed the
                # styling it was meant to change.
                #
                # Keeping what the portal already had is the only safe reading of "I could not tell".
                # It applies to vectors too: a renderer this plugin cannot translate is not an
                # instruction to clear the portal's styling.
                style = was["style"]
                kept.append(qgis_layer.name())
            entry = {"layer_id": layer_id, "layer_type": layer_type,
                     "visible": bool(child.isVisible()), "opacity": _opacity_of(qgis_layer),
                     "style": style, "popup_fields": []}
            configs.append(entry)
            tree.append({"layer_id": layer_id, "layer_type": layer_type})
            if was is None:
                added.append(qgis_layer.name())
            elif _style_differs(was, entry):
                restyled.append(qgis_layer.name())
            else:
                unchanged.append(qgis_layer.name())
        return tree

    tree = walk(group)

    removed = [_config_label(cfg) for key, cfg in before.items() if key not in seen]

    # Renaming the group is the obvious way to rename the portal, so it has to mean that — but it
    # is a visible change to a published page, so it is proposed rather than applied. The stored
    # title is what the portal was called when the group was opened; the group's CURRENT name is
    # what the user has since typed.
    portal_id, stored_title = group_portal(group)
    current_name = group.name() if hasattr(group, "name") else stored_title
    rename = None
    if portal_id is not None and current_name and current_name != stored_title:
        rename = (stored_title, current_name)

    return {"configs": configs, "uploads": uploads, "unchanged": unchanged,
            "restyled": restyled, "added": added, "removed": removed, "rename": rename,
            "tree": tree, "kept": kept}


def _style_differs(before: dict, after: dict) -> bool:
    """Whether anything a viewer would SEE has changed.

    Visibility and opacity count: a layer switched off in QGIS is a real change to the portal, and
    treating it as "unchanged" would publish something the user did not intend. Compared through
    sorted JSON so that key order — which carries no meaning — never reads as a difference.
    """
    import json

    try:
        from .symbology import comparable_style
    except Exception:                   # noqa: BLE001 - outside QGIS, compare the raw dicts
        def comparable_style(style):
            return style or {}

    def shape(cfg):
        # COMPARED THROUGH THE SAME DEFAULTS. A style read back out of QGIS is always complete, while
        # a stored one holds only what somebody chose — so comparing them as written reported every
        # layer in a freshly opened portal as restyled. See `symbology.comparable_style`.
        return json.dumps({"style": comparable_style(cfg.get("style")),
                           "visible": bool(cfg.get("visible", True)),
                           "opacity": round(float(cfg.get("opacity", 1.0) or 1.0), 3)},
                          sort_keys=True, default=str)

    return shape(before) != shape(after)


def _config_label(cfg: dict) -> str:
    name = cfg.get("name") or cfg.get("layer_name")
    if name:
        return str(name)
    return "{0} layer {1}".format(cfg.get("layer_type") or "?", cfg.get("layer_id"))


def _opacity_of(qgis_layer) -> float:
    """Layer opacity as 0–1. QGIS keeps it on the renderer for vectors and the layer for rasters."""
    try:
        value = qgis_layer.opacity()          # QgsRasterLayer, and QgsVectorLayer since 3.18
        if value is not None:
            return round(float(value), 3)
    except Exception:                         # noqa: BLE001 - older API, fall through
        pass
    return 1.0


def push(client, group, configs, publish: bool = True, new_title: str | None = None,
         tree=None) -> dict:
    """Create or update the portal this group represents. Returns the portal document.

    Updating is a WHOLE-document write of `layer_configs`, not a series of add/remove calls: the
    group is the intended state, and applying it in pieces would leave the portal briefly showing
    something nobody asked for if one call failed halfway.
    """
    portal_id, title = group_portal(group)
    if not configs:
        raise PortalError("This group has no GeoDeploy layers in it. Add layers from an instance, "
                          "or upload the ones you have, and try again.")
    try:
        if portal_id is None:
            portal = client.portals.create(title=new_title or title or "Untitled portal")
            portal_id = portal["id"]
        elif new_title:
            client.portals.update(portal_id, title=new_title)
        client.portals.update(portal_id, layer_configs=configs)
        if tree is not None:
            # Sent separately because `layer_groups` is "leave as-is" when omitted, and a portal
            # with no folders must be able to STAY without folders rather than keep an old tree.
            client.portals.set_groups(portal_id, tree)
        if publish:
            client.portals.publish(portal_id)
        doc = client.portals.get(portal_id)
    except GeoDeployError as exc:
        raise PortalError(str(exc)) from exc

    # Re-tag, so the group knows which portal it is now and what it is called. Without this a
    # renamed group would propose the SAME rename on every subsequent push, and a group that just
    # created a portal would create a second one next time.
    try:
        group.setCustomProperty(P_PORTAL_ID, str(doc.get("id", portal_id)))
        group.setCustomProperty(P_PORTAL_TITLE, doc.get("title") or new_title or title or "")
    except Exception:                   # noqa: BLE001 - a tag is not worth failing a publish over
        pass
    return doc


def enrich_from_published(doc: dict, instance, style_from_legend) -> dict:
    """Fill in what the API's `layer_configs` do not carry, from the portal's own published style.

    THE ASYMMETRY THAT CAUSED A BUG. `PortalOut.LayerConfig` is `{layer_id, layer_type, visible,
    opacity, style, popup_fields}` — no `source`, no `geometry_type`, no `name`. The published
    style.json carries all three, because it has to: it IS the drawing instructions.

    Without `source`, the group builder had nothing portal-specific to build a RASTER from and fell
    back to the layer's own listing entry — so a portal drawing a DEM as `colormap_name=terrain`
    opened in the layer's default grey, exactly the complaint that was fixed for the anonymous path
    and not for this one. Without `geometry_type` it depended on the listing row matching, and
    without `name` a layer that failed to open was reported by its numeric id.

    The API's own values always win. This only ADDS keys, so the portal's authoritative styling and
    visibility are untouched — and an unpublished portal simply has nothing to merge, which is
    honest: its rasters are not being served under any styling yet.
    """
    if not doc.get("published") or not doc.get("slug"):
        return doc
    try:
        baked = configs_from_published_style(
            instance.published_style(doc["slug"]), style_from_legend)
    except Exception as exc:            # noqa: BLE001 - the API document is still perfectly usable
        _note("Could not read the published style for extra layer detail: {0}".format(exc))
        return doc

    by_key = {}
    for cfg in baked:
        try:
            by_key[(int(cfg["layer_id"]), str(cfg.get("layer_type") or "vector"))] = cfg
        except (TypeError, ValueError, KeyError):
            continue
    for cfg in (doc.get("layer_configs") or []):
        try:
            match = by_key.get((int(cfg.get("layer_id")), str(cfg.get("layer_type") or "vector")))
        except (TypeError, ValueError):
            continue
        if not match:
            continue
        for key in ("source", "geometry_type", "name"):
            if cfg.get(key) is None and match.get(key) is not None:
                cfg[key] = match[key]
    return doc
