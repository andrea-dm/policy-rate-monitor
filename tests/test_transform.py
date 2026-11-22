from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from policy_rate_monitor.config import parse_config
from policy_rate_monitor.core.errors import MissingInputError, UnexpectedFormatError
from policy_rate_monitor.core.models import SourceRelease
from policy_rate_monitor.transform import (
    load_data_quality,
    load_observations,
    load_release,
    load_series,
    transform,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from policy_rate_monitor.config import DatasetConfig

pytestmark = pytest.mark.contract

CONFIG = {
    "dataset": {
        "id": "test_rates",
        "title": "Test rates",
        "description": "Synthetic rates for tests.",
        "attribution": "Test",
        "data_dir": "data",
    },
    "source": {
        "discovery_url": "https://example.org/downloads",
        "file_name": "rates.zip",
        "fallback_url": "https://example.org/rates.zip",
        "cache_ttl_hours": 24,
        "max_attempts": 1,
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 1,
    },
    "structure": {
        "dimensions": [
            {"id": "FREQ", "coded": True, "column": "freq"},
            {"id": "REF_AREA", "coded": True, "column": "ref_area"},
        ],
        "time_dimension": {"id": "TIME_PERIOD", "date_format": "%Y-%m-%d"},
        "measure": {
            "id": "OBS_VALUE",
            "precision": 15,
            "scale": 6,
            "unit_multiplier": "UNIT_MULT",
        },
        "observation_attributes": [
            {
                "id": "OBS_STATUS",
                "coded": True,
                "keep_as": "obs_status",
                "invalid_codes": ["M"],
            }
        ],
        "series_attributes": [
            {"id": "UNIT_MEASURE", "coded": True},
            {"id": "UNIT_MULT", "coded": True},
            {"id": "DECIMALS", "coded": True},
            {"id": "TITLE", "coded": False},
        ],
        "series_metadata": {
            "ref_area": "REF_AREA",
            "frequency": "FREQ",
            "title": "TITLE",
            "unit": "UNIT_MEASURE",
            "decimals": "DECIMALS",
        },
        "selection": {"FREQ": "D"},
    },
    "columns": {
        "encoding": "utf-8",
        "delimiter": ",",
        "header_label_separator": ":",
        "value_label_separator": ": ",
        "missing_values": ["NaN"],
        "unmapped": [],
        "optional": [],
    },
    "rules": [
        {"name": "unparseable_date", "severity": "warning"},
        {"name": "exact_duplicate", "severity": "info"},
        {
            "name": "conflicting_duplicate",
            "severity": "warning",
            "params": {"keep": "last"},
        },
    ],
    "snapshot": {"lookback_months": 12, "stale_latest_days": 30, "stale_prior_days": 7},
    "output": {"metadata_fields": ["series_key", "title"]},
    "ref_area_aliases": {},
}

HEADER = (
    "FREQ:Frequency,REF_AREA:Reference area,TIME_PERIOD:Time period,"
    "OBS_VALUE:Value,UNIT_MEASURE:Unit,UNIT_MULT:Unit multiplier,"
    "DECIMALS:Decimals,TITLE:Title,OBS_STATUS:Status"
)
US = "D: Daily,US: United States"
US_SERIES = "368: Per cent per year,0: Units,2: Two,US policy rate"
GB_ROW = (
    "D: Daily,GB: United Kingdom,2024-01-01,5.25,368: Per cent,0: Units,2: Two,GB,A"
)


def write_zip(data_dir: Path, *rows: str, header: str = HEADER) -> None:
    raw = data_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw / "rates.zip", "w") as archive:
        archive.writestr("rates.csv", "\n".join([header, *rows]) + "\n")


