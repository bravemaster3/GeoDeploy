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

# Namespaced so nothing else in a QGIS project collides with them.
P_LAYER_ID = "geodeploy/layer_id"
P_LAYER_TYPE = "geodeploy/layer_type"
P_INSTANCE = "geodeploy/instance"
P_PORTAL_ID = "geodeploy/portal_id"
P_PORTAL_TITLE = "geodeploy/portal_title"


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
    * `removed`   — on the portal, no longer in the group.
    """
    before = {}
    for cfg in (current_configs or []):
        before[(int(cfg.get("layer_id")), str(cfg.get("layer_type")))] = cfg

    configs, uploads, unchanged, restyled, added = [], [], [], [], []
    seen = set()
    for child in group.children():
        qgis_layer = getattr(child, "layer", lambda: None)()
        if qgis_layer is None:
            continue                    # a nested group; reported by configs_from_group
        identity = layer_identity(qgis_layer)
        if identity is None:
            uploads.append((qgis_layer.name(), qgis_layer, child))
            continue
        layer_id, layer_type = identity
        seen.add((layer_id, layer_type))
        style = style_for(qgis_layer, layer_type) or {}
        entry = {"layer_id": layer_id, "layer_type": layer_type,
                 "visible": bool(child.isVisible()), "opacity": _opacity_of(qgis_layer),
                 "style": style, "popup_fields": []}
        configs.append(entry)
        was = before.get((layer_id, layer_type))
        if was is None:
            added.append(qgis_layer.name())
        elif _style_differs(was, entry):
            restyled.append(qgis_layer.name())
        else:
            unchanged.append(qgis_layer.name())

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
            "restyled": restyled, "added": added, "removed": removed, "rename": rename}


def _style_differs(before: dict, after: dict) -> bool:
    """Whether anything a viewer would SEE has changed.

    Visibility and opacity count: a layer switched off in QGIS is a real change to the portal, and
    treating it as "unchanged" would publish something the user did not intend. Compared through
    sorted JSON so that key order — which carries no meaning — never reads as a difference.
    """
    import json

    def shape(cfg):
        return json.dumps({"style": cfg.get("style") or {},
                           "visible": bool(cfg.get("visible", True)),
                           "opacity": round(float(cfg.get("opacity", 1.0) or 1.0), 3)},
                          sort_keys=True)

    return shape(before) != shape(after)


def _config_label(cfg: dict) -> str:
    name = cfg.get("name") or cfg.get("layer_name")
    if name:
        return str(name)
    return "{0} layer {1}".format(cfg.get("layer_type") or "?", cfg.get("layer_id"))


def configs_from_group(group, style_for) -> tuple[list[dict], list[str]]:
    """`(layer_configs, skipped)` from a QGIS group, top of the group first.

    `style_for(qgis_layer, layer_type)` returns the GeoDeploy style dict — passed in so this module
    does not have to know about renderers, and so the caller can decide whether styles travel.

    A layer with no GeoDeploy identity is SKIPPED and named. It is almost always a local file the
    user has not uploaded yet, and quietly dropping it would produce a portal missing a layer with
    no explanation, while quietly uploading it would be a surprise write.
    """
    configs, skipped = [], []
    for child in group.children():
        qgis_layer = getattr(child, "layer", lambda: None)()
        if qgis_layer is None:
            # A nested group. Portals have one flat ordered list, so there is nothing to map a
            # sub-group onto; say so rather than silently flattening it.
            skipped.append("{0} (a nested group)".format(child.name()))
            continue
        identity = layer_identity(qgis_layer)
        if identity is None:
            skipped.append(qgis_layer.name())
            continue
        layer_id, layer_type = identity
        configs.append({
            "layer_id": layer_id,
            "layer_type": layer_type,
            # `isVisible` is the CHECKBOX in the layer tree, which is what the user manipulated.
            "visible": bool(child.isVisible()),
            "opacity": _opacity_of(qgis_layer),
            "style": style_for(qgis_layer, layer_type) or {},
            "popup_fields": [],
        })
    return configs, skipped


def _opacity_of(qgis_layer) -> float:
    """Layer opacity as 0–1. QGIS keeps it on the renderer for vectors and the layer for rasters."""
    try:
        value = qgis_layer.opacity()          # QgsRasterLayer, and QgsVectorLayer since 3.18
        if value is not None:
            return round(float(value), 3)
    except Exception:                         # noqa: BLE001 - older API, fall through
        pass
    return 1.0


def push(client, group, configs, publish: bool = True, new_title: str | None = None) -> dict:
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
