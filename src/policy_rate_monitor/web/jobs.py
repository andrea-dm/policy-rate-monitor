"""Background jobs: ``fetch``, ``transform`` and ``report``, one at a time.

The jobs write the same files, so a single worker thread runs them, and a new job is
refused while one is queued or running. Jobs live in memory: they are lost when the
service stops.

See also: [HTTP API](api.md#jobs) and [decision
0014](adr/0014-http-api.md).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict

from policy_rate_monitor.web.errors import ApiError, ErrorBody, error_body

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Job", "JobRunner", "JobStatus"]


class JobStatus(StrEnum):
    """Where a job is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


class Job(BaseModel):
    """One run of ``fetch``, ``transform`` or ``report``.

    Attributes:
        id: The job's id, for ``GET /jobs/{id}``.
        kind: ``fetch``, ``transform`` or ``report``.
        status: Queued, running, succeeded or failed.
        created_at: When the job was accepted.
        started_at: When it started; ``None`` while queued.
        finished_at: When it ended; ``None`` until then.
        result: What it produced, once it succeeded.
        error: Why it failed, once it failed.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    status: JobStatus
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None


def _now() -> datetime:
    return datetime.now(UTC)


class JobRunner:
    """Runs the jobs on one worker thread and keeps their state."""

    def __init__(self) -> None:
        """Starts with no jobs; the worker thread starts with the first one."""
        super().__init__()
        self._jobs: dict[str, Job] = {}
        self._changed = threading.Condition()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")

    def submit(self, kind: str, work: Callable[[], dict[str, Any]]) -> Job:
        """Queues a job.

        Args:
            kind: ``fetch``, ``transform`` or ``report``.
            work: Does the job and returns its result.

        Returns:
            The job, as queued.

        Raises:
            ApiError: Another job is queued or running (409, ``job_running``), or
                the runner is shut down (503, ``shutting_down``).
        """
        with self._changed:
            active = [job for job in self._jobs.values() if job.status in _ACTIVE]
            if active:
                running = active[0]
                msg = (
                    f"job {running.id} ({running.kind}) is {running.status}; "
                    "wait for it to finish"
                )
                raise ApiError(HTTPStatus.CONFLICT, "job_running", msg)
            job = Job(
                id=uuid4().hex, kind=kind, status=JobStatus.QUEUED, created_at=_now()
            )
            self._jobs[job.id] = job
            try:
                self._executor.submit(self._run, job.id, work)
            except RuntimeError:  # the executor refuses work once it is shut down
                del self._jobs[job.id]
                msg = "the service is shutting down; no new job is accepted"
                raise ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE, "shutting_down", msg
                ) from None
        return job

    def get(self, job_id: str) -> Job | None:
        """Returns a job as it is now.

        Args:
            job_id: The job's id.

        Returns:
            The job, or ``None`` if there is no job with that id.
        """
        with self._changed:
            return self._jobs.get(job_id)

    def wait(self, job_id: str, timeout: float) -> Job:
        """Waits until a job has finished.

        Args:
            job_id: The job's id.
            timeout: The longest wait, in seconds.

        Returns:
            The finished job.

        Raises:
            KeyError: There is no job with that id.
            TimeoutError: The job is still queued or running after *timeout*.
        """
        with self._changed:
            finished = self._changed.wait_for(
                lambda: self._jobs[job_id].status not in _ACTIVE, timeout
            )
            if not finished:
                msg = f"job {job_id} still {self._jobs[job_id].status}"
                raise TimeoutError(msg)
            return self._jobs[job_id]

    def shutdown(self) -> None:
        """Waits for the running job, then stops the worker thread."""
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, job_id: str, work: Callable[[], dict[str, Any]]) -> None:
        self._update(job_id, status=JobStatus.RUNNING, started_at=_now())
        try:
            result = work()
        except Exception as error:  # a failed job is a result, not a crash # noqa: BLE001
            self._update(
                job_id,
                status=JobStatus.FAILED,
                finished_at=_now(),
                error=error_body(error),
            )
        else:
            self._update(
                job_id, status=JobStatus.SUCCEEDED, finished_at=_now(), result=result
            )

    def _update(self, job_id: str, **changes: object) -> None:
        with self._changed:
            job = self._jobs[job_id]
            self._jobs[job_id] = Job.model_validate({**job.model_dump(), **changes})
            self._changed.notify_all()
