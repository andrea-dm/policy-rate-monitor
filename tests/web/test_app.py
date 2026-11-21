"""The read endpoints and the errors of the HTTP API, against the in-memory provider."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from policy_rate_monitor.cli.main import main
from policy_rate_monitor.core.models import SnapshotStatus
from policy_rate_monitor.service import Observation, TimeSeries
from policy_rate_monitor.web import create_app
from tests.web.conftest import CONFIG, REQUESTED, StagedLater

if TYPE_CHECKING:
    from pathlib import Path

    from policy_rate_monitor.config import DatasetConfig
    from policy_rate_monitor.providers import InMemoryProvider

pytestmark = pytest.mark.contract


def test_summary_gives_the_same_snapshots_as_the_cli(
    client: TestClient,
    provider: InMemoryProvider,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def memory_provider(*_args: object) -> InMemoryProvider:
        return provider

    monkeypatch.setattr(
        "policy_rate_monitor.cli.main.BulkFileProvider", memory_provider
    )
    argv = ["report", "--countries", ",".join(REQUESTED), "--config", str(CONFIG)]
    main([*argv, "--out", str(tmp_path / "cli")])
    from_cli = json.loads((tmp_path / "cli" / "summary.json").read_text())

    response = client.get("/summary", params={"countries": ",".join(REQUESTED)})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshots"] == from_cli["snapshots"]
    assert body["source"] == from_cli["source"]
    assert body["dataset"] == from_cli["dataset"]
    statuses = {
        item["requested_ref_area"]: item["status"] for item in body["snapshots"]
    }
    assert statuses == {
        "US": SnapshotStatus.OK,
        "EA": SnapshotStatus.OK,
        "JP": SnapshotStatus.NO_VALID_DATA,
        "CH": SnapshotStatus.INSUFFICIENT_HISTORY,
        "UK": SnapshotStatus.UNKNOWN_REF_AREA,
    }


def test_ref_areas_lists_every_code_with_its_name_and_aliases(
    client: TestClient,
) -> None:
    response = client.get("/ref-areas")

    assert response.status_code == 200
    assert response.json() == [
        {"ref_area": "CH", "ref_area_name": "Switzerland", "aliases": []},
        {"ref_area": "JP", "ref_area_name": "Japan", "aliases": []},
        {"ref_area": "US", "ref_area_name": "United States", "aliases": []},
        {"ref_area": "XM", "ref_area_name": "Euro area", "aliases": ["EA"]},
    ]


def test_timeseries_gives_the_valid_rates_exactly(client: TestClient) -> None:
    response = client.get("/timeseries", params={"country": "US"})

    assert response.status_code == 200
    # Read as JSON text, so the numbers become Decimals without passing through floats.
    found = TimeSeries.model_validate_json(response.text)
    assert found.observations == (
        Observation(date=date(2025, 1, 2), value=Decimal("4.5")),
        Observation(date=date(2026, 1, 2), value=Decimal("3.75")),
    )


def test_timeseries_starts_at_start_and_reads_an_alias(client: TestClient) -> None:
    response = client.get(
        "/timeseries", params={"country": "ea", "start": "2025-06-01"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested_ref_area"] == "ea"
    assert body["series"]["ref_area"] == "XM"
    assert body["observations"] == [{"date": "2026-01-02", "value": 2.0}]


def test_timeseries_of_an_unknown_code_is_not_found(client: TestClient) -> None:
    response = client.get("/timeseries", params={"country": "UK"})

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": "no series for UK; GET /ref-areas lists the codes",
    }


def test_chart_has_one_line_per_ref_area_with_a_latest_rate(
    client: TestClient,
) -> None:
    response = client.get("/chart", params={"countries": ",".join(REQUESTED)})

    assert response.status_code == 200
    figure = response.json()
    assert [trace["name"] for trace in figure["data"]] == [
        "United States",
        "Euro area",
        "Switzerland",
    ]
    assert figure["data"][0]["y"] == [4.5, 3.75]
    assert figure["data"][0]["line"]["shape"] == "hv"


@pytest.mark.parametrize(
    "path",
    [
        "/summary?countries=,",
        "/summary",
        "/timeseries?country=US&start=2025-13-01",
        "/timeseries?country=%20",
        "/chart?countries=",
    ],
)
def test_an_invalid_request_gets_422_with_the_error_body(
    client: TestClient, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error", "message"}
    assert body["error"] == "invalid_request"


def test_an_unknown_route_gets_404_with_the_error_body(client: TestClient) -> None:
    response = client.get("/nothing-here")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "message": "Not Found"}


def test_a_wrong_method_gets_405_with_the_error_body_and_allow(
    client: TestClient,
) -> None:
    response = client.delete("/summary")

    assert response.status_code == 405
    assert response.json() == {"error": "http_error", "message": "Method Not Allowed"}
    assert response.headers["Allow"] == "GET"


@pytest.mark.parametrize(
    "path",
    [
        "/ref-areas",
        "/summary?countries=US",
        "/timeseries?country=US",
        "/chart?countries=US",
    ],
)
def test_with_nothing_staged_the_read_endpoints_say_to_run_transform(
    config: DatasetConfig, tmp_path: Path, path: str
) -> None:
    client = TestClient(create_app(config, StagedLater(config), tmp_path))

    response = client.get(path)

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "missing_input"
    assert "transform" in body["message"]


def test_a_bug_gets_500_without_its_traceback(
    config: DatasetConfig,
    provider: InMemoryProvider,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def broken() -> list[str]:
        msg = "secret detail of a bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(provider, "list_series", broken)
    client = TestClient(
        create_app(config, provider, tmp_path), raise_server_exceptions=False
    )

    response = client.get("/ref-areas")

    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "message": "RuntimeError"}
    assert "Traceback" not in response.text
    assert "secret detail" not in response.text


def test_the_openapi_document_describes_the_real_error_body(
    client: TestClient,
) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    responses = paths["/timeseries"]["get"]["responses"]
    for status in ("404", "409", "422", "500"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorBody"}


def test_the_openapi_document_lists_every_route(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {
        "/ref-areas",
        "/summary",
        "/timeseries",
        "/chart",
        "/jobs/fetch",
        "/jobs/transform",
        "/jobs/report",
        "/jobs/{job_id}",
    }
