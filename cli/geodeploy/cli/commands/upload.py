"""`geodeploy upload` — one command for every kind of file, and any number of them.

The route (through the API, direct-to-storage, chunked, CSV, raster) is worked out per file by
`geodeploy.uploads.plan`; `--dry-run` prints those decisions without moving a byte, which is the
honest way to find out that a 300 MB GeoPackage is going to become a GeoParquet layer rather than
a PostGIS table.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from ...errors import GeoDeployError
from ...uploads import LARGE_UPLOAD_THRESHOLD
from ..main import add_command
from ..output import EXIT_GENERIC, EXIT_OK, human_size
from ._common import confirm  # noqa: F401  (kept for symmetry with other command modules)


def register(subparsers) -> None:
    parser = add_command(
        subparsers, "upload", cmd_upload,
        "upload one or more files and register them as layers",
        epilog="""\
examples:
  geodeploy upload roads.gpkg                       one file
  geodeploy upload *.gpkg *.tif --wait              a whole directory's worth, waiting for ingest
  geodeploy upload sites.csv --x lon --y lat        CSV points (columns guessed if omitted)
  geodeploy upload plots.csv --wkt geometry         CSV with WKT geometry of any type
  geodeploy upload big.parquet --name "Parcels"     GeoParquet, direct to storage, chunked
  geodeploy upload data/*.tif --dry-run             what would happen, without doing it

Files at or over {threshold} bypass the API and upload straight to object storage in parts —
that is what makes a multi-gigabyte upload survive a proxy in front of the instance.
""".format(threshold=human_size(LARGE_UPLOAD_THRESHOLD)))

    parser.add_argument("files", nargs="+", help="files to upload")
    parser.add_argument("--type", dest="layer_type", choices=["vector", "raster"],
                        help="force the layer type (default: from the extension)")
    parser.add_argument("--name", help="layer name (only sensible with a single file)")
    parser.add_argument("--wait", action="store_true",
                        help="wait for ingest to finish, and fail if it does not")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the route each file would take, and stop")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="files uploaded at once (default 1; each large file already uses "
                             "four parallel parts)")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="stop at the first failure instead of continuing")
    parser.add_argument("--public", action="store_true",
                        help="share each layer publicly once it is ready (STAC + OGC + raw asset)")

    csv_group = parser.add_argument_group("CSV geometry")
    csv_group.add_argument("--x", dest="x_column", help="longitude/easting column")
    csv_group.add_argument("--y", dest="y_column", help="latitude/northing column")
    csv_group.add_argument("--wkt", dest="wkt_column", help="WKT geometry column (any geometry type)")
    csv_group.add_argument("--srid", type=int, default=4326, help="CRS of the coordinates (default 4326)")
    csv_group.add_argument("--delimiter", choices=["comma", "semicolon", "tab", "pipe", "space"],
                           help="field delimiter (default: sniffed from the file)")
    csv_group.add_argument("--no-guess", action="store_true",
                           help="do not guess geometry columns — fail instead")


def cmd_upload(ctx, args) -> int:
    client = ctx.client()
    out = ctx.out

    if args.name and len(args.files) > 1:
        out.error("--name applies to a single file; upload them one at a time to name each.")
        return EXIT_GENERIC

    plans = []
    for path in args.files:
        plans.append(client.uploads.plan(
            path, layer_type=args.layer_type, name=args.name, x_column=args.x_column,
            y_column=args.y_column, wkt_column=args.wkt_column, srid=args.srid,
            delimiter=args.delimiter, guess_csv=not args.no_guess))

    for plan in plans:
        if plan.csv_opts and plan.csv_opts.get("guessed"):
            geometry = (("WKT column {0}".format(plan.csv_opts["wkt_column"]))
                        if plan.csv_opts.get("wkt_column")
                        else "x={0}, y={1}".format(plan.csv_opts.get("x_column"),
                                                   plan.csv_opts.get("y_column")))
            out.warn("{0}: guessed geometry from the header ({1}). Pass --x/--y or --wkt to be "
                     "explicit.".format(os.path.basename(plan.path), geometry))

    if args.dry_run:
        ctx.out.render([dict(p.as_dict(), size_h=human_size(p.size)) for p in plans],
                       ["path", "layer_type", "route", "name", "size_h", "chunked", "reason"])
        return EXIT_OK

    results = []  # type: List[Any]
    failures = 0
    for plan in plans:
        progress = out.progress(os.path.basename(plan.path), plan.size)
        out.info("{0} → {1} ({2}, {3})".format(os.path.basename(plan.path), plan.name,
                                               plan.route, human_size(plan.size)))
        try:
            result = client.uploads.upload(
                plan.path, plan=plan, wait=False,
                on_progress=lambda done, total: progress.update(done, total))
            progress.finish()
            out.info("  queued as layer {0} (job {1})".format(result.layer_id, result.job_id))
            results.append(result)
        except GeoDeployError as exc:
            progress.finish()
            failures += 1
            out.error("{0}: {1}".format(os.path.basename(plan.path), exc))
            if args.stop_on_error:
                break

    if args.wait:
        for result in results:
            failures += _wait_for(ctx, result)

    if args.public:
        for result in results:
            if result.final is None or (result.final or {}).get("status") in ("ready", "completed"):
                try:
                    client.layers.api(result.plan.layer_type).share(result.layer_id,
                                                                    visibility="public")
                    out.info("  {0} is now public.".format(result.plan.name))
                except GeoDeployError as exc:
                    out.warn("Could not share {0}: {1}".format(result.plan.name, exc))

    payload = [r.as_dict() for r in results]
    if out.json_mode:
        out.json({"ok": failures == 0, "uploaded": payload, "failed": failures})
    else:
        out.table(payload, ["file", "name", "layer_type", "layer_id", "job_id"]) if payload else None
        if not failures:
            out.success("{0} file(s) uploaded.{1}".format(
                len(results), "" if args.wait else " Ingest continues in the background — "
                                                   "`geodeploy layers list` shows progress."))
    return EXIT_GENERIC if failures else EXIT_OK


def _wait_for(ctx, result) -> int:
    """Follow one ingest job to the end. Returns 1 if it failed, 0 otherwise."""
    out = ctx.out
    label = os.path.basename(result.plan.path)

    def on_progress(status: Dict[str, Any]) -> None:
        out.info("  {0}: {1:3d}%  {2}".format(label, status.get("progress") or 0,
                                              status.get("current_step") or status.get("status")))
    try:
        result.final = ctx.client().jobs.wait(result.job_id, result.plan.layer_type,
                                              on_progress=on_progress)
        out.success("{0} ready (layer {1}).".format(label, result.layer_id))
        return 0
    except GeoDeployError as exc:
        out.error("{0}: {1}".format(label, exc))
        return 1
