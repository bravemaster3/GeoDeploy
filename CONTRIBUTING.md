# Contributing to GeoDeploy

Thanks for being here. GeoDeploy is open source, and it stays that way — see
[What stays open](#what-stays-open) if you want the commitment in writing before you invest time.

## Ways to help

You do not need to write code to be useful:

- **Report what broke.** Concretely: what you did, what happened, what you expected. A description of
  the situation beats a guess at the cause — several fixes have come from someone saying "this looked
  wrong" about behaviour that turned out to be a real bug elsewhere.
- **Improve the docs.** Every page has an *Edit* link. If something is inaccurate or assumes
  knowledge you did not have, that is a bug in the docs.
- **Contribute a template.** The easiest first contribution — see
  [templates/community/CONTRIBUTING.md](templates/community/CONTRIBUTING.md). A template is a theme
  plus a declaration of which portal experiences it suits; no application code involved.
- **Translate.** The dashboard and published portals are translatable.
- **Fix or build something.** Read on.

## Before a large change

Open an issue first and say what you plan to do. Not for permission — to avoid you building something
that collides with work in flight, or that has been tried and rejected for a reason worth knowing.
Small fixes need no ceremony: send the pull request.

## Sign your commits (DCO)

GeoDeploy uses the [Developer Certificate of Origin](https://developercertificate.org/). It is a short
statement that you wrote the contribution, or otherwise have the right to submit it under the
project's licence. Add it by committing with `-s`:

```bash
git commit -s -m "fix: raster clip no longer drops the last row"
```

which appends:

```
Signed-off-by: Your Name <you@example.com>
```

**Why a DCO and not a CLA.** A CLA asks you to assign rights to the project owner, which is what a
company needs when it might one day relicense or close parts of the code. GeoDeploy is not going to do
either, so asking for it would be taking something the project does not need. The DCO is one line and
you keep your copyright.

## Development

```bash
git clone https://github.com/bravemaster3/GeoDeploy
cd GeoDeploy
docker compose up -d --build
```

The stack builds and runs locally with the same Compose file used in production.

| | |
| --- | --- |
| Backend tests | `cd api && python -m pytest` — needs a throwaway PostGIS; **never point it at an instance you care about** |
| Frontend | `cd ui && npm install && npm run build` |
| Docs site | `pip install -r requirements-docs.txt && mkdocs serve` |

A green frontend build proves the code compiles, not that it runs — check the page too.

### How this repository is organised

Every folder has a `README.md` describing what is in it and why. Read the one for the area you are
changing before you start; it will usually save you more time than it costs. `CLAUDE.md` at the root
describes the conventions those notes follow.

Two things that catch people out, both documented in
[notes_temp/notes_for_future.md](notes_temp/notes_for_future.md):

- **Three surfaces must agree** on portal layout: `portal_generator.py` (server),
  `templates/shared/portal.js` (published runtime) and `PortalEditor.vue` (editor preview). A change
  to one usually needs all three.
- **The Celery worker shares the API image.** Changing anything the worker imports means rebuilding
  and recreating `celery` too, or it silently runs old code.

## Pull requests

- **One change per pull request.** Two unrelated fixes in one branch are hard to review and harder to
  revert.
- **Say why, not just what.** The diff shows what changed; the description should say what problem it
  solves and anything you considered and rejected.
- **Include a test when fixing a bug** — one that fails before your fix. It is the difference between
  "fixed" and "fixed until someone refactors".
- **CI must pass.** It runs the backend tests and builds the frontend.

Do not worry about matching the commit-message style in the log — that is a house habit, not a
requirement. Clear English is enough.

## What stays open

Every feature of GeoDeploy is in this repository under the MIT licence, and that is the arrangement
going forward. There is no paid tier, no feature held back, and nothing that unlocks with a key.

If a hosted **GeoDeploy Cloud** appears, it is hosting only — running this same open-source code for
people who would rather not run a server. It will not have features you cannot get by installing it
yourself. If that ever changes, it changes in the open, with notice, and not quietly.

Concretely, this means your contribution is not being routed into something closed. It is also why
the DCO is enough: there is no future relicensing that would need your permission.

## Reporting a security issue

Please do **not** open a public issue for a vulnerability. Email the maintainer at the address on the
[GitHub profile](https://github.com/bravemaster3) with what you found and how to reproduce it, and
give a reasonable window to ship a fix before disclosing.

## Licence

By contributing you agree that your work is licensed under the [MIT licence](LICENSE), the same terms
as the rest of the project. You keep the copyright to what you write.
