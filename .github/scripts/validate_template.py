#!/usr/bin/env python3
"""Validate community template PRs."""
import json
import os
import sys
from pathlib import Path

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

REQUIRED_FILES = {"template.json", "style.json", "layout.html", "theme.css", "preview.png"}

TEMPLATE_SCHEMA = {
    "type": "object",
    "required": ["name", "author", "description"],
    "properties": {
        "name": {"type": "string"},
        "author": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string"},
        "basemap": {"type": "string"},
        "version": {"type": "string"},
        "license": {"type": "string"},
    },
}

errors = []
community_dir = Path("templates/community")

for template_dir in community_dir.iterdir():
    if not template_dir.is_dir() or template_dir.name == "CONTRIBUTING.md":
        continue

    name = template_dir.name
    existing = {f.name for f in template_dir.iterdir()}
    missing = REQUIRED_FILES - existing
    if missing:
        errors.append(f"{name}: missing files: {', '.join(missing)}")
        continue

    # Validate template.json
    try:
        meta = json.loads((template_dir / "template.json").read_text())
        for field in TEMPLATE_SCHEMA["required"]:
            if field not in meta:
                errors.append(f"{name}: template.json missing required field '{field}'")
    except json.JSONDecodeError as e:
        errors.append(f"{name}: template.json is invalid JSON: {e}")

    # Validate style.json is valid JSON with version 8
    try:
        style = json.loads((template_dir / "style.json").read_text())
        if style.get("version") != 8:
            errors.append(f"{name}: style.json must have 'version': 8 (MapLibre GL)")
    except json.JSONDecodeError as e:
        errors.append(f"{name}: style.json is invalid JSON: {e}")

    # Check preview.png dimensions
    if HAS_PILLOW:
        try:
            img = Image.open(template_dir / "preview.png")
            if img.size != (800, 500):
                errors.append(f"{name}: preview.png must be exactly 800×500px, got {img.size[0]}×{img.size[1]}")
        except Exception as e:
            errors.append(f"{name}: cannot read preview.png: {e}")

    # Check layout.html has required placeholders
    layout = (template_dir / "layout.html").read_text()
    for placeholder in ["{{TITLE}}", "{{STYLE_JSON}}", "{{THEME_CSS}}", "{{POPUP_CONFIG}}"]:
        if placeholder not in layout:
            errors.append(f"{name}: layout.html missing placeholder {placeholder}")

    # Check no external CDN dependencies in layout.html
    cdn_patterns = ["cdn.jsdelivr.net", "cdnjs.cloudflare.com", "maxcdn.bootstrapcdn.com"]
    for cdn in cdn_patterns:
        if cdn in layout:
            errors.append(f"{name}: layout.html must not reference external CDN ({cdn}). Bundle dependencies locally.")

if errors:
    print("Template validation FAILED:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("All templates validated successfully.")
