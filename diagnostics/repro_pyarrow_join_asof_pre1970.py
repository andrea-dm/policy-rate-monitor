"""Minimal reproduction: pyarrow ``Table.join_asof`` misses matches for pre-1970 keys.

Observed on 2026-11-17 with pyarrow 26.0.1. ``date32`` stores days since 1970-01-01,
so earlier dates are negative. If the right table holds ANY negative key, the as-of
join returns null for the group, even for matches that exist after 1970. No error is
raised. Plain int32 keys behave the same; shifting all keys to be non-negative fixes it.

This is one reason the pyarrow join was rejected for the 12-month look-back
(see diagnostics/README.md).

Run: uv run --with pyarrow python diagnostics/repro_pyarrow_join_asof_pre1970.py
"""

from __future__ import annotations

import datetime as dt

import pyarrow as pa

TARGET = dt.date(2025, 11, 9)  # a Sunday; the expected match is Friday 2025-11-07
EXPECTED_ROW = 1


def prior_row(right_dates: list[dt.date]) -> int | None:
    """Return the right-row index matched for ``TARGET`` (None: no match)."""
    left = pa.table({"key": ["A"], "on": pa.array([TARGET], pa.date32())})
    right = pa.table(
        {
            "key": ["A"] * len(right_dates),
            "on": pa.array(right_dates, pa.date32()),
            "row": list(range(len(right_dates))),
        }
    )
    joined = left.join_asof(right, on="on", by="key", tolerance=-3650)
    return joined["row"][0].as_py()


def main() -> None:
    """Run the three cases and print whether each one matched the expected row."""
    print("pyarrow", pa.__version__)
    friday = dt.date(2025, 11, 7)
    cases = {
        "1970-01-02, 2025-11-07": [dt.date(1970, 1, 2), friday],
        "1969-12-31, 2025-11-07": [dt.date(1969, 12, 31), friday],
        "1946-01-01, 2025-11-07": [dt.date(1946, 1, 1), friday],
    }
    for label, dates in cases.items():
        got = prior_row(dates)
        verdict = "ok" if got == EXPECTED_ROW else f"WRONG (expected {EXPECTED_ROW})"
        print(f"right dates {label}: matched row {got!r:<5} {verdict}")


if __name__ == "__main__":
    main()
