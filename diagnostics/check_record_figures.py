"""Reproduces the figures about the BIS data quoted in the decision records.

- Decision 0005: for how many areas the 12-month change in basis points, computed in
  floating point, differs from the exact decimal result.
- Decision 0011: the highest valid rate of each area, which shows the implausible
  values of a few series (Brazil, Argentina).

The 12-month change follows the report's rule: the latest valid observation, and the
last valid one on or before the same date 12 months earlier.

Usage, from the repository root:
    uv run python diagnostics/check_record_figures.py [OBSERVATIONS]

OBSERVATIONS defaults to data/staged/observations.parquet (written by
``bis-prates transform``).
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

MONTHS = 12
HIGHEST = 4
DEFAULT_OBSERVATIONS = "data/staged/observations.parquet"


def main() -> None:
    """Prints both figures for the staged observations."""
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OBSERVATIONS)
    observations = pd.read_parquet(path, dtype_backend="pyarrow")
    valid = observations[observations["is_valid"]]

    not_exact: list[str] = []
    areas = valid.groupby("ref_area")
    for area, rows in areas:
        dates = pd.to_datetime(rows["date"].astype(str))
        target = dates.max() - pd.DateOffset(months=MONTHS)
        prior = rows[dates <= target]
        if prior.empty:
            continue
        latest_value = rows["value"].iloc[-1]
        prior_value = prior["value"].iloc[-1]
        exact = (latest_value - prior_value) * 100
        in_floats = (float(latest_value) - float(prior_value)) * 100
        if Decimal(repr(in_floats)) != exact:
            not_exact += [f"{area}: {exact:f} bp, in floats {in_floats}"]

    count = f"{len(not_exact)} of {areas.ngroups} areas"
    print(f"12-month change not exact in floats: {count}")
    for line in not_exact:
        print(f"  {line}")

    highest = areas["value"].max().sort_values(ascending=False).head(HIGHEST)
    print("Highest valid rate, in per cent:")
    for area, value in highest.items():
        print(f"  {area}: {value.normalize():,f}")


if __name__ == "__main__":
    main()
