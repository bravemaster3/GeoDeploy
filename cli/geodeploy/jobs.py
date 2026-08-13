"""Ingest jobs — status, and waiting for one to finish.

Every heavy operation in GeoDeploy is queued to Celery and answers 202 with a job id: uploads,
conversions, PMTiles tiling, CSV imports, re-processing. So "did my upload work?" is always this
module, and a CLI that did not wait would report success for work that has not started.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from .errors import GeoDeployError

#: Terminal job states. `ready` and `completed` are both used by the API depending on the pipeline,
#: and a client that knows only one of them polls forever on the other.
DONE = ("ready", "completed", "done")
FAILED = ("failed", "error")


class JobFailed(GeoDeployError):
    """A job reached a terminal FAILED state. Carries the last status payload for the message."""

    def __init__(self, job: Dict[str, Any]):
        self.job = job or {}
        message = self.job.get("error_message") or self.job.get("current_step") or "Job failed"
        super().__init__(message)


class JobTimeout(GeoDeployError):
    """`wait` gave up. The job is still running server-side — this is a client-side deadline."""

    def __init__(self, job: Dict[str, Any], seconds: float):
        self.job = job or {}
        super().__init__("Still {0} after {1:.0f}s (job {2}). It keeps running on the server; "
                         "check with `geodeploy jobs show`."
                         .format(self.job.get("status") or "running", seconds,
                                 self.job.get("id")))


class Jobs(object):
    def __init__(self, client: Any):
        self._c = client

    def get(self, job_id: str, layer_type: str = "vector") -> Dict[str, Any]:
        return self._c.get("/data/{0}/jobs/{1}".format(
            "raster" if layer_type == "raster" else "vector", job_id))

    def wait(self, job_id: str, layer_type: str = "vector", interval: float = 2.0,
             timeout: Optional[float] = 3600.0,
             on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Poll until the job finishes. Returns the final status; raises `JobFailed` if it failed.

        `on_progress` is called only when the printable state CHANGES (percentage or step), not on
        every poll — the difference between a readable log and 600 identical lines for a long
        conversion.
        """
        started = time.time()
        last_seen = None
        status = {}  # type: Dict[str, Any]
        while True:
            status = self.get(job_id, layer_type) or {}
            marker = (status.get("progress"), status.get("current_step"), status.get("status"))
            if on_progress and marker != last_seen:
                on_progress(status)
                last_seen = marker
            state = (status.get("status") or "").lower()
            if state in FAILED:
                raise JobFailed(status)
            if state in DONE:
                return status
            if timeout is not None and (time.time() - started) > timeout:
                raise JobTimeout(status, time.time() - started)
            time.sleep(interval)
