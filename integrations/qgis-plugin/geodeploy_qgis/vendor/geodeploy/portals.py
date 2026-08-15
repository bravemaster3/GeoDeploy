"""Portals — create, arrange layers, style them, publish.

The editing model is the one the dashboard uses and the one the plugins are written against:

    GET /api/portals/{id}  →  mutate `layer_configs`  →  PUT /api/portals/{id}  →  POST …/publish

Two conventions are load-bearing and easy to get wrong:

* **`layer_configs[0]` is the TOP of the layer list and draws on top.** Adding a layer therefore
  prepends by default, matching what "add a layer" does in the editor.
* **A draft edit changes nothing that is live.** Publishing re-bakes the static bundle; until then
  the published portal serves its previous state. Every mutating method here says so in the message
  the CLI prints, because "I changed it and nothing happened" is otherwise the first support
  question.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .config import split_portal_url
from .errors import NotFoundError, ValidationError

ACCESS_TYPES = ("public", "password", "organization", "owner")
ARCHETYPES = ("webmap", "storymap", "catalog")


class Portals(object):
    def __init__(self, client: Any):
        self._c = client

    # ── read ────────────────────────────────────────────────────────────────────────────────────

    def list(self, published: Optional[bool] = None,
             query: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._c.get("/portals") or []
        if published is not None:
            rows = [r for r in rows if bool(r.get("published")) is published]
        if query:
            needle = query.lower()
            rows = [r for r in rows if needle in (r.get("title") or "").lower()
                    or needle in (r.get("slug") or "").lower()]
        return rows

    def get(self, ref: Any) -> Dict[str, Any]:
        """A portal by id, slug, title, or its published URL — the CLI should not force people to
        look up ids, nor to edit a URL they just copied out of the address bar."""
        text = str(ref).strip()
        if "/portals/" in text:
            origin, text = split_portal_url(text)
            if origin and origin != self._c.url:
                raise ValidationError(
                    400, "That portal is on {0}, but this command is talking to {1}.".format(
                        origin, self._c.url))
        if text.isdigit():
            return self._c.get("/portals/{0}".format(int(text)))
        rows = self.list()
        for row in rows:
            if (row.get("slug") or "") == text:
                return self._c.get("/portals/{0}".format(row["id"]))
        matches = [r for r in rows if (r.get("title") or "").lower() == text.lower()]
        if len(matches) == 1:
            return self._c.get("/portals/{0}".format(matches[0]["id"]))
        if len(matches) > 1:
            raise ValidationError(400, "Several portals are called {0!r}; use the id.".format(ref))
        partial = [r for r in rows if text.lower() in (r.get("title") or "").lower()]
        if len(partial) == 1:
            return self._c.get("/portals/{0}".format(partial[0]["id"]))
        raise NotFoundError(404, "No portal matching {0!r}.".format(ref))

    def url(self, portal: Dict[str, Any]) -> str:
        """The public URL of a portal's published bundle (valid once it has been published)."""
        return "{0}/portals/{1}/".format(self._c.url, portal.get("slug"))

    # ── write ───────────────────────────────────────────────────────────────────────────────────

    def create(self, title: str, description: Optional[str] = None, template_id: str = "minimal",
               access_type: str = "public", access_password: Optional[str] = None,
               archetype: Optional[str] = None, layer_configs: Optional[List[Dict]] = None,
               theme: Optional[Dict] = None, layout_config: Optional[Dict] = None,
               story: Optional[Dict] = None) -> Dict[str, Any]:
        _check_access(access_type, access_password)
        body = {"title": title, "template_id": template_id, "access_type": access_type,
                "layer_configs": layer_configs or []}
        if description is not None:
            body["description"] = description
        if access_password:
            body["access_password"] = access_password
        if theme:
            body["theme"] = theme
        if story:
            body["story"] = story
        if layout_config or archetype:
            merged = dict(layout_config or {})
            if archetype:
                if archetype not in ARCHETYPES:
                    raise ValidationError(400, "Experience must be one of {0}.".format(
                        ", ".join(ARCHETYPES)))
                merged["archetype"] = archetype
            body["layout_config"] = merged
        return self._c.post("/portals", body)

    def update(self, portal_id: Any, **fields: Any) -> Dict[str, Any]:
        """PUT the fields given. Anything omitted is left exactly as it was."""
        body = {k: v for k, v in fields.items() if v is not None}
        if not body:
            raise ValidationError(400, "Nothing to change.")
        if "access_type" in body:
            _check_access(body["access_type"], body.get("access_password"))
        return self._c.put("/portals/{0}".format(int(portal_id)), body)

    def set_config(self, portal_id: Any, config: Dict[str, Any]) -> Dict[str, Any]:
        """Push a whole portal document back (the `portal-get` → edit → `portal-set` round trip).

        Read-only and server-owned fields are dropped rather than sent and rejected: `slug` is
        derived from the title, and `id`/`published`/timestamps are not settable.
        """
        return self._c.put("/portals/{0}".format(int(portal_id)), editable_config(config))

    def delete(self, portal_id: Any) -> Any:
        return self._c.delete("/portals/{0}".format(int(portal_id)))

    def publish(self, portal_id: Any) -> Dict[str, Any]:
        return self._c.post("/portals/{0}/publish".format(int(portal_id)))

    def unpublish(self, portal_id: Any) -> Dict[str, Any]:
        return self._c.post("/portals/{0}/unpublish".format(int(portal_id)))

    def upload_asset(self, portal_id: Any, path: str) -> Dict[str, Any]:
        """A logo or an About-page image. SVG is accepted and served with a strict CSP."""
        from .transport import MultipartBody
        body = MultipartBody(file_path=path, filename=os.path.basename(path))
        return self._c.request("POST", "/portals/{0}/assets".format(int(portal_id)),
                               body=body, content_type=body.content_type,
                               timeout=self._c.upload_timeout)

    # ── layers on a portal ──────────────────────────────────────────────────────────────────────

    def layers(self, portal: Any) -> List[Dict[str, Any]]:
        """The portal's layer configs, top of the list first."""
        doc = portal if isinstance(portal, dict) else self.get(portal)
        return list(doc.get("layer_configs") or [])

    def add_layer(self, portal_id: Any, layer_id: int, layer_type: str, style: Optional[Dict] = None,
                  visible: bool = True, opacity: float = 1.0, popup_fields: Optional[List[str]] = None,
                  bottom: bool = False, replace: bool = False) -> Dict[str, Any]:
        """Add a layer to a portal. Prepends, because index 0 is the top of the list.

        `replace=True` updates the entry when the layer is already there instead of refusing —
        which is what a script re-running its own setup wants.
        """
        doc = self._c.get("/portals/{0}".format(int(portal_id)))
        configs = list(doc.get("layer_configs") or [])
        entry = {"layer_id": int(layer_id), "layer_type": layer_type, "visible": visible,
                 "opacity": opacity, "style": dict(style or {}),
                 "popup_fields": list(popup_fields or [])}
        existing = _find_config(configs, layer_id, layer_type)
        if existing is not None:
            if not replace:
                raise ValidationError(400, "Layer {0} ({1}) is already on this portal.".format(
                    layer_id, layer_type))
            configs[existing] = entry
        elif bottom:
            configs.append(entry)
        else:
            configs.insert(0, entry)
        return self._c.put("/portals/{0}".format(int(portal_id)), {"layer_configs": configs})

    def remove_layer(self, portal_id: Any, layer_id: int,
                     layer_type: Optional[str] = None) -> Dict[str, Any]:
        doc = self._c.get("/portals/{0}".format(int(portal_id)))
        configs = list(doc.get("layer_configs") or [])
        kept = [c for c in configs
                if not (int(c.get("layer_id", -1)) == int(layer_id)
                        and (layer_type is None or c.get("layer_type") == layer_type))]
        if len(kept) == len(configs):
            raise NotFoundError(404, "Layer {0} is not on portal {1}.".format(layer_id, portal_id))
        return self._c.put("/portals/{0}".format(int(portal_id)), {"layer_configs": kept})

    def set_layer_style(self, portal_id: Any, layer_id: int, style: Dict[str, Any],
                        layer_type: Optional[str] = None, merge: bool = True,
                        visible: Optional[bool] = None, opacity: Optional[float] = None,
                        popup_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Restyle one layer on one portal, keeping everything else about the entry."""
        doc = self._c.get("/portals/{0}".format(int(portal_id)))
        configs = list(doc.get("layer_configs") or [])
        index = _find_config(configs, layer_id, layer_type)
        if index is None:
            raise NotFoundError(404, "Layer {0} is not on portal {1}.".format(layer_id, portal_id))
        entry = dict(configs[index])
        entry["style"] = dict(entry.get("style") or {}, **style) if merge else dict(style)
        if visible is not None:
            entry["visible"] = visible
        if opacity is not None:
            entry["opacity"] = opacity
        if popup_fields is not None:
            entry["popup_fields"] = list(popup_fields)
        configs[index] = entry
        return self._c.put("/portals/{0}".format(int(portal_id)), {"layer_configs": configs})

    def move_layer(self, portal_id: Any, layer_id: int, position: Any,
                   layer_type: Optional[str] = None) -> Dict[str, Any]:
        """Reorder: `position` is `top`, `bottom`, `up`, `down`, or a 0-based index (0 = top)."""
        doc = self._c.get("/portals/{0}".format(int(portal_id)))
        configs = list(doc.get("layer_configs") or [])
        index = _find_config(configs, layer_id, layer_type)
        if index is None:
            raise NotFoundError(404, "Layer {0} is not on portal {1}.".format(layer_id, portal_id))
        entry = configs.pop(index)
        if position == "top":
            target = 0
        elif position == "bottom":
            target = len(configs)
        elif position == "up":
            target = max(0, index - 1)
        elif position == "down":
            target = min(len(configs), index + 1)
        else:
            try:
                target = max(0, min(int(position), len(configs)))
            except (TypeError, ValueError):
                raise ValidationError(400, "Position must be top, bottom, up, down or an index.")
        configs.insert(target, entry)
        return self._c.put("/portals/{0}".format(int(portal_id)), {"layer_configs": configs})

    # ── folders (V-13 layer groups) ─────────────────────────────────────────────────────────────

    def groups(self, portal: Any) -> Optional[List[Dict[str, Any]]]:
        doc = portal if isinstance(portal, dict) else self.get(portal)
        return doc.get("layer_groups")

    def set_groups(self, portal_id: Any, groups: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Replace the folder tree. `None` is not "clear" for the API (it means leave alone), so
        clearing is an explicit empty list."""
        return self._c.put("/portals/{0}".format(int(portal_id)),
                           {"layer_groups": [] if groups is None else groups})

    # ── export (draw-a-box download, the public route) ──────────────────────────────────────────

    def export_bundle(self, slug: str, bbox: str, items: List[Dict[str, Any]],
                      target_crs: str = "4326") -> Dict[str, Any]:
        """Queue a clipped export of portal layers. Public endpoint — no auth needed."""
        return self._c.post("/portals/{0}/export-bundle".format(slug),
                            {"bbox": bbox, "items": items, "target_crs": target_crs}, auth=False)

    def export_status(self, slug: str, job_id: str) -> Dict[str, Any]:
        return self._c.get("/portals/{0}/export-status/{1}".format(slug, job_id), auth=False)

    def export_download(self, slug: str, job_id: str, sink) -> Any:
        return self._c.download("/portals/{0}/export-download/{1}".format(slug, job_id), sink,
                                auth=False)


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

#: Fields `PUT /portals/{id}` accepts. Anything else in a saved document is server-owned; sending
#: it back is at best ignored and at worst a 422, so the round trip filters rather than hopes.
EDITABLE_FIELDS = ("title", "description", "template_id", "layer_configs", "layer_groups",
                   "layout_config", "story", "theme", "initial_view", "access_type",
                   "access_password", "basemap")


def editable_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (config or {}).items() if k in EDITABLE_FIELDS}


def _find_config(configs: List[Dict[str, Any]], layer_id: Any,
                 layer_type: Optional[str]) -> Optional[int]:
    for index, entry in enumerate(configs):
        if int(entry.get("layer_id", -1)) == int(layer_id) and (
                layer_type is None or entry.get("layer_type") == layer_type):
            return index
    return None


def _check_access(access_type: str, password: Optional[str]) -> None:
    if access_type not in ACCESS_TYPES:
        raise ValidationError(400, "Access must be one of {0}.".format(", ".join(ACCESS_TYPES)))
    if access_type == "password" and not password:
        raise ValidationError(400, "A password-protected portal needs --password.")
