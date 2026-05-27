# Contributing a template

A GeoDeploy template is a folder with exactly five files:

```
my-template/
├── template.json   # Metadata
├── style.json      # MapLibre GL base style (basemap only — no data layers)
├── layout.html     # Portal HTML scaffold with {{TITLE}}, {{STYLE_JSON}}, {{THEME_CSS}}, {{POPUP_CONFIG}} placeholders
├── theme.css       # Typography and UI colour overrides
└── preview.png     # Exactly 800×500 pixels
```

## template.json schema

```json
{
  "name": "string (required)",
  "author": "string (required)",
  "description": "string (required)",
  "tags": ["array of strings"],
  "language": "en | fr | pt | ...",
  "basemap": "osm-bright | osm-humanitarian | satellite | ...",
  "preview": "preview.png",
  "version": "semver",
  "license": "MIT | CC-BY-4.0 | ..."
}
```

## layout.html placeholders

GeoDeploy replaces these at portal-publish time:

- `{{TITLE}}` — portal title string
- `{{STYLE_JSON}}` — MapLibre GL style object (JSON)
- `{{THEME_CSS}}` — contents of theme.css inlined into a `<style>` tag
- `{{POPUP_CONFIG}}` — JSON object mapping layer_id → [field names]

## CI validation (automatic on PR)

Every PR to `templates/community/` triggers a GitHub Action that checks:

1. All five required files are present
2. `template.json` validates against the JSON schema
3. `style.json` validates against the MapLibre GL style specification
4. `preview.png` is exactly 800×500 pixels
5. `layout.html` contains no external CDN URLs (must be self-contained)

## Submitting

1. Fork the repo
2. Create `templates/community/your-template-name/` with all five files
3. Open a pull request — the CI will validate automatically
4. A maintainer reviews the visual design and merges

Premium template candidates are identified by the maintainer and offered a revenue-share agreement.
