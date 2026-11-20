"""Benchmark: the 12-month 'on or before' snapshot for ALL series, five approaches.

Evidence for the look-back decision (2026-11-17; findings in diagnostics/README.md).
Every approach computes, per series: the latest valid observation, the target date
12 calendar months earlier, and the last valid observation on or before it. Outputs
are hashed (``digest``): identical digests at the same scale mean identical answers.

Scaling: the real daily series are replicated ``scale`` times under new codes
(GB_0, GB_1, ...).

Usage, from the repository root (duckdb is benchmark-only, not a project dependency):
    BENCH="uv run --with pyarrow --with duckdb python diagnostics/bench_snapshot.py"
    $BENCH prep                      # writes tmp/bench_base.parquet
    $BENCH run <approach> <scale>    # prints one JSON line

Approaches: bisect_py, searchsorted_np, pandas_merge_asof, arrow_join_asof, duckdb_asof
"""

from __future__ import annotations

import bisect
import hashlib
import json
import resource
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable

ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists()
)
CSV = ROOT / "resources" / "WS_CBPOL_csv_flat.csv"
BASE = ROOT / "tmp" / "bench_base.parquet"  # gitignored scratch
RATE = pa.decimal128(12, 4)
SOURCE_COLUMNS = [
    "FREQ:Frequency",
    "REF_AREA:Reference area",
    "TIME_PERIOD:Time period or range",
    "OBS_VALUE:Observation Value",
    "OBS_STATUS:Observation Status",
]

# (latest_date, latest_value, target_date, prior_date, prior_value) per series
type Snapshot = tuple[Any, Any, Any, Any, Any]
type Results = dict[str, Snapshot]


def prep(csv_path: str | Path = CSV) -> None:
    """Keep valid daily observations from the bulk CSV and write the base parquet."""
    table = pacsv.read_csv(
        csv_path,
        convert_options=pacsv.ConvertOptions(
            include_columns=SOURCE_COLUMNS,
            column_types={c: pa.string() for c in SOURCE_COLUMNS},
        ),
    ).rename_columns(["freq", "ref_area", "period", "value", "status"])
    daily = pc.starts_with(table["freq"], "D:")
    valid = pc.invert(pc.starts_with(table["status"], "M:"))
    table = table.filter(pc.and_(daily, valid))
    dates = pc.strptime(table["period"], format="%Y-%m-%d", unit="s")
    base = pa.table(
        {
            "ref_area": pc.utf8_slice_codeunits(table["ref_area"], 0, 2),
            "date": pc.cast(dates, pa.date32()),
            "value": pc.cast(table["value"], RATE),
        }
    )
    BASE.parent.mkdir(exist_ok=True)
    pq.write_table(base, BASE)
    series = len(pc.unique(base["ref_area"]))
    print(f"base rows={base.num_rows:,} series={series}")


def load(scale: int) -> pa.Table:
    """Return the base table replicated ``scale`` times, sorted by (series, date)."""
    base = pq.read_table(BASE)
    parts = [
        base.set_column(
            0, "ref_area", pc.binary_join_element_wise(base["ref_area"], f"_{k}", "")
        )
        for k in range(scale)
    ]
    table = pa.concat_tables(parts).combine_chunks()
    return table.sort_by([("ref_area", "ascending"), ("date", "ascending")])


def minus_12_months(dates: list[dt.date]) -> list[dt.date]:
    """Subtract 12 calendar months from each date (29 Feb maps to 28 Feb)."""
    return [(pd.Timestamp(d) - pd.DateOffset(months=12)).date() for d in dates]


def group_bounds(table: pa.Table) -> tuple[list[str], np.ndarray]:
    """Return series names and the start offsets of each series (plus the end)."""
    areas = table["ref_area"].to_numpy(zero_copy_only=False)
    starts = np.flatnonzero(np.r_[True, areas[1:] != areas[:-1]])
    return list(areas[starts]), np.r_[starts, len(areas)]


