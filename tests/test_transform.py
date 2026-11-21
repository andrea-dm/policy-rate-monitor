from __future__ import annotations

import json
import zipfile
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from policy_rate_monitor.config import parse_config
from policy_rate_monitor.core.errors import MissingInputError, UnexpectedFormatError
from policy_rate_monitor.transform import (
    load_data_quality,
    load_observations,
    load_series,
    transform,
)

if TYPE_CHECKING:
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


def write_zip(data_dir: Path, *rows: str, header: str = HEADER) -> None:
    raw = data_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw / "rates.zip", "w") as archive:
        archive.writestr("rates.csv", "\n".join([header, *rows]) + "\n")


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
    with pytest.raises(MissingInputError, match="bis-prates fetch"):
        transform(make_config(), tmp_path)
    with pytest.raises(MissingInputError, match="bis-prates transform"):
        load_observations(tmp_path)
