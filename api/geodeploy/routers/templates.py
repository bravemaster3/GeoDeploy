import json
import re
import os
from pathlib import Path
from fastapi import APIRouter
from ..schemas import TemplateOut

router = APIRouter(prefix="/templates", tags=["templates"])

TEMPLATES_DIR = Path("/templates")


def _load_templates() -> list[TemplateOut]:
    templates = []
    for category, is_official in [("official", True), ("community", False)]:
        base = TEMPLATES_DIR / category
        if not base.exists():
            continue
        for entry in sorted(base.iterdir()):
            meta_file = entry / "template.json"
            if not meta_file.exists():
                continue
            try:
                # A complete template needs a basemap (style.json). layout.html is optional —
                # templates fall back to the shared skeleton (templates/shared/layout.html).
                if not (entry / "style.json").exists():
                    continue
                meta = json.loads(meta_file.read_text())
                accent = bg = None
                try:
                    css = (entry / "theme.css").read_text(encoding="utf-8")
                    m = re.search(r"--accent:\s*([^;]+);", css)
                    accent = m.group(1).strip() if m else None
                    m = re.search(r"--bg:\s*([^;]+);", css)
                    bg = m.group(1).strip() if m else None
                except OSError:
                    pass   # a template without theme.css simply has no palette to advertise
                templates.append(TemplateOut(
                    id=entry.name,
                    name=meta.get("name", entry.name),
                    author=meta.get("author", ""),
                    description=meta.get("description", ""),
                    tags=meta.get("tags", []),
                    language=meta.get("language", "en"),
                    basemap=meta.get("basemap", "osm-bright"),
                    # Only advertise a preview that EXISTS. This was unconditional, so every card
                    # rendered a broken <img> and the "no preview" fallback was unreachable — no
                    # template in the repo ships a preview.png. Accepts the common web formats so a
                    # community template can drop in a .webp/.jpg without a code change.
                    preview_url=next(
                        (f"/templates-static/{category}/{entry.name}/preview{ext}"
                         for ext in (".png", ".webp", ".jpg", ".jpeg")
                         if (entry / f"preview{ext}").exists()), None),
                    accent=accent,
                    bg=bg,
                    version=meta.get("version", "1.0.0"),
                    license=meta.get("license", "MIT"),
                    is_official=is_official,
                    archetype=meta.get("archetype"),   # V-11 preset experience (None → webmap)
                    # Default to web-map-only rather than "everything": a template that never
                    # declared support has only ever been used as a web map, and silently offering
                    # it for a story map would apply a treatment nobody designed for that layout.
                    archetypes=(meta.get("archetypes")
                                or ([meta["archetype"]] if meta.get("archetype") else ["webmap"])),
                    layout=meta.get("layout"),          # V-11 optional region/panel overrides
                    # V-16 dashboard preset: the starting widget set + wiring (unbound data
                    # sources — the builder binds them to this portal's layers).
                    dashboard=meta.get("dashboard"),
                ))
            except Exception:
                continue
    return templates


@router.get("", response_model=list[TemplateOut])
async def list_templates():
    return _load_templates()