def to_date(day_number: int) -> dt.date:
    """Convert a date32 day number to a Python date."""
    return pa.scalar(day_number, pa.int32()).cast(pa.date32()).as_py()


def bisect_py(table: pa.Table) -> Results:
    """Approach A: per series, convert to Python lists and use ``bisect``."""
    names, bounds = group_bounds(table)
    out: Results = {}
    for i, name in enumerate(names):
        series = table.slice(bounds[i], bounds[i + 1] - bounds[i])
        dates, values = series["date"].to_pylist(), series["value"].to_pylist()
        target = minus_12_months([dates[-1]])[0]
        j = bisect.bisect_right(dates, target) - 1
        prior = (dates[j], values[j]) if j >= 0 else (None, None)
        out[name] = (dates[-1], values[-1], target, *prior)
    return out


def searchsorted_np(table: pa.Table) -> Results:
    """Approach B: per series, ``np.searchsorted`` on the date32 day numbers."""
    names, bounds = group_bounds(table)
    days = table["date"].combine_chunks().cast(pa.int32()).to_numpy()
    values = table["value"].combine_chunks()
    last = bounds[1:] - 1
    latest = [to_date(int(days[k])) for k in last]
    targets = minus_12_months(latest)
    epoch = np.datetime64("1970-01-01")
    out: Results = {}
    for i, name in enumerate(names):
        target_day = (np.datetime64(targets[i]) - epoch).astype(int)
        start, end = bounds[i], bounds[i + 1]
        j = start + np.searchsorted(days[start:end], target_day, side="right") - 1
        prior = (
            (to_date(int(days[j])), values[int(j)].as_py())
            if j >= start
            else (None, None)
        )
        out[name] = (latest[i], values[int(last[i])].as_py(), targets[i], *prior)
    return out


def pandas_merge_asof(table: pa.Table) -> Results:
    """Approach C: one ``pd.merge_asof`` over all series (keys as datetime64)."""
    df = table.to_pandas(types_mapper=pd.ArrowDtype)
    latest = df.groupby("ref_area", sort=False).tail(1)
    left = pd.DataFrame(
        {
            "ref_area": latest["ref_area"].to_numpy(),
            "latest_date": latest["date"].astype("datetime64[ns]").to_numpy(),
            "latest_value": latest["value"].to_numpy(),
        }
    )
    left["target"] = left["latest_date"] - pd.DateOffset(months=12)
    right = pd.DataFrame(
        {
            "ref_area": df["ref_area"].to_numpy(),
            "prior_date": df["date"].astype("datetime64[ns]").to_numpy(),
            "prior_value": df["value"],
        }
    )
    merged = pd.merge_asof(
        left.sort_values("target"),
        right.sort_values("prior_date"),
        left_on="target",
        right_on="prior_date",
        by="ref_area",
        direction="backward",
    )
    return {
        r.ref_area: (
            r.latest_date.date(),
            r.latest_value,
            r.target.date(),
            None if pd.isna(r.prior_date) else r.prior_date.date(),
            None if pd.isna(r.prior_value) else r.prior_value,
        )
        for r in merged.itertuples()
    }


