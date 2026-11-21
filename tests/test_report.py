from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from policy_rate_monitor.config import parse_config
from policy_rate_monitor.core.models import (
    DataQualityIssue,
    DataQualitySeverity,
    DataSnapshot,
    SeriesMetadata,
    SnapshotStatus,
    SourceRelease,
)
from policy_rate_monitor.report import Report, ReportArgs

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "bis_cbpol.json"

# The staged value type (precision and scale from the config's measure).
RATE = pd.ArrowDtype(pa.decimal128(15, 6))


@pytest.mark.contract
def test_report_writes_the_four_files(tmp_path: Path) -> None:
    config = parse_config(CONFIG_PATH.read_text(encoding="utf-8"), origin="test")
    us = SeriesMetadata(
        series_key="D.US",
        ref_area="US",
        ref_area_name="United States",
        frequency="D",
        title="Policy rate, United States, daily",
        unit="Per cent per year",
        decimals=2,
    )
    observations = pd.DataFrame(
        {
            "series_key": ["D.US", "D.US", "D.US"],
            "date": pd.Series(
                [date(2025, 11, 10), date(2026, 6, 1), date(2026, 11, 10)],
                dtype="date32[pyarrow]",
            ),
            "value": pd.Series(
                [Decimal("3.75"), None, Decimal("4.00")],
                dtype=RATE,
            ),
            "is_valid": [True, False, True],
        }
    )
    snapshots = [
        DataSnapshot(
            requested_ref_area="us",
            ref_area="US",
            series=us,
            latest_date=date(2026, 11, 10),
            latest_value=Decimal("4.000000"),
            target_date=date(2025, 11, 10),
            prior_date=date(2025, 11, 10),
            prior_value=Decimal("3.750000"),
            change_bps=Decimal("25.000000"),
            status=SnapshotStatus.OK,
        ),
        DataSnapshot(
            requested_ref_area="ZZ",
            ref_area="ZZ",
            status=SnapshotStatus.UNKNOWN_REF_AREA,
        ),
    ]
    issues = [
        DataQualityIssue(
            rule="exact_duplicate",
            severity=DataQualitySeverity.INFO,
            series_key="D.US",
            detail="2 exact duplicate rows dropped",
            count=2,
        )
    ]
    release = SourceRelease(
        location="https://example.org/data.zip",
        checksum_sha256="0" * 64,
        retrieved_at=datetime(2026, 11, 19, 8, 0, tzinfo=UTC),
    )

    args = ReportArgs(
        snapshots=snapshots,
        observations=observations,
        series=[us],
        issues=issues,
        release=release,
        out_dir=tmp_path,
    )

    paths = Report(config).export(args)

    assert [path.name for path in paths] == [
        "summary.csv",
        "summary.json",
        "policy_rates.png",
        "report.md",
    ]
    assert all(path.stat().st_size > 0 for path in paths)

    with paths[0].open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0] == {
        "requested_ref_area": "us",
        "ref_area": "US",
        "series_key": "D.US",
        "ref_area_name": "United States",
        "title": "Policy rate, United States, daily",
        "unit": "Per cent per year",
        "decimals": "2",
        "latest_date": "2026-11-10",
        "latest_value": "4",
        "prior_date": "2025-11-10",
        "prior_value": "3.75",
        "change_bps": "25",
        "status": "ok",
        "flags": "",
    }
    assert rows[1]["ref_area"] == "ZZ"
    assert rows[1]["status"] == "unknown_ref_area"
    assert rows[1]["latest_value"] == ""

    summary = json.loads(paths[1].read_text(encoding="utf-8"))
    assert summary["dataset"] == {
        "id": config.dataset.id,
        "title": config.dataset.title,
    }
    assert summary["source"]["location"] == "https://example.org/data.zip"
    assert summary["snapshots"][0]["change_bps"] == 25
    assert summary["snapshots"][0]["latest_value"] == 4
    assert summary["snapshots"][1]["status"] == "unknown_ref_area"
    assert summary["snapshots"][1]["series"] is None

    report = paths[3].read_text(encoding="utf-8")
    assert "| United States | US | 2026-11-10 | 4.00 |" in report
    assert "![" in report
    assert "(policy_rates.png)" in report
