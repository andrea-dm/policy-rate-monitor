from __future__ import annotations

import base64
import csv
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from policy_rate_monitor.config import DatasetConfig, parse_config
from policy_rate_monitor.core.models import (
    DataQualityIssue,
    DataQualitySeverity,
    DataSnapshot,
    SeriesMetadata,
    SnapshotFlag,
    SnapshotStatus,
    SourceRelease,
)
from policy_rate_monitor.report import ICONS, Report, ReportArgs

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "bis_cbpol.json"

# report.md as the code wrote it before the page moved to a template, with the emoji
# added since. A change to the texts in the configuration changes these pages too.
GOLDEN = Path(__file__).parent / "data" / "report"

# The staged value type (precision and scale from the config's measure).
RATE = pd.ArrowDtype(pa.decimal128(15, 6))

US = SeriesMetadata(
    series_key="D.US",
    ref_area="US",
    ref_area_name="United States",
    frequency="D",
    title="Policy rate, United States, daily",
    unit="Per cent per year",
    decimals=2,
)


@pytest.fixture(scope="module")
def config() -> DatasetConfig:
    return parse_config(CONFIG_PATH.read_text(encoding="utf-8"), origin="test")


def _full_args(out_dir: Path) -> ReportArgs:
    """A release with every field, flags, and a finding for every series."""
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
            series=US,
            latest_date=date(2026, 11, 10),
            latest_value=Decimal("4.000000"),
            target_date=date(2025, 11, 10),
            prior_date=date(2025, 11, 10),
            prior_value=Decimal("3.750000"),
            change_bps=Decimal("25.000000"),
            status=SnapshotStatus.OK,
            flags=(SnapshotFlag.STALE_PRIOR,),
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
        ),
        DataQualityIssue(
            rule="unexpected_value",
            severity=DataQualitySeverity.WARNING,
            series_key=None,
            detail="UNIT_MEASURE is 368 | 369",
            count=1,
        ),
    ]
    release = SourceRelease(
        location="https://example.org/data.zip",
        checksum_sha256="0" * 64,
        version_id="2026-11-18",
        retrieved_at=datetime(2026, 11, 19, 8, 0, tzinfo=UTC),
    )
    return ReportArgs(
        snapshots=snapshots,
        observations=observations,
        series=[US],
        issues=issues,
        release=release,
        out_dir=out_dir,
    )


def _empty_args(out_dir: Path) -> ReportArgs:
    """No record of the release, no findings and nothing to chart."""
    full = _full_args(out_dir)
    return ReportArgs(
        snapshots=full.snapshots[1:],
        observations=full.observations,
        series=[],
        issues=[],
        release=None,
        out_dir=out_dir,
    )


def _without_time(page: str) -> str:
    return re.sub(r"Generated on .* UTC\.", "Generated on <time> UTC.", page)