def arrow_join_asof(table: pa.Table) -> Results:
    """Approach D: ``pa.Table.join_asof`` with the workarounds it needs.

    pyarrow 26 matches nothing when any key is negative (date32 before 1970), rejects
    decimal value columns, and requires a tolerance. Keys are therefore shifted to be
    non-negative, a row index is joined instead of the values, and the tolerance spans
    the whole history.
    """
    _, bounds = group_bounds(table)
    latest = table.take(pa.array(bounds[1:] - 1))
    targets = minus_12_months(latest["date"].to_pylist())
    days = table["date"].combine_chunks().cast(pa.int32())
    offset = min(pc.min(days).as_py(), 0)
    target_days = pa.array(targets, pa.date32()).cast(pa.int32())
    left = pa.table(
        {
            "ref_area": latest["ref_area"],
            "target": pc.subtract(target_days, offset),
        }
    )
    right = pa.table(
        {
            "ref_area": table["ref_area"],
            "target": pc.subtract(days, offset),
            "row": pa.array(np.arange(table.num_rows)),
        }
    )
    history = pc.max(table["date"]).as_py() - pc.min(table["date"]).as_py()
    joined = left.sort_by("target").join_asof(
        right.sort_by("target"), on="target", by="ref_area", tolerance=-history.days - 1
    )
    prior_dates = pc.take(table["date"], joined["row"]).to_pylist()
    prior_values = pc.take(table["value"], joined["row"]).to_pylist()
    joined_targets = pc.add(joined["target"], offset).cast(pa.int32()).cast(pa.date32())
    by_area = {row["ref_area"]: row for row in latest.to_pylist()}
    return {
        area: (by_area[area]["date"], by_area[area]["value"], target, p_date, p_value)
        for area, target, p_date, p_value in zip(
            joined["ref_area"].to_pylist(),
            joined_targets.to_pylist(),
            prior_dates,
            prior_values,
            strict=True,
        )
    }


DUCKDB_QUERY = """
    WITH latest AS (
        SELECT ref_area,
               arg_max(date, date)  AS latest_date,
               arg_max(value, date) AS latest_value
        FROM obs
        GROUP BY ref_area
    ),
    l AS (
        SELECT *, CAST(latest_date - INTERVAL 12 MONTH AS DATE) AS target FROM latest
    )
    SELECT l.ref_area, l.latest_date, l.latest_value, l.target, p.date, p.value
    FROM l ASOF LEFT JOIN obs p ON l.ref_area = p.ref_area AND l.target >= p.date
"""


def duckdb_asof(table: pa.Table) -> Results:
    """Approach E: DuckDB ``ASOF LEFT JOIN`` over the Arrow table (no copy)."""
    con = duckdb.connect()
    con.register("obs", table)
    rows = con.execute(DUCKDB_QUERY).fetchall()
    return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in rows}


APPROACHES: dict[str, Callable[[pa.Table], Results]] = {
    f.__name__: f
    for f in (
        bisect_py,
        searchsorted_np,
        pandas_merge_asof,
        arrow_join_asof,
        duckdb_asof,
    )
}


def normalise(results: Results) -> dict[str, tuple[str | Decimal | None, ...]]:
    """Make results comparable across approaches (strings for dates, 4-dp Decimals)."""

    def dec(x: object) -> Decimal | None:
        return None if x is None else Decimal(str(x)).quantize(Decimal("0.0001"))

    def day(x: object) -> str | None:
        return None if x is None else str(x)

    return {
        k: (day(v[0]), dec(v[1]), day(v[2]), day(v[3]), dec(v[4]))
        for k, v in results.items()
    }


def run(approach: str, scale: int) -> None:
    """Time one approach at one scale and print a JSON line with the result digest."""
    table = load(scale)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start = time.perf_counter()
    results = APPROACHES[approach](table)
    seconds = time.perf_counter() - start
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    normalised = normalise(results)
    payload = json.dumps(sorted(normalised.items()), default=str).encode()
    print(
        json.dumps(
            {
                "approach": approach,
                "scale": scale,
                "rows": table.num_rows,
                "series": len(results),
                "seconds": round(seconds, 3),
                "extra_peak_mb": round((rss_after - rss_before) / 1024),
                "digest": hashlib.sha256(payload).hexdigest()[:12],
                "sample_GB_0": [str(x) for x in normalised.get("GB_0", ())],
            }
        )
    )


def main(argv: list[str]) -> None:
    """Dispatch ``prep [csv]`` or ``run <approach> <scale>``."""
    command, *rest = argv
    if command == "prep":
        prep(*rest)
    elif command == "run":
        approach, scale = rest
        run(approach, int(scale))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
