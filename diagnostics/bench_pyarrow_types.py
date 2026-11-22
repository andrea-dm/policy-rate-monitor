"""Checks the library behaviour that decisions 0001 and 0006 rely on.

Decision 0001 stores rates as ``decimal128`` and dates as ``date32``, held by pandas 3
with pyarrow-backed types, in parquet files. Decision 0006 rejects pandas ``merge_asof``
and pyarrow ``join_asof`` for the 12-month look-back. Each check prints a claim, what it
expects in brackets, and what the installed pandas and pyarrow do; a difference is a
finding (2026-11-17, pandas 3.1.1, pyarrow 26.0.1; see diagnostics/README.md).

Run, from the repository root:
    uv run python diagnostics/bench_pyarrow_types.py
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

import pandas as pd
import pyarrow as pa

RATE = pa.decimal128(12, 4)
SUNDAY = dt.date(2025, 11, 9)
FRIDAY = dt.date(2025, 11, 7)


def exact_change() -> str:
    """The 12-month change in basis points, in decimal128 and in float."""
    latest = pd.Series([Decimal("4.10")], dtype=pd.ArrowDtype(RATE))
    prior = pd.Series([Decimal("3.85")], dtype=pd.ArrowDtype(RATE))
    change = ((latest - prior) * 100).tolist()
    return f"{change} (float: {(4.10 - 3.85) * 100})"


def parquet_round_trip() -> str:
    """A rate and a date before 1970, written to parquet and read back."""
    frame = pd.DataFrame(
        {
            "date": pd.Series([dt.date(1946, 1, 1)], dtype=pd.ArrowDtype(pa.date32())),
            "value": pd.Series([Decimal("3.85")], dtype=pd.ArrowDtype(RATE)),
        }
    )
    buffer = io.BytesIO()
    frame.to_parquet(buffer)
    back = pd.read_parquet(io.BytesIO(buffer.getvalue()), dtype_backend="pyarrow")
    return f"{back.dtypes.astype(str).tolist()} {back.to_dict('records')}"


def cast_extra_decimals() -> str:
    """A value with more decimals than the scale, cast to decimal128(12, 4)."""
    text = pd.Series(["3.12345"], dtype=pd.ArrowDtype(pa.string()))
    try:
        cast = text.astype(pd.ArrowDtype(RATE))
    except pa.ArrowInvalid as error:
        return f"refused: {error}"
    return f"rounded to {cast.tolist()}"


def merge_asof_on_arrow_dates() -> str:
    """pandas merge_asof with date32[pyarrow] keys."""
    dates = pd.ArrowDtype(pa.date32())
    left = pd.DataFrame({"on": pd.Series([SUNDAY], dtype=dates)})
    right = pd.DataFrame({"on": pd.Series([FRIDAY], dtype=dates), "row": [0]})
    try:
        merged = pd.merge_asof(left, right, on="on")
    except (TypeError, ValueError) as error:
        return f"refused: {type(error).__name__}: {error}"
    return f"matched row {merged.row.tolist()}"


def join_asof_before_1970() -> str:
    """pyarrow join_asof, with and without a right key before 1970."""
    found: list[str] = []
    for first in (dt.date(1970, 1, 2), dt.date(1946, 1, 1)):
        left = pa.table({"key": ["A"], "on": pa.array([SUNDAY], pa.date32())})
        right = pa.table(
            {
                "key": ["A", "A"],
                "on": pa.array([first, FRIDAY], pa.date32()),
                "row": [0, 1],
            }
        )
        joined = left.join_asof(right, on="on", by="key", tolerance=-36500)
        found += [f"first key {first}: row {joined.column('row')[0].as_py()}"]
    return ", ".join(found)


CHECKS = {
    "decimal128 change x 100 [exact: 25]": exact_change,
    "parquet keeps decimal128 and a 1946 date32 [same types and values]": (
        parquet_round_trip
    ),
    "cast '3.12345' to scale 4 [refused, not rounded]": cast_extra_decimals,
    "merge_asof on date32[pyarrow] keys [refused: TypeError]": (
        merge_asof_on_arrow_dates
    ),
    "join_asof for Sunday, prior Friday is row 1 [row None with a key before 1970]": (
        join_asof_before_1970
    ),
}


def main() -> None:
    """Runs every check and prints what the installed libraries do."""
    print("pandas", pd.__version__, "pyarrow", pa.__version__)
    for claim, check in CHECKS.items():
        print(f"- {claim}\n  {check()}")


if __name__ == "__main__":
    main()
