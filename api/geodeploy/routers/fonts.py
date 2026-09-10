"""Glyphs for map labels, and the list of faces this instance can actually draw.

## Why this is a route

MapLibre draws text from GLYPH PBFs, and a fontstack its glyph source does not have renders as
**nothing at all**: no error, no warning, no text. So "which fonts exist" is not a cosmetic question
— it decides whether a labelled map has labels on it.

Two things follow, and both are why this is a route rather than a constant:

* **The list is DISCOVERED, not declared.** `templates/shared/fonts/` is a drop-in directory: a face
  copied in is servable immediately, with no rebuild, no config and no code change. Hard-coding the
  list would mean an operator could install a font the plugin refuses to offer.
* **Both style builders name one URL.** `services/portal_generator` and `ui/src/lib/mapStyle.js`
  both point `glyphs` at `/api/fonts/{fontstack}/{range}.pbf`, so neither has to know what is
  installed — only the server does, and only the server should.

## What ships, and how to ship more

Noto Sans in Regular, Bold and Italic, over the nine codepoint ranges labels actually use: ASCII and
Latin-1, Latin Extended-A and -B, combining diacriticals and Greek, Cyrillic, Latin Extended
Additional (Vietnamese), and punctuation. About 780 KB a face.

`scripts/build_glyphs.js` generates more from any TTF — Noto Serif and Noto Sans Mono are the
obvious next two, and after that it is whatever an operator's data needs. `docs/portals.md` has the
command.

## QGIS is not affected by any of this

Worth stating because it is the natural worry: QGIS draws labels with real system fonts through Qt,
not with these glyphs. A label pushed from QGIS keeps its original family in `labels.qgis_font` and
gets it back unchanged; the fontstack here only decides what the **web portal** draws.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/fonts", tags=["fonts"])

#: Where the glyph set lives. The same `templates/` mount the portal runtime is read from, so a face
#: dropped in here needs no nginx change, no rebuild and no restart.
FONT_ROOT = Path("/templates/shared/fonts")

#: What a portal falls back to when a style names a face this instance does not have. Something must
#: be drawn — a missing stack is invisible text — and this is the one face that is always shipped.
FALLBACK = "Noto Sans Regular"

#: A glyph range is always `<start>-<end>`, both within 0–65535. Checked rather than trusted, because
#: this value becomes part of a filesystem path.
_RANGE = re.compile(r"^\d{1,5}-\d{1,5}$")

#: A fontstack is one or more face names joined by commas. Anything else is not a fontstack, and
#: letting it through would turn a public route into a file reader.
_STACK = re.compile(r"^[A-Za-z0-9 _.,-]{1,120}$")


def installed() -> list[str]:
    """Every face this instance can draw, sorted. Empty when no glyph set is installed."""
    try:
        return sorted(d.name for d in FONT_ROOT.iterdir() if d.is_dir() and any(d.iterdir()))
    except Exception:                   # noqa: BLE001 - a missing directory is "none installed"
        return []


def resolve(fontstack: str) -> str | None:
    """The first face in a stack this instance has, or the fallback, or None if nothing is installed.

    MapLibre's fontstack is a comma-separated PREFERENCE LIST — the first face that exists wins —
    so honouring the order is what makes `"Noto Serif Bold,Noto Sans Regular"` mean what it says.
    """
    have = set(installed())
    if not have:
        return None
    for name in (part.strip() for part in fontstack.split(",")):
        if name in have:
            return name
    return FALLBACK if FALLBACK in have else sorted(have)[0]


@router.get("")
async def list_fonts():
    """The faces this instance can draw.

    Read by the QGIS plugin and the portal editor so both offer exactly what will actually render,
    rather than a list compiled into them that an install can contradict in either direction.
    """
    faces = installed()
    return {"fonts": faces, "fallback": FALLBACK if FALLBACK in faces else (faces[0] if faces else None)}


@router.get("/{fontstack}/{glyph_range}.pbf")
async def glyphs(fontstack: str, glyph_range: str):
    """One glyph range for one fontstack."""
    if not _STACK.match(fontstack) or not _RANGE.match(glyph_range):
        raise HTTPException(400, "Not a font stack and glyph range.")

    face = resolve(fontstack)
    if face is None:
        raise HTTPException(
            503, "No fonts are installed on this instance, so labels cannot be drawn. See "
                 "templates/shared/fonts/ and scripts/build_glyphs.js.")

    # RESOLVED AND CHECKED, not merely joined: the face name is derived from the URL, and a name
    # that escaped the font directory would turn a public route into a file reader. The regex above
    # already forbids `/` and `..`; this is the second lock on the same door.
    try:
        target = (FONT_ROOT / face / f"{glyph_range}.pbf").resolve()
        root = FONT_ROOT.resolve()
        inside = root in target.parents
    except Exception:                   # noqa: BLE001
        inside, target = False, None

    if not inside or target is None or not target.is_file():
        # A RANGE WITH NO GLYPHS IS NOT AN ERROR. A Latin face has nothing in the CJK blocks, and
        # MapLibre asks for whatever range a character falls in; 404 is how the spec says "no glyphs
        # here", and the renderer simply draws nothing for those codepoints and carries on.
        raise HTTPException(404, "No glyphs in that range for that face.")

    return FileResponse(
        target,
        media_type="application/x-protobuf",
        # Glyphs never change for a given face and range, so they are worth caching hard: a
        # label-heavy map asks for the same handful of ranges on every pan.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
