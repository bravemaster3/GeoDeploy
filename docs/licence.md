# Licence

GeoDeploy is **open source under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)**.
The full text is in [`LICENSE`](https://github.com/bravemaster3/GeoDeploy/blob/main/LICENSE), and
attribution details are in [`NOTICE`](https://github.com/bravemaster3/GeoDeploy/blob/main/NOTICE).

## What you may do

Everything you would want to do with software you run yourself:

- **Run it**, for anything — personal, academic, commercial, government — with no seat count, no
  licence key and no usage reporting.
- **Modify it.** Change anything, and you are under no obligation to publish your changes.
- **Redistribute it**, modified or not, including inside a commercial product.
- **Host it for other people**, including as a paid service.

There is no separate "enterprise edition". Every feature is in
[this repository](https://github.com/bravemaster3/GeoDeploy), under this licence.

## What you must do when you redistribute

Only if you *distribute* GeoDeploy or a derivative — running it for yourself, or for your own users,
requires nothing:

- Include a copy of the licence.
- Keep the existing copyright, patent, trademark and attribution notices, and carry the `NOTICE`
  file forward.
- **State that you changed the files you changed.** A line in a changelog is enough; nobody expects
  a diff.

## Patents and trademarks

Two things Apache 2.0 adds over a shorter licence such as MIT, and the reason this project uses it:

**A patent grant (§3).** Every contributor grants you a licence to any patents they hold that their
contribution necessarily infringes. So the code cannot later be used as the basis of a patent claim
against you by the people who wrote it. That grant terminates for anyone who initiates patent
litigation over the software — a defensive clause, not an offensive one.

**No trademark licence (§6).** The copyright licence does not give you rights to the GeoDeploy name
or logo. You may fork, modify and sell the software; you may not present your fork *as* GeoDeploy.
This is the ordinary arrangement and does not restrict any technical use.

## Contributions

Contributions are accepted under the same licence, certified by a
[Developer Certificate of Origin](https://developercertificate.org/) sign-off — one `-s` on your
commit. There is no CLA: nothing asks you to assign rights to the project owner, and no relicensing
is planned that would need your permission. See
[`CONTRIBUTING.md`](https://github.com/bravemaster3/GeoDeploy/blob/main/CONTRIBUTING.md).

## Your data is not covered by this

The licence governs the **software**. It says nothing about what you put into it.

Your layers, portals, metadata and uploaded files are yours. GeoDeploy stores them in your PostGIS
and your object storage, in open formats, and no telemetry leaves your server. If you stop using
GeoDeploy, [everything is already readable](data-access.md) by QGIS, DuckDB, Python and R without it.

Publishing a layer publicly, or attaching a licence to it in its metadata, is your decision and your
licence to make — that field describes *your* data to the people using it, and is unrelated to this
page.

## Third-party components

GeoDeploy runs alongside services that are **separate programs with their own licences** — PostGIS,
MinIO, Martin, TiTiler, Redis and nginx among them — and bundles third-party browser libraries such
as MapLibre GL JS and deck.gl. Running them alongside GeoDeploy does not change their terms or bring
them under this one.

If you redistribute a GeoDeploy deployment, check those components' licences as well as this one.
MinIO in particular is AGPL-licensed, which has obligations of its own if you distribute or offer it
as a service; a deployment using external S3-compatible storage instead does not include it at all.

!!! note "Not legal advice"
    This page summarises the licence in plain language for orientation. Where it and the
    [licence text](https://github.com/bravemaster3/GeoDeploy/blob/main/LICENSE) differ, the licence
    text governs.