def write_release(data_dir: Path) -> SourceRelease:
    """Writes the record ``fetch`` keeps next to the ZIP, and returns it."""
    zip_path = data_dir / "raw" / "rates.zip"
    release = SourceRelease(
        location="https://example.org/rates.zip",
        checksum_sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    zip_path.with_suffix(".json").write_text(release.model_dump_json())
    return release


def make_config(**changes: object) -> DatasetConfig:
    return parse_config(json.dumps({**CONFIG, **changes}), origin="test")


def test_missing_values_are_kept_and_marked_invalid(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-02,NaN,{US_SERIES},M: Missing",
        f"{US},2024-01-03,,{US_SERIES},A: Normal",
    )
    transform(make_config(), tmp_path)

    observations = load_observations(tmp_path)
    assert observations.value.tolist()[0] == Decimal("5.25")
    assert observations.value.isna().tolist() == [False, True, True]
    assert observations.is_valid.tolist() == [True, False, False]
    assert observations.obs_status.tolist() == ["A", "M", "A"]


def test_duplicates_are_dropped_and_reported(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-01,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-02,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-02,5.50,{US_SERIES},A: Normal",
    )
    summary = transform(make_config(), tmp_path)

    observations = load_observations(tmp_path)
    assert observations.value.tolist() == [Decimal("5.25"), Decimal("5.50")]
    issues = {issue.rule: issue for issue in load_data_quality(tmp_path)}
    assert issues["exact_duplicate"].count == 1
    assert issues["conflicting_duplicate"].count == 1
    assert issues["conflicting_duplicate"].series_key == "D.US"
    assert (summary.rows_read, summary.observations, summary.issues) == (4, 2, 2)


def test_only_the_selected_frequency_is_kept(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"M: Monthly,US: United States,2024-01-31,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-31,5.25,{US_SERIES},A: Normal",
    )
    transform(make_config(), tmp_path)

    observations = load_observations(tmp_path)
    assert observations.series_key.tolist() == ["D.US"]
    assert [series.series_key for series in load_series(tmp_path)] == ["D.US"]


def test_unit_multiplier_is_applied_exactly(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,0.0525,368: Per cent per year,2: Hundreds,2: Two,"
        "US policy rate,A: Normal",
    )
    transform(make_config(), tmp_path)

    assert load_observations(tmp_path)["value"].tolist() == [Decimal("5.25")]


def test_a_value_with_more_decimals_than_the_type_is_never_rounded(
    tmp_path: Path,
) -> None:
    # pandas 2.3.3 silently rounded such a value when casting it to the decimal type.
    write_zip(
        tmp_path,
        f"{US},2024-01-01,3.1234567,{US_SERIES},A: Normal",
        f"{US},2024-01-02,3.123456,{US_SERIES},A: Normal",
    )
    rules = [{"name": "non_numeric_value", "severity": "warning"}]
    transform(make_config(rules=rules), tmp_path)

    observations = load_observations(tmp_path)
    assert observations.value.isna().tolist() == [True, False]
    assert observations.value.tolist()[1] == Decimal("3.123456")
    assert observations.is_valid.tolist() == [False, True]
    [issue] = load_data_quality(tmp_path)
    assert (issue.rule, issue.count) == ("non_numeric_value", 1)


def test_values_that_are_not_plain_numbers_are_read_as_decimal_reads_them(
    tmp_path: Path,
) -> None:
    # Plain numbers take a fast cast; these forms take the exact Decimal path.
    texts = ["5.250000000", "1E-2", " 4.5", "-.5", "1234567890"]
    write_zip(
        tmp_path,
        *(
            f"{US},2024-01-{day:02d},{text},{US_SERIES},A: Normal"
            for day, text in enumerate(texts, start=1)
        ),
    )
    transform(make_config(), tmp_path)

    values = load_observations(tmp_path)["value"]
    assert values.tolist()[:4] == [
        Decimal("5.25"),
        Decimal("0.01"),
        Decimal("4.5"),
        Decimal("-0.5"),
    ]
    # Ten whole digits do not fit precision 15 and scale 6.
    assert values.isna().tolist() == [False, False, False, False, True]


def test_unparseable_dates_are_dropped_and_reported(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-13-01,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-02,5.25,{US_SERIES},A: Normal",
    )
    transform(make_config(), tmp_path)

    assert len(load_observations(tmp_path)) == 1
    [issue] = load_data_quality(tmp_path)
    assert (issue.rule, issue.count) == ("unparseable_date", 1)


def test_series_metadata_is_kept(tmp_path: Path) -> None:
    write_zip(tmp_path, f"{US},2024-01-01,5.25,{US_SERIES},A: Normal")
    transform(make_config(), tmp_path)

    [series] = load_series(tmp_path)
    assert series.ref_area == "US"
    assert series.ref_area_name == "United States"
    assert series.frequency == "D"
    assert series.title == "US policy rate"
    assert series.unit == "Per cent per year"
    assert series.decimals == 2
    assert dict(series.attributes) == {"UNIT_MULT": "0"}


def test_the_csv_in_the_zip_is_found_whatever_the_case_of_its_extension(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    with zipfile.ZipFile(raw / "rates.zip", "w") as archive:
        archive.writestr("RATES.CSV", f"{HEADER}\n{US},2024-01-01,5.25,{US_SERIES},A\n")

    summary = transform(make_config(), tmp_path)

    assert summary.observations == 1


def test_missing_required_column_is_named(tmp_path: Path) -> None:
    write_zip(tmp_path, "D: Daily,US: United States", header="FREQ:F,REF_AREA:R")

    with pytest.raises(UnexpectedFormatError, match="TIME_PERIOD"):
        transform(make_config(), tmp_path)


def test_missing_inputs_say_which_step_to_run(tmp_path: Path) -> None:
    # The adapters word the hint; the message only says what is missing.
    with pytest.raises(MissingInputError, match=r"rates\.zip does not exist") as fetch:
        transform(make_config(), tmp_path)
    assert fetch.value.step == "fetch"
    for load in (load_observations, load_series, load_data_quality):
        with pytest.raises(MissingInputError, match=r"does not exist$") as staged:
            load(tmp_path)
        assert staged.value.step == "transform"


@pytest.mark.parametrize(
    ("enabled", "findings"), [(True, 1), (False, 0)], ids=["rule_on", "rule_off"]
)
def test_the_configured_keep_applies_whether_the_rule_is_on_or_not(
    tmp_path: Path, *, enabled: bool, findings: int
) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-02,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-02,5.50,{US_SERIES},A: Normal",
    )
    rule = {
        "name": "conflicting_duplicate",
        "severity": "warning",
        "enabled": enabled,
        "params": {"keep": "first"},
    }
    transform(make_config(rules=[rule]), tmp_path)

    assert load_observations(tmp_path).value.tolist() == [Decimal("5.25")]
    assert len(load_data_quality(tmp_path)) == findings


ALL_RULES = [
    {"name": name, "severity": "warning"}
    for name in ("non_numeric_value", "status_value_mismatch")
]


def findings(data_dir: Path) -> dict[str, int]:
    """The count of each rule's findings, summed over the series."""
    counts: dict[str, int] = {}
    for issue in load_data_quality(data_dir):
        counts[issue.rule] = counts.get(issue.rule, 0) + issue.count
    return counts


@pytest.mark.parametrize(
    "text",
    [
        "1_5",  # Decimal reads it as 15
        "\u0661\u0665",  # Arabic-Indic 15, which Decimal reads
        "\uff11\uff15",  # full-width 15
        "Infinity",
        "-inf",
        "nan",
        "sNaN",
        "5.2500000000000000000000000001",  # 29 digits: Decimal would round it
        "1e",
        "0x10",
        "\u00a05.25",  # a non-breaking space before it
        "5.25\u3000",  # an ideographic space after it
    ],
)
def test_only_a_plain_ascii_number_that_converts_exactly_is_a_value(
    tmp_path: Path, text: str
) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,{text},{US_SERIES},A: Normal",
        f"{US},2024-01-02, +5.25 ,{US_SERIES},A: Normal",
    )
    transform(make_config(rules=ALL_RULES), tmp_path)

    observations = load_observations(tmp_path)
    assert observations.value.isna().tolist() == [True, False]
    assert observations.value.tolist()[1] == Decimal("5.25")
    assert findings(tmp_path) == {"non_numeric_value": 1}


