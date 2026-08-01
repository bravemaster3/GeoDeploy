"""The beat schedule and the worker's import list must agree.

This exists because they did not. `demo-reset-tick` was added to `beat_schedule` without adding
`geodeploy.tasks.demo_reset` to `include`, so beat sent the task every 60 seconds and the worker —
which had never imported the module — discarded each one with a `KeyError` traceback. On EVERY
install, demo or not, forever. Nothing failed loudly enough to notice: the backup queue kept working,
and the only symptom was a log line a minute.

The mistake is a one-line omission that no other test could catch, so it gets its own file.
"""
import importlib

from geodeploy.celery_app import BEAT_SCHEDULE, DEMO_BEAT, celery_app

# Everything that MAY be scheduled — the demo entry is only added to the live schedule on a demo
# instance, but it has to be importable and correctly named on every install, since flipping the flag
# must not require a code change.
ALL_SCHEDULED = {**BEAT_SCHEDULE, **DEMO_BEAT}


def test_every_scheduled_task_is_in_the_worker_import_list():
    """THE regression. `include` is what the worker imports at startup; a task outside it is
    unregistered no matter how correctly it is scheduled."""
    included = set(celery_app.conf.include)
    for name, entry in ALL_SCHEDULED.items():
        module = entry["task"].rsplit(".", 1)[0]
        assert module in included, (
            f"beat schedule '{name}' runs {entry['task']}, but {module} is not in "
            f"celery_app include= — the worker will discard every message it sends.")


def test_every_scheduled_task_actually_exists():
    """Catches the other half: a schedule entry naming a task that was renamed or never written.
    Importing the module registers the task under its declared `name=`, which is the string beat
    puts on the wire — so comparing against the registry checks the STRING, not just the function."""
    for name, entry in ALL_SCHEDULED.items():
        module = entry["task"].rsplit(".", 1)[0]
        importlib.import_module(module)
        assert entry["task"] in celery_app.tasks, (
            f"beat schedule '{name}' names {entry['task']}, which no imported module registers.")


def test_demo_tick_is_not_scheduled_on_a_normal_install():
    """Demo mode has to be invisible to an install that is not a demo. The task guards itself, but a
    per-minute message that can only ever no-op is still a per-minute message."""
    from geodeploy.config import get_settings
    if get_settings().geodeploy_demo_mode:
        import pytest
        pytest.skip("this instance IS a demo")
    assert "demo-reset-tick" not in BEAT_SCHEDULE


def test_scheduled_tasks_are_routed_to_a_queue_the_worker_consumes():
    """docker-compose runs the worker with `-Q ingest,backup`. A schedule entry sent to any other
    queue would sit unconsumed forever, which looks exactly like the task never firing."""
    consumed = {"ingest", "backup"}
    for name, entry in ALL_SCHEDULED.items():
        queue = entry.get("options", {}).get("queue")
        assert queue in consumed, (
            f"beat schedule '{name}' targets queue {queue!r}, which the worker does not consume.")
