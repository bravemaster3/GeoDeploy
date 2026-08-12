"""`geodeploy jobs …` — ingest jobs, and waiting for one."""
from __future__ import annotations

from ..main import add_command, group_parser
from ..output import EXIT_OK


def register(subparsers) -> None:
    group = group_parser(subparsers, "jobs", "ingest job status")

    show = add_command(group, "show", cmd_show, "the current state of a job")
    show.add_argument("job_id")
    show.add_argument("--type", dest="layer_type", choices=["vector", "raster"], default="vector")

    watch = add_command(group, "watch", cmd_watch, "follow a job until it finishes")
    watch.add_argument("job_id")
    watch.add_argument("--type", dest="layer_type", choices=["vector", "raster"], default="vector")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("--max-wait", dest="max_wait", type=float, default=3600.0,
                       help="give up after this many seconds (the job keeps running server-side)")


def cmd_show(ctx, args) -> int:
    ctx.out.render(ctx.client().jobs.get(args.job_id, args.layer_type),
                   ["id", "layer_id", "layer_type", "status", "progress", "current_step",
                    "error_message"])
    return EXIT_OK


def cmd_watch(ctx, args) -> int:
    final = ctx.client().jobs.wait(
        args.job_id, args.layer_type, interval=args.interval, timeout=args.max_wait,
        on_progress=lambda st: ctx.out.info("  {0:3d}%  {1}".format(
            st.get("progress") or 0, st.get("current_step") or st.get("status") or "")))
    ctx.out.render(final, ["id", "layer_id", "layer_type", "status", "progress", "current_step"])
    if not ctx.out.json_mode:
        ctx.out.success("Job {0} finished.".format(args.job_id))
    return EXIT_OK