def test_a_value_with_many_digits_that_fits_is_kept(tmp_path: Path) -> None:
    # 28 significant digits, all but the first 9 zeros: exact, and fits (15, 6).
    write_zip(tmp_path, f"{US},2024-01-01,5.250000{'0' * 21},{US_SERIES},A: Normal")
    transform(make_config(rules=ALL_RULES), tmp_path)

    assert load_observations(tmp_path).value.tolist() == [Decimal("5.25")]
    assert findings(tmp_path) == {}


@pytest.mark.parametrize("unit_mult", ["", "1.5", "two", "1_0", "\u00a00"])
def test_a_bad_unit_multiplier_makes_its_rows_invalid_not_the_run_fail(
    tmp_path: Path, unit_mult: str
) -> None:
    gb = "D: Daily,GB: United Kingdom"
    write_zip(
        tmp_path,
        f"{gb},2024-01-01,5.25,368: Per cent,{unit_mult},2: Two,GB rate,A: Normal",
        f"{gb},2024-01-02,NaN,368: Per cent,{unit_mult},2: Two,GB rate,M: Missing",
        f"{US},2024-01-01,5.25,{US_SERIES},A: Normal",
    )
    transform(make_config(rules=ALL_RULES), tmp_path)

    observations = load_observations(tmp_path)
    assert observations.series_key.tolist() == ["D.GB", "D.GB", "D.US"]
    assert observations.value.isna().tolist() == [True, True, False]
    assert observations.is_valid.tolist() == [False, False, True]
    [issue] = load_data_quality(tmp_path)
    assert (issue.rule, issue.series_key, issue.count) == (
        "non_numeric_value",
        "D.GB",
        1,
    )
    assert "UNIT_MULT" in issue.detail


