# templates/shared/fonts/

Glyph sets for map labels, served by `api/geodeploy/routers/fonts.py` at
`/api/fonts/{fontstack}/{range}.pbf` and listed at `GET /api/fonts`.

## Why these files exist at all

A web map draws text from **signed-distance-field glyphs**, not from fonts on the reader's machine.
A fontstack the server does not have renders as **nothing at all** — no error, no warning, no text —
so an instance with no glyph set is an instance whose labelled maps have no labels on them.

## What is here

**Noto Sans** in Regular, Bold and Italic, trimmed to the nine codepoint ranges labels actually use:

| range | what it covers | | range | what it covers |
|---|---|---|---|---|
| `0-255` | ASCII, Latin-1 | | `1280-1535` | Cyrillic supplement, Armenian, Hebrew |
| `256-511` | Latin Extended-A | | `7680-7935` | Latin Extended Additional (Vietnamese) |
| `512-767` | Latin Extended-B | | `8192-8447` | punctuation, currency |
| `768-1023` | combining diacriticals, Greek | | `8448-8703` | letterlike and number forms |
| `1024-1279` | Cyrillic | | | |

About 780 KB a face. The same face over full Unicode is ~1.2 MB, and the extra is CJK, symbols and
maths — blocks a label almost never reaches. Licensed under the SIL Open Font License (see `NOTICE`).

## Adding a face

This is a **drop-in directory**. Nothing is compiled in: the route lists whatever it finds, the
plugin asks for that list when it connects, and portals pick a face up with no rebuild and no
restart.

```bash
docker run --rm -v "$PWD":/w -w /w node:20-bookworm bash -lc \
  'npm i --no-audit --no-fund fontnik && node scripts/build_glyphs.js NotoSerif-Regular.ttf "templates/shared/fonts/Noto Serif Regular"'
```

Name the directory the way a style will ask for it — `Noto Serif Bold`, not `NotoSerif-Bold`.
**Noto Serif** and **Noto Sans Mono** are the two most worth adding: QGIS users reach for a serif or
a monospace often enough that mapping them onto a sans is a visible substitution.

## What happens to a font that is not here

Nothing breaks. The QGIS plugin maps the family to the nearest face that *is* installed — a serif
stays a serif, a monospace stays a monospace, bold and italic are preserved — and names the
substitution in the Log Messages panel. The published style asks for the original face **and** the
fallback, so installing the real one later fixes portals that are already published, without
republishing them.

QGIS itself is unaffected: it draws with the fonts on the author's own machine, and
`labels.qgis_font` carries the original family so a round trip hands the typeface back unchanged.

## Last updated
2026-09-03 (created with the label feature)
