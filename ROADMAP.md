# Roadmap

The roadmap lives in the documentation, as **releases**:

**<https://docs-geodeploy.kndev.org/roadmap/>** — source: [`docs/roadmap.md`](docs/roadmap.md)

It is one file, edited like any other page.

---

## Why this file is only a pointer

There used to be three roadmaps: this one, `docs/roadmap.md`, and an interactive board at
`docs/roadmap.html` whose embedded JSON was the declared source of truth. They drifted, as three
copies of anything do — this file still announced a "frontier" that had shipped weeks earlier, and
the board rendered in its own styling so it read as a different document from the rest of the docs.

The board is gone and this file is a pointer. One roadmap, in the docs, in the docs' own design.

## Editing it

Edit `docs/roadmap.md`. Releases are `<div class="gd-rel">` blocks rendered as stops on a timeline;
add `now` to the class for the one being worked on, and use `- [x]` for what is done. The styling
lives in `docs/stylesheets/extra.css` under "Roadmap".