@pytest.mark.parametrize("decimals", ["", "1.5", "-1", "two"])
def test_a_bad_decimals_leaves_the_decimals_unknown_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, decimals: str
) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,5.25,368: Per cent,0: Units,{decimals},US rate,A: Normal",
    )
    with caplog.at_level(logging.WARNING):
        summary = transform(make_config(), tmp_path)

    [series] = load_series(tmp_path)
    assert series.decimals is None
    assert "DECIMALS of series D.US" in caplog.text
    assert summary.issues == 0


def test_a_value_that_is_not_a_number_is_one_finding_not_two(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,n/a,{US_SERIES},A: Normal",  # not a number
        f"{US},2024-01-02,,{US_SERIES},A: Normal",  # missing, valid status
        f"{US},2024-01-03,NaN,{US_SERIES},A: Normal",  # missing, valid status
        f"{US},2024-01-04,5.25,{US_SERIES},M: Missing",  # value, invalid status
        f"{US},2024-01-05,NaN,{US_SERIES},M: Missing",  # consistent
    )
    transform(make_config(rules=ALL_RULES), tmp_path)

    issues = load_data_quality(tmp_path)
    assert findings(tmp_path) == {"non_numeric_value": 1, "status_value_mismatch": 3}
    details = sorted(issue.detail for issue in issues if issue.count == 2)  # noqa: PLR2004
    assert details == [
        "2 observations with a valid status but a missing value; kept as invalid"
    ]


def test_unexpected_columns_are_reported(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,5.25,{US_SERIES},A: Normal,x,y",
        header=f"{HEADER},EXTRA:Extra,MORE:More",
    )
    rules = [{"name": "unexpected_column", "severity": "info"}]
    transform(make_config(rules=rules), tmp_path)

    [issue] = load_data_quality(tmp_path)
    assert (issue.rule, issue.series_key, issue.count) == ("unexpected_column", None, 2)
    assert "EXTRA, MORE" in issue.detail


def test_unexpected_values_are_reported_per_series(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},2024-01-01,5.25,{US_SERIES},A: Normal",
        f"{US},2024-01-02,5.25,368: Per cent per year,0: Units,2: Two,US policy "
        "rate,A: Normal",
        f"{US},2024-01-03,5.25,999: Other,0: Units,2: Two,US policy rate,A: Normal",
    )
    rules = [
        {
            "name": "unexpected_value",
            "severity": "warning",
            "params": {"expected": {"UNIT_MEASURE": "368"}},
        }
    ]
    transform(make_config(rules=rules), tmp_path)

    [issue] = load_data_quality(tmp_path)
    assert (issue.rule, issue.series_key, issue.count) == (
        "unexpected_value",
        "D.US",
        1,
    )
    assert "UNIT_MEASURE is not '368'" in issue.detail


def test_a_date_in_digits_of_another_script_is_not_a_date(tmp_path: Path) -> None:
    write_zip(
        tmp_path,
        f"{US},\uff12\uff10\uff12\uff14-01-01,5.25,{US_SERIES},A: Normal",  # full-width
        f"{US},2024-01-02,5.25,{US_SERIES},A: Normal",
    )
    rules = [{"name": "unparseable_date", "severity": "warning"}]
    transform(make_config(rules=rules), tmp_path)

    assert load_observations(tmp_path).date.astype(str).tolist() == ["2024-01-02"]
    assert findings(tmp_path) == {"unparseable_date": 1}


def corrupt_zip(data_dir: Path) -> None:
    # Larger than Arrow's 1 MB block, so a CRC error would surface in its threads.
    write_zip(data_dir, *[f"{US},2024-01-01,5.25,{US_SERIES},A: Normal"] * 20_000)
    path = data_dir / "raw" / "rates.zip"
    content = bytearray(path.read_bytes())
    content[45] ^= 0xFF  # inside the CSV's data: its CRC no longer matches
    path.write_bytes(bytes(content))


def not_a_zip(data_dir: Path) -> None:
    (data_dir / "raw").mkdir()
    (data_dir / "raw" / "rates.zip").write_bytes(b"<html>maintenance</html>")


def truncated_row(data_dir: Path) -> None:
    write_zip(data_dir, f"{US},2024-01-01,5.25,{US_SERIES},A: Normal", f"{US},2024")


def wrong_encoding(data_dir: Path) -> None:
    (data_dir / "raw").mkdir()
    with zipfile.ZipFile(data_dir / "raw" / "rates.zip", "w") as archive:
        row = f"{US},2024-01-01,5.25,{US_SERIES},A: Normal"
        text = f"{HEADER}\n{row.replace('United States', 'Été')}\n"
        archive.writestr("rates.csv", text.encode("latin-1"))


