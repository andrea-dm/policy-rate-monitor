"""Spike: pandas 3 with pyarrow-backed dtypes (decimal128, date32) for the snapshot.

Evidence for the pyarrow-backend decision (2026-11-17; findings in
diagnostics/README.md). Checks groupby, decimal arithmetic, Decimal scalars, DateOffset
on date32, merge_asof on Arrow keys, casts of non-numeric text and of extra decimals to
decimal128(12, 4), and the parquet round trip. Each check prints OK with its result, or
FAIL with the exception: a FAIL is a finding, not a broken script.

Run: uv run --with pyarrow python diagnostics/spike_pyarrow_dtypes.py
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

if TYPE_CHECKING:
    from collections.abc import Callable

RATE = pd.ArrowDtype(pa.decimal128(12, 4))
DATE = pd.ArrowDtype(pa.date32())
TEXT = pd.ArrowDtype(pa.string())
SAMPLE_CSV = (
    "REF_AREA,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "GB,2025-11-07,4,A\nGB,2025-11-10,4,A\nGB,2026-11-06,4,A\nGB,2026-11-09,4,A\n"
    "JP,2025-11-10,0.5,A\nJP,2026-11-07,NaN,M\nJP,2026-11-10,1.25,A\n"
    "AU,2025-11-05,3.75,A\nAU,2026-11-05,3.6,A\n"
)


@dataclass(frozen=True)
class Sample:
    """The typed sample: all rows, valid rows sorted by date, latest valid per area."""

    all_rows: pd.DataFrame
    valid: pd.DataFrame
    latest: pd.DataFrame


def load_sample() -> Sample:
    """Read the sample with the pyarrow engine and type the date and value columns."""
    raw = pd.read_csv(
        io.StringIO(SAMPLE_CSV),
        engine="pyarrow",
        dtype_backend="pyarrow",
        dtype={"OBS_VALUE": TEXT},
    )
    print("raw dtypes:", raw.dtypes.to_dict())
    typed = raw.assign(
        date=pd.to_datetime(raw["TIME_PERIOD"]).astype(DATE),
        value=raw["OBS_VALUE"].where(raw["OBS_VALUE"] != "NaN").astype(RATE),
    )
    print("value dtype:", typed["value"].dtype, "| nulls:", typed["value"].isna().sum())
    valid = typed[typed["OBS_STATUS"] == "A"].sort_values("date")
    latest = valid.groupby("REF_AREA").tail(1).set_index("REF_AREA")
    return Sample(all_rows=typed, valid=valid, latest=latest)


def groupby_tail(s: Sample) -> object:
    """Latest valid date and value per area."""
    return s.latest[["date", "value"]].to_dict("index")


def decimal_subtract(_: Sample) -> object:
    """3.60 - 3.75, times 100, in decimal128."""
    now = pd.Series([Decimal("3.6")], dtype=RATE)
    before = pd.Series([Decimal("3.75")], dtype=RATE)
    return ((now - before) * 100).tolist()


def scalar_type(s: Sample) -> object:
    """Python type of a decimal128 scalar."""
    value = s.latest.loc["JP", "value"]
    return f"{type(value).__name__} {value!r}"


def date_offset_on_date32(s: Sample) -> object:
    """Subtract 12 calendar months from date32[pyarrow] values."""
    return (s.latest["date"] - pd.DateOffset(months=12)).tolist()


def merge_asof_arrow_keys(s: Sample) -> object:
    """merge_asof with date32[pyarrow] join keys on both sides."""
    left = s.latest.reset_index()[["REF_AREA", "date"]]
    left["target"] = (left["date"] - pd.DateOffset(months=12)).astype(DATE)
    right = s.valid[["REF_AREA", "date", "value"]].rename(columns={"date": "prior"})
    merged = pd.merge_asof(
        left.sort_values("target"),
        right.sort_values("prior"),
        left_on="target",
        right_on="prior",
        by="REF_AREA",
    )
    return merged.to_dict("records")


def merge_asof_datetime64_keys(s: Sample) -> object:
    """merge_asof with datetime64[ns] join keys and decimal128 values."""
    left = s.latest.reset_index()[["REF_AREA", "date"]]
    left["target"] = left["date"].astype("datetime64[ns]") - pd.DateOffset(months=12)
    right = s.valid[["REF_AREA", "date", "value"]].rename(columns={"date": "prior"})
    right["prior"] = right["prior"].astype("datetime64[ns]")
    merged = pd.merge_asof(
        left.sort_values("target"),
        right.sort_values("prior"),
        left_on="target",
        right_on="prior",
        by="REF_AREA",
    )
    return [
        (row.REF_AREA, row.target.date(), row.prior.date(), row.value)
        for row in merged.itertuples()
    ]


def count_decimals(_: Sample) -> object:
    """Count the decimals in the source text, before any cast."""
    text = pd.Series(["94.6875", "3.12345"])
    return text.str.extract(r"\.(\d+)$")[0].str.len().tolist()


def cast_extra_decimals(_: Sample) -> object:
    """Cast a value with 5 decimals to scale 4."""
    return pd.Series(["3.12345"], dtype=TEXT).astype(RATE).tolist()


def cast_non_numeric(_: Sample) -> object:
    """Cast non-numeric text to decimal."""
    return pd.Series(["abc"], dtype=TEXT).astype(RATE).tolist()


def parquet_round_trip(s: Sample) -> object:
    """Write to parquet and read back: stored type and pandas dtypes."""
    path = Path(tempfile.mkdtemp()) / "sample.parquet"
    s.all_rows.to_parquet(path)
    back = pd.read_parquet(path, dtype_backend="pyarrow")
    stored = pq.read_schema(path).field("value").type
    return str(stored), str(back["value"].dtype), str(back["date"].dtype)


CHECKS: list[tuple[str, Callable[[Sample], object]]] = [
    ("groupby tail", groupby_tail),
    ("decimal subtract x100", decimal_subtract),
    ("scalar to Python", scalar_type),
    ("DateOffset on date32", date_offset_on_date32),
    ("merge_asof on date32[pyarrow] keys", merge_asof_arrow_keys),
    ("merge_asof on datetime64 keys, decimal values", merge_asof_datetime64_keys),
    ("count decimals in the source text", count_decimals),
    ("cast '3.12345' to decimal(12,4)", cast_extra_decimals),
    ("cast 'abc' to decimal", cast_non_numeric),
    ("parquet round trip types", parquet_round_trip),
]


def main() -> None:
    """Run every check and print its outcome."""
    print("pandas", pd.__version__, "pyarrow", pa.__version__)
    sample = load_sample()
    for name, check in CHECKS:
        try:
            result = check(sample)
        except Exception as exc:  # noqa: BLE001 - a spike records any failure as a finding
            print(f"FAIL {name}: {type(exc).__name__}: {str(exc)[:160]}")
        else:
            print(f"OK   {name}: {result}")


if __name__ == "__main__":
    main()
