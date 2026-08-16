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


def push(client, group, configs, publish: bool = True) -> dict:
    """Create or update the portal this group represents. Returns the portal document.

    Updating is a WHOLE-document write of `layer_configs`, not a series of add/remove calls: the
    group is the intended state, and applying it in pieces would leave the portal briefly showing
    something nobody asked for if a call failed halfway.
    """
    portal_id, title = group_portal(group)
    if not configs:
        raise PortalError("This group has no GeoDeploy layers in it. Add layers from an instance, "
                          "or upload the ones you have, and try again.")
    try:
        if portal_id is None:
            portal = client.portals.create(title=title or "Untitled portal")
            portal_id = portal["id"]
        client.portals.update(portal_id, layer_configs=configs)
        if publish:
            client.portals.publish(portal_id)
        return client.portals.get(portal_id)
    except GeoDeployError as exc:
        raise PortalError(str(exc)) from exc