@pytest.mark.contract
def test_report_writes_the_five_files(config: DatasetConfig, tmp_path: Path) -> None:
    paths = Report(config).export(_full_args(tmp_path))

    assert [path.name for path in paths] == [
        "summary.csv",
        "summary.json",
        "policy_rates.png",
        "report.md",
        "report.html",
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
        "flags": "stale_prior",
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


@pytest.mark.contract
@pytest.mark.parametrize(
    ("make_args", "golden"),
    [(_full_args, "full.md"), (_empty_args, "empty.md")],
)
def test_report_md_matches_the_expected_page(
    config: DatasetConfig,
    tmp_path: Path,
    make_args: Callable[[Path], ReportArgs],
    golden: str,
) -> None:
    Report(config).export(make_args(tmp_path))

    page = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = (GOLDEN / golden).read_text(encoding="utf-8")
    assert _without_time(page) == _without_time(expected)
    assert re.search(
        r"^Generated on \d{4}-\d\d-\d\d at \d\d:\d\d UTC\.$", page, re.MULTILINE
    )


@pytest.mark.contract
def test_report_html_has_the_sections_of_report_md(
    config: DatasetConfig, tmp_path: Path
) -> None:
    Report(config).export(_full_args(tmp_path))

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    sections = re.findall(r"^## (.+)$", markdown, re.MULTILINE)
    assert sections == [
        "Summary",
        "About the data",
        "Method and assumptions",
        "Data quality",
        "Limitations",
    ]
    assert re.findall(r"<h2 id=\"[a-z]+\">(.+)</h2>", page) == sections
    assert '<h3 id="status-legend">Status legend</h3>\n\n<dl>' in page
    assert f"<h1>{config.dataset.title}</h1>" in page
    rows = re.findall(r"<tr>\n(.*?)\n</tr>", page, re.DOTALL)
    assert rows[1] == (
        '<th scope="row">United States</th>\n<td>US</td>\n<td>2026-11-10</td>\n'
        '<td class="num">4.00</td>\n<td>2025-11-10</td>\n<td class="num">3.75</td>\n'
        '<td class="num">25</td>\n<td><span aria-hidden="true">✅</span> ok</td>\n'
        '<td><span aria-hidden="true">⏳</span> stale_prior</td>'
    )
    # A code the data does not have is named by its code.
    assert rows[2].startswith('<th scope="row">ZZ</th>\n<td>ZZ</td>')
    assert (
        '<td>unexpected_value</td>\n<td><span aria-hidden="true">⚠️</span> warning</td>'
        "\n<td>all</td>"
    ) in page
    # A cell keeps its "|": only Markdown needs it escaped.
    assert "UNIT_MEASURE is 368 | 369" in page
    assert "<code>" + "0" * 64 + "</code>" in page


@pytest.mark.unit
def test_every_status_flag_and_severity_has_an_emoji() -> None:
    names = [*SnapshotStatus, *SnapshotFlag, *DataQualitySeverity]

    assert sorted(ICONS) == sorted(names)


@pytest.mark.contract
def test_report_html_hides_its_emoji_from_screen_readers(
    config: DatasetConfig, tmp_path: Path
) -> None:
    Report(config).export(_empty_args(tmp_path))

    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    body = page[page.index("<body>") :]
    emoji = re.findall("|".join(set(ICONS.values())), body)
    hidden = re.findall(r'<span aria-hidden="true">([^<]+)</span> ', body)
    assert emoji
    assert sorted(emoji) == sorted(hidden)
    assert '<span aria-hidden="true">✅</span> No findings.' in page


@pytest.mark.contract
def test_report_html_shows_the_interactive_chart_and_loads_nothing(
    config: DatasetConfig, tmp_path: Path
) -> None:
    Report(config).export(_full_args(tmp_path))

    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert 'class="plotly-graph-div"' in page
    assert "<img" not in page
    # Plotly's JavaScript is in the page, not fetched.
    assert not re.search(r"<script[^>]*\ssrc=", page)
    assert not re.search(r"<link\b", page)
    # HTML forbids control characters other than whitespace.
    assert not re.search("[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]", page)


@pytest.mark.contract
def test_without_plotly_report_html_embeds_the_png(
    config: DatasetConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As if the interactive extra were not installed: charts must be imported afresh.
    monkeypatch.delitem(sys.modules, "policy_rate_monitor.charts", raising=False)
    monkeypatch.setitem(sys.modules, "plotly", None)
    monkeypatch.setitem(sys.modules, "plotly.graph_objects", None)

    paths = Report(config).export(_full_args(tmp_path))

    page = paths[4].read_text(encoding="utf-8")
    assert "plotly" not in page.lower()
    found = re.search(r'<img src="data:image/png;base64,([^"]+)" alt="([^"]+)">', page)
    assert found is not None
    assert base64.b64decode(found[1]) == paths[2].read_bytes()
    assert found[2] == config.dataset.title


@pytest.mark.contract
def test_text_from_the_data_is_escaped_in_report_html(
    config: DatasetConfig, tmp_path: Path
) -> None:
    script = "<script>alert(1)</script>"
    dataset = config.dataset.model_copy(update={"title": f"Rates {script}"})
    config = config.model_copy(update={"dataset": dataset})
    args = _full_args(tmp_path)
    issue = args.issues[0].model_copy(update={"detail": f"bad {script}"})
    args = ReportArgs(
        snapshots=args.snapshots,
        observations=args.observations,
        series=args.series,
        issues=[issue],
        release=args.release,
        out_dir=tmp_path,
    )

    Report(config).export(args)

    page = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page
    assert "<h1>Rates &lt;script&gt;alert(1)&lt;/script&gt;</h1>" in page
    assert "<td>bad &lt;script&gt;alert(1)&lt;/script&gt;</td>" in page
    # Markdown has no escaping: the text is written as it is.
    assert f"# Rates {script}" in (tmp_path / "report.md").read_text(encoding="utf-8")
