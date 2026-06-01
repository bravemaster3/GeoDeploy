# templates/

## Purpose
Portal templates — the visual skins applied when a portal is published. Each template is a self-contained folder; GeoDeploy substitutes placeholders into its `layout.html` to produce a static portal.

## Contents
- `official/` — maintainer templates: `minimal/`, `research/`, `west-africa-fr/`, `humanitarian/`.
  - Only `minimal/` is currently **complete** (has `layout.html`, `style.json`, `theme.css`, `template.json`). The others have `template.json` only and are **skipped by the API** (`routers/templates.py` requires both `template.json` and `layout.html`).
- `community/` — user-contributed templates + `CONTRIBUTING.md` (the canonical spec for the 5-file template format and CI rules).

### A template folder (5 files)
- `template.json` — metadata (name, author, description, tags, language, basemap, version, license).
- `style.json` — MapLibre base style (basemap only, no data layers).
- `layout.html` — portal HTML scaffold with placeholders: `{{TITLE}}`, `{{STYLE_JSON}}`, `{{THEME_CSS}}`, `{{POPUP_CONFIG}}`, `{{ACCESS_TYPE}}`, `{{PASSWORD_SHA256}}`.
- `theme.css` — typography/color overrides (inlined into `<style>`).
- `preview.png` — exactly 800×500.

## Dependencies / relationships
- Bind-mounted read-only at `/templates` in the api + celery containers.
- Read by `api/.../routers/templates.py` (listing) and `api/.../services/portal_generator.py` (publish-time substitution).
- `layout.html`'s embedded JS builds the layer switcher from `geodeploy:*` metadata that `portal_generator.generate_style()` injects — the two are tightly coupled.
- `minimal/layout.html` **absolutifies tile URLs** (`location.origin + url`) so MapLibre's worker can fetch them; any new template's layout must do the same.
- `minimal/layout.html` also implements (driven by `geodeploy:*` layer metadata): per-layer **zoom-to-layer** (`geodeploy:bbox`), **geometry icons** (`geodeploy:geometry` → point/line/polygon/raster), **viewer-side styling** (vectors: color / size / opacity via `map.setPaintProperty`; rasters: palette / hillshade / stretch via `source.setTiles` rebuilding the TiTiler URL — uses `geodeploy:bands`) — all session-only, with a **Reset styling** link, a feature **popup + docked "full table" attribute panel** (`#attr-panel`, built from `queryRenderedFeatures`), a **basemap switcher** control (top-right, `BasemapControl` — OSM/Dark/Satellite + the template default), and a **lng/lat readout** (`#coords`, bottom-right). New templates that want these features need the matching markup/CSS/JS.

## Current status & known issues
- Adding a template field/placeholder means editing both the template `layout.html` and `portal_generator.build_portal_bundle`.
- The non-minimal official templates are intentionally incomplete (metadata-only) and won't appear in the gallery until a `layout.html` is added.
- CI (`.github/workflows/validate-template.yml`) validates community submissions; see `community/CONTRIBUTING.md`.

## Last updated
2026-06-01
