# docs/overrides/

## Purpose
The one MkDocs Material theme override, registered as `theme.custom_dir` in `mkdocs.yml`. It exists
for a single job: **link previews**. A pasted docs URL used to render as a bare card — title and
domain, nothing else — because no page emitted Open Graph tags.

## Contents
- `main.html` — extends Material's `base.html` and fills `{% block extrahead %}` with `og:type`,
  `og:site_name`, `og:title`, `og:description`, `og:url`, the `og:image` block and
  `twitter:card`. Per page: the title falls back to `site_name` on the home page, the description to
  `site_description`, and the URL to `page.canonical_url` (already absolute — which is what `og:`
  requires).

## Dependencies / relationships
- `mkdocs.yml` — `theme.custom_dir: docs/overrides`, and `overrides/` is listed in `exclude_docs` so
  MkDocs does not *also* copy this template into the built site as a page.
- `docs/assets/og-image.png` — the 1200x630 card. Its source of truth is `ui/public/og-image.png`,
  the same image every GeoDeploy instance serves at `/og-image.png`; keep the two identical.
- The instance-side equivalents are `ui/index.html` (dashboard) and
  `api/geodeploy/services/portal_generator.py::_social_meta` (published portals). Those two need
  nginx to rewrite `__GEODEPLOY_ORIGIN__` into an absolute origin; the docs do not, because
  `site_url` is known at build time.

## Current status & known issues
- Material's own `social` plugin would render a *per-page* card image, which is nicer, but it needs
  Cairo + Pango system libraries in the build image and the Pages workflow installs MkDocs with a
  plain `pip install`. If those libraries ever land in the CI image, that plugin supersedes this
  file.
- A card is only re-scraped when the social network's cache expires. LinkedIn's Post Inspector
  (`linkedin.com/post-inspector/`) forces it; other networks vary.

## Last updated
2026-08-18 (created)
