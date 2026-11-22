"""The pipeline jobs of the HTTP API: one at a time, polled until they finish."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, override

import pytest
from fastapi.testclient import TestClient

from policy_rate_monitor.core.errors import SourceUnavailableError
from policy_rate_monitor.core.models import SourceRelease
from policy_rate_monitor.providers import InMemoryProvider
from policy_rate_monitor.web import create_app
from policy_rate_monitor.web.errors import ApiError
from policy_rate_monitor.web.jobs import KEEP_FINISHED, JobRunner, JobStatus
from tests.web.conftest import (
    ROWS,
    SERIES,
    Blocking,
    Growing,
    SlowTransform,
    StagedLater,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from fastapi import FastAPI

    from policy_rate_monitor.config import DatasetConfig

pytestmark = pytest.mark.contract

WAIT = 10.0


def _finished(app: FastAPI, job_id: str) -> dict[str, Any]:
    runner: JobRunner = app.state.jobs
    return runner.wait(job_id, WAIT).model_dump(mode="json")


class Offline(InMemoryProvider):
    @override
    def fetch(self) -> SourceRelease:
        msg = "cannot reach https://data.bis.org: no cached copy either"
        raise SourceUnavailableError(msg)


def test_a_fetch_job_goes_from_queued_to_succeeded(
    config: DatasetConfig, tmp_path: Path
) -> None:
    provider = InMemoryProvider(config.structure, ROWS, SERIES)
    app = create_app(config, provider, tmp_path)
    client = TestClient(app)

    response = client.post("/jobs/fetch")

    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "fetch"
    assert job["status"] == JobStatus.QUEUED
    assert job["started_at"] is None
    assert response.headers["Location"] == f"/jobs/{job['id']}"
    done = _finished(app, job["id"])
    assert done["status"] == JobStatus.SUCCEEDED
    assert done["created_at"] <= done["started_at"] <= done["finished_at"]
    assert done["result"] == {"release": provider.fetch().model_dump(mode="json")}
    assert client.get(f"/jobs/{job['id']}").json() == done


def test_a_failed_job_carries_the_error_kind_and_message(
    config: DatasetConfig, tmp_path: Path
) -> None:
    app = create_app(config, Offline(config.structure, ROWS, SERIES), tmp_path)
    client = TestClient(app)

    job = client.post("/jobs/fetch").json()

    done = _finished(app, job["id"])
    assert done["status"] == JobStatus.FAILED
    assert done["result"] is None
    assert done["error"] == {
        "error": "source_unavailable",
        "message": "cannot reach https://data.bis.org: no cached copy either",
    }


def test_a_second_job_while_one_runs_is_refused(
    config: DatasetConfig, tmp_path: Path
) -> None:
    provider = Blocking(config)
    app = create_app(config, provider, tmp_path)
    client = TestClient(app)
    first = client.post("/jobs/fetch").json()
    assert provider.started.wait(WAIT)
    running = client.get(f"/jobs/{first['id']}").json()
    assert running["status"] == JobStatus.RUNNING
    assert running["started_at"] is not None
    assert running["finished_at"] is None

    response = client.post("/jobs/transform")

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "job_running"
    assert first["id"] in body["message"]
    provider.release.set()
    assert _finished(app, first["id"])["status"] == JobStatus.SUCCEEDED
    assert client.post("/jobs/transform").status_code == 202


def test_after_a_transform_job_the_new_data_is_served_without_a_restart(
    config: DatasetConfig, tmp_path: Path
) -> None:
    app = create_app(config, StagedLater(config), tmp_path)
    client = TestClient(app)
    assert client.get("/ref-areas").status_code == 409

    job = client.post("/jobs/transform").json()

    done = _finished(app, job["id"])
    assert done["status"] == JobStatus.SUCCEEDED
    assert done["result"] == {
        "observations": len(ROWS),
        "series": len(SERIES),
        "findings": 0,
    }
    assert client.get("/ref-areas").status_code == 200


def test_a_transform_job_replaces_data_that_was_already_served(
    config: DatasetConfig, tmp_path: Path
) -> None:
    app = create_app(config, Growing(config), tmp_path)
    client = TestClient(app)
    before = client.get("/ref-areas").json()

    job = client.post("/jobs/transform").json()

    assert _finished(app, job["id"])["status"] == JobStatus.SUCCEEDED
    after = client.get("/ref-areas").json()
    assert len(after) == len(before) + 1


def test_during_the_first_transform_a_read_is_told_at_once_to_wait_for_it(
    config: DatasetConfig, tmp_path: Path
) -> None:
    provider = SlowTransform(config)
    app = create_app(config, provider, tmp_path)
    client = TestClient(app)
    job = client.post("/jobs/transform").json()
    assert provider.started.wait(WAIT)

    response = client.get("/ref-areas")

    provider.release.set()
    assert response.status_code == 409
    assert response.json()["error"] == "missing_input"
    assert "transform is running" in response.json()["message"]
    assert _finished(app, job["id"])["status"] == JobStatus.SUCCEEDED
    assert client.get("/ref-areas").status_code == 200


def test_a_report_job_with_nothing_staged_fails_with_missing_input(
    config: DatasetConfig, tmp_path: Path
) -> None:
    app = create_app(config, StagedLater(config), tmp_path)
    client = TestClient(app)

    job = client.post("/jobs/report", json={"countries": ["US"]}).json()

    done = _finished(app, job["id"])
    assert done["status"] == JobStatus.FAILED
    assert done["error"] == {
        "error": "missing_input",
        "message": "data/staged/observations.parquet does not exist: "
        "run POST /jobs/transform first",
    }


def test_a_report_job_writes_the_report_files(
    config: DatasetConfig, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    provider = InMemoryProvider(config.structure, ROWS, SERIES)
    app = create_app(config, provider, out_dir)
    client = TestClient(app)

    response = client.post(
        "/jobs/report", json={"countries": ["US", "EA"], "start": "2025-01-01"}
    )

    assert response.status_code == 202
    done = _finished(app, response.json()["id"])
    assert done["status"] == JobStatus.SUCCEEDED
    names = [
        "summary.csv",
        "summary.json",
        "policy_rates.png",
        "report.md",
        "report.html",
    ]
    assert done["result"] == {"files": [str(out_dir / name) for name in names]}
    snapshots = json.loads((out_dir / "summary.json").read_text())["snapshots"]
    assert [item["requested_ref_area"] for item in snapshots] == ["US", "EA"]


@pytest.mark.parametrize(
    "body", [{"countries": []}, {"countries": [" ", ""]}, {"start": "2025-01-01"}]
)
def test_a_report_job_needs_a_code(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/jobs/report", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_a_report_job_refuses_a_code_outside_the_code_pattern(
    client: TestClient,
) -> None:
    bad = '=HYPERLINK("x")'

    response = client.post("/jobs/report", json={"countries": ["US", bad]})

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_request",
        "message": f"invalid code {bad!r}: use letters, digits and _ @ $ -",
    }


def test_an_unknown_job_is_not_found(client: TestClient) -> None:
    response = client.get("/jobs/no-such-job")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "message": "no job no-such-job"}


@pytest.mark.unit
def test_a_bug_in_a_job_is_reported_without_its_detail() -> None:
    runner = JobRunner()

    def broken() -> dict[str, object]:
        msg = "secret detail of a bug"
        raise RuntimeError(msg)

    job = runner.submit("fetch", broken)

    done = runner.wait(job.id, WAIT)
    runner.shutdown()
    assert done.status is JobStatus.FAILED
    assert done.error is not None
    assert done.error.model_dump() == {
        "error": "internal_error",
        "message": "RuntimeError",
    }


@pytest.mark.unit
def test_a_shut_down_runner_refuses_a_job_without_blocking_the_next_one() -> None:
    runner = JobRunner()
    runner.shutdown()

    for _ in range(2):
        with pytest.raises(ApiError) as refused:
            runner.submit("fetch", dict)
        assert refused.value.status is HTTPStatus.SERVICE_UNAVAILABLE
        assert refused.value.body.error == "shutting_down"


@pytest.mark.unit
def test_the_runner_refuses_a_job_until_the_running_one_has_finished() -> None:
    runner = JobRunner()
    gate = threading.Event()

    def slow() -> dict[str, object]:
        gate.wait(WAIT)
        return {}

    first = runner.submit("fetch", slow)

    with pytest.raises(ApiError) as refused:
        runner.submit("transform", dict)
    assert refused.value.status is HTTPStatus.CONFLICT
    gate.set()
    assert runner.wait(first.id, WAIT).status is JobStatus.SUCCEEDED
    second = runner.submit("transform", dict)
    assert runner.wait(second.id, WAIT).status is JobStatus.SUCCEEDED
    runner.shutdown()


@pytest.mark.unit
@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit])
def test_a_job_stopped_by_a_base_exception_is_failed_and_the_next_one_runs(
    stop: type[BaseException],
) -> None:
    runner = JobRunner()

    def stopped() -> dict[str, object]:
        raise stop

    job = runner.submit("fetch", stopped)

    done = runner.wait(job.id, WAIT)
    assert done.status is JobStatus.FAILED
    assert done.error is not None
    assert done.error.message == stop.__name__
    second = runner.submit("transform", dict)
    assert runner.wait(second.id, WAIT).status is JobStatus.SUCCEEDED
    runner.shutdown()


class UnprintableError(OSError):
    @override
    def __str__(self) -> str:
        raise RuntimeError


class ExitingError(OSError):
    @override
    def __str__(self) -> str:
        raise SystemExit


def no_dict() -> Any:  # a bug: the work must return a dict
    return ["not", "a", "dict"]


def unprintable() -> dict[str, object]:
    raise UnprintableError


def exiting() -> dict[str, object]:
    raise ExitingError


@pytest.mark.unit
@pytest.mark.parametrize("work", [no_dict, unprintable, exiting])
def test_a_job_whose_end_cannot_be_recorded_as_is_is_still_failed(
    work: Callable[[], dict[str, object]],
) -> None:
    runner = JobRunner()

    job = runner.submit("fetch", work)

    assert runner.wait(job.id, WAIT).status is JobStatus.FAILED
    second = runner.submit("transform", dict)
    assert runner.wait(second.id, WAIT).status is JobStatus.SUCCEEDED
    runner.shutdown()


@pytest.mark.unit
def test_the_runner_keeps_the_last_finished_jobs_and_the_running_one() -> None:
    runner = JobRunner()
    ids: list[str] = []
    for _ in range(KEEP_FINISHED + 1):
        job = runner.submit("fetch", dict)
        runner.wait(job.id, WAIT)
        ids += [job.id]
    gate = threading.Event()

    def slow() -> dict[str, object]:
        gate.wait(WAIT)
        return {}

    running = runner.submit("transform", slow)

    assert runner.get(ids[0]) is None
    assert all(runner.get(job_id) is not None for job_id in ids[1:])
    assert runner.get(running.id) is not None
    gate.set()
    assert runner.wait(running.id, WAIT).status is JobStatus.SUCCEEDED
    assert runner.get(ids[1]) is None
    assert runner.get(running.id) is not None
    runner.shutdown()
