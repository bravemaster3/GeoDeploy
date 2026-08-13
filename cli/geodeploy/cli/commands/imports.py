"""`geodeploy import …` — register data that is already in the database or the bucket.

Nothing is copied and nothing is moved: a PostGIS table is introspected and registered where it
stands, and an object in storage is attached by key. This is how you point a fresh instance at
data that is already on the server, without a round trip through your laptop.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..main import add_command, group_parser
from ..output import EXIT_GENERIC, EXIT_OK


def register(subparsers) -> None:
    group = group_parser(subparsers, "import", "register existing PostGIS tables or storage objects",
                         aliases=["discover"])

    db_list = add_command(group, "db-list", cmd_db_list,
                          "spatial tables in the database, and whether they are registered")
    db_list.add_argument("--all", action="store_true", help="include already-imported tables")

    db_add = add_command(group, "db-add", cmd_db_add, "register PostGIS tables as layers",
                         epilog="examples:\n  geodeploy import db-add public.roads public.sites\n")
    db_add.add_argument("tables", nargs="+", help="schema.table (as listed by db-list)")
    db_add.add_argument("--name", help="display name (single table only)")

    st_list = add_command(group, "storage-list", cmd_storage_list,
                          "GeoTIFF / GeoParquet / CSV objects in the bucket")
    st_list.add_argument("--kind", choices=["raster", "geoparquet", "csv"])

    st_add = add_command(group, "storage-add", cmd_storage_add,
                         "attach storage objects as layers (GeoParquet is inspected + prepared)")
    st_add.add_argument("keys", nargs="+", help="object keys as listed by storage-list")
    st_add.add_argument("--name", help="display name (single object only)")
    st_add.add_argument("--wait", action="store_true", help="wait for any queued jobs")

    csv_cols = add_command(group, "csv-columns", cmd_csv_columns, "header of a CSV in the bucket")
    csv_cols.add_argument("key")
    csv_cols.add_argument("--delimiter", default="comma",
                          choices=["comma", "semicolon", "tab", "pipe", "space"])

    csv_add = add_command(group, "csv", cmd_csv, "build a PostGIS layer from a CSV in the bucket")
    csv_add.add_argument("key")
    csv_add.add_argument("--name")
    csv_add.add_argument("--x", dest="x_column")
    csv_add.add_argument("--y", dest="y_column")
    csv_add.add_argument("--wkt", dest="wkt_column")
    csv_add.add_argument("--srid", type=int, default=4326)
    csv_add.add_argument("--delimiter", default="comma",
                         choices=["comma", "semicolon", "tab", "pipe", "space"])
    csv_add.add_argument("--wait", action="store_true")


def cmd_db_list(ctx, args) -> int:
    rows = ctx.client().imports.database_tables()
    if not args.all:
        rows = [r for r in rows if not r.get("imported")]
    ctx.out.render(rows, ["schema_name", "table_name", "geometry_column", "geometry_type", "srid",
                          "imported"],
                   empty="No spatial tables found (or all of them are already registered).")
    return EXIT_OK


def cmd_db_add(ctx, args) -> int:
    available = {"{0}.{1}".format(r.get("schema_name"), r.get("table_name")): r
                 for r in ctx.client().imports.database_tables()}
    tables = []  # type: List[Dict[str, Any]]
    for ref in args.tables:
        found = available.get(ref)
        if not found:
            ctx.out.error("No spatial table {0!r}. `geodeploy import db-list` shows what there is."
                          .format(ref))
            return EXIT_GENERIC
        entry = {"schema_name": found["schema_name"], "table_name": found["table_name"],
                 "geometry_column": found.get("geometry_column"),
                 "srid": found.get("srid") or 0,
                 "geometry_type": found.get("geometry_type")}
        if args.name and len(args.tables) == 1:
            entry["name"] = args.name
        tables.append(entry)
    result = ctx.client().imports.database(tables)
    ctx.out.render(result)
    if not ctx.out.json_mode:
        ctx.out.success("Registered {0} table(s).".format(len(tables)))
    return EXIT_OK


def cmd_storage_list(ctx, args) -> int:
    ctx.out.render(ctx.client().imports.storage_objects(args.kind),
                   ["key", "kind", "size", "imported"], empty="Nothing importable in the bucket.")
    return EXIT_OK


def cmd_storage_add(ctx, args) -> int:
    items = [{"key": key} for key in args.keys]
    if args.name and len(items) == 1:
        items[0]["name"] = args.name
    result = ctx.client().imports.storage(items)
    jobs = (result or {}).get("jobs") or []
    ctx.out.render(result)
    if args.wait and jobs:
        for job in jobs:
            ctx.client().jobs.wait(job.get("id"), "vector",
                                   on_progress=lambda st: ctx.out.info("  {0:3d}%  {1}".format(
                                       st.get("progress") or 0, st.get("current_step") or "")))
        ctx.out.success("All queued imports finished.")
    return EXIT_OK


def cmd_csv_columns(ctx, args) -> int:
    ctx.out.render(ctx.client().imports.csv_columns(args.key, args.delimiter))
    return EXIT_OK


def cmd_csv(ctx, args) -> int:
    job = ctx.client().imports.csv(args.key, name=args.name, x_column=args.x_column,
                                   y_column=args.y_column, wkt_column=args.wkt_column,
                                   srid=args.srid, delimiter=args.delimiter)
    if args.wait and job.get("id"):
        job = ctx.client().jobs.wait(job["id"], "vector",
                                     on_progress=lambda st: ctx.out.info("  {0:3d}%  {1}".format(
                                         st.get("progress") or 0, st.get("current_step") or "")))
    ctx.out.render(job)
    return EXIT_OK