def empty_csv(data_dir: Path) -> None:
    (data_dir / "raw").mkdir()
    with zipfile.ZipFile(data_dir / "raw" / "rates.zip", "w") as archive:
        archive.writestr("rates.csv", "")


def header_only(data_dir: Path) -> None:
    write_zip(data_dir)


def no_daily_rows(data_dir: Path) -> None:
    write_zip(data_dir, f"M: Monthly,US: United States,2024-01,5.25,{US_SERIES},A")


@pytest.mark.parametrize(
    ("write", "message"),
    [
        (corrupt_zip, "is not a readable ZIP file: Bad CRC-32"),
        (not_a_zip, "is not a readable ZIP file"),
        (truncated_row, "cannot be read"),
        (wrong_encoding, "cannot be read"),
        (empty_csv, "cannot be read"),
        (header_only, "holds no rows"),
        (no_daily_rows, "has no rows with FREQ = D"),
    ],
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_a_source_file_that_cannot_be_read_is_an_unexpected_format(
    tmp_path: Path, write: Callable[[Path], None], message: str
) -> None:
    write(tmp_path)

    with pytest.raises(UnexpectedFormatError, match=rf"rates\.zip {message}"):
        transform(make_config(), tmp_path)


def test_a_series_row_with_no_ref_area_code_is_an_unexpected_format(
    tmp_path: Path,
) -> None:
    write_zip(tmp_path, f"D: Daily,,2024-01-01,5.25,{US_SERIES},A: Normal")

    with pytest.raises(UnexpectedFormatError, match=r"series D\. has invalid metadata"):
        transform(make_config(), tmp_path)


def test_the_release_of_the_zip_read_is_staged_last(tmp_path: Path) -> None:
    write_zip(tmp_path, f"{US},2024-01-01,5.25,{US_SERIES},A: Normal")
    release = write_release(tmp_path)

    transform(make_config(), tmp_path)

    assert load_release(tmp_path) == release
    staged = tmp_path / "staged"
    assert sorted(path.name for path in staged.iterdir()) == [
        "data_quality.json",
        "observations.parquet",
        "release.json",
        "series.json",
    ]
    assert (staged / "release.json").stat().st_mtime_ns >= max(
        path.stat().st_mtime_ns for path in staged.iterdir()
    )


def test_no_release_is_staged_without_a_record_of_the_zip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_zip(tmp_path, f"{US},2024-01-01,5.25,{US_SERIES},A: Normal")
    write_release(tmp_path)
    transform(make_config(), tmp_path)
    assert load_release(tmp_path) is not None

    # A record of another ZIP, one with no checksum, then none at all: the stale
    # release.json goes.
    write_zip(tmp_path, f"{US},2024-01-02,5.50,{US_SERIES},A: Normal")
    transform(make_config(), tmp_path)
    assert load_release(tmp_path) is None
    record = write_release(tmp_path).model_copy(update={"checksum_sha256": None})
    (tmp_path / "raw" / "rates.json").write_text(record.model_dump_json())
    transform(make_config(), tmp_path)
    assert load_release(tmp_path) is None
    (tmp_path / "raw" / "rates.json").unlink()
    with caplog.at_level(logging.WARNING):
        transform(make_config(), tmp_path)
    assert load_release(tmp_path) is None
    assert not (tmp_path / "staged" / "release.json").exists()
    assert "no valid release record next to the ZIP" in caplog.text


def test_an_unreadable_staged_release_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "staged").mkdir()
    (tmp_path / "staged" / "release.json").write_bytes(b"\xff not UTF-8")

    assert load_release(tmp_path) is None


def test_a_failed_write_leaves_the_staged_files_of_the_last_run(tmp_path: Path) -> None:
    write_zip(tmp_path, f"{US},2024-01-01,5.25,{US_SERIES},A: Normal")
    write_release(tmp_path)
    transform(make_config(), tmp_path)
    staged = tmp_path / "staged"
    before = {path.name: path.read_bytes() for path in staged.iterdir()}

    write_zip(tmp_path, GB_ROW, f"{US},2024-01-02,5.50,{US_SERIES},A: Normal")
    write_release(tmp_path)
    staged.chmod(0o500)  # no new file can be written there
    try:
        with pytest.raises(PermissionError):
            transform(make_config(), tmp_path)
    finally:
        staged.chmod(0o700)

    assert {path.name: path.read_bytes() for path in staged.iterdir()} == before
    assert sorted(path.name for path in (tmp_path / "raw").iterdir()) == [
        "rates.json",
        "rates.parquet",
        "rates.zip",
    ]
