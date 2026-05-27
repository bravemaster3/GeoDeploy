import json
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
                meta = json.loads(meta_file.read_text())
                templates.append(TemplateOut(
                    id=f"{category}/{entry.name}",
                    name=meta.get("name", entry.name),
                    author=meta.get("author", ""),
                    description=meta.get("description", ""),
                    tags=meta.get("tags", []),
                    language=meta.get("language", "en"),
                    basemap=meta.get("basemap", "osm-bright"),
                    preview_url=f"/templates-static/{category}/{entry.name}/preview.png",
                    version=meta.get("version", "1.0.0"),
                    license=meta.get("license", "MIT"),
                    is_official=is_official,
                ))
            except Exception:
                continue
    return templates


@router.get("", response_model=list[TemplateOut])
async def list_templates():
    return _load_templates()
