"""Benchmark: the 12-month 'on or before' snapshot for ALL series, six approaches.

Evidence for the look-back decision (2026-11-17; the standard SQL approach was added
on 2026-11-22; findings in diagnostics/README.md).
Every approach computes, per series: the latest valid observation, the target date
12 calendar months earlier, and the last valid observation on or before it. Outputs
are hashed (``digest``): identical digests at the same scale mean identical answers.

Scaling: the real daily series are replicated ``scale`` times under new codes
(GB_0, GB_1, ...).

Usage, from the repository root (duckdb is benchmark-only, not a project dependency):
    BENCH="uv run --with pyarrow --with duckdb python diagnostics/bench_snapshot.py"
    $BENCH prep                      # writes to a gitignored scratch folder
    $BENCH run <approach> <scale>    # prints one JSON line

Approaches: bisect_py, searchsorted_np, pandas_merge_asof, arrow_join_asof, duckdb_asof,
duckdb_standard
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
from typing import TYPE_CHECKING, cast

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

    from numpy.typing import NDArray

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

# (latest_date, latest_value, target_date, prior_date, prior_value) per series; each
# approach returns its own date and decimal types, which `normalise` makes comparable.
type Snapshot = tuple[object, object, object, object, object]
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
    base = pa.Table.from_arrays(
        [
            pc.utf8_slice_codeunits(table["ref_area"], 0, 2),
            dates.cast(pa.date32()),
            table["value"].cast(RATE),
        ],
        names=["ref_area", "date", "value"],
    )
    BASE.parent.mkdir(exist_ok=True)
    pq.write_table(base, BASE)  # pyright: ignore[reportUnknownMemberType]
    series = len(pc.unique(base["ref_area"]))
    print(f"base rows={base.num_rows:,} series={series}")


def load(scale: int) -> pa.Table:
    """Return the base table replicated ``scale`` times, sorted by (series, date)."""
    base = pq.read_table(BASE)  # pyright: ignore[reportUnknownMemberType]
    areas = base["ref_area"].to_pylist()
    parts = [
        base.set_column(0, "ref_area", pa.array([f"{area}_{k}" for area in areas]))
        for k in range(scale)
    ]
    table = pa.concat_tables(parts).combine_chunks()
    return table.sort_by([("ref_area", "ascending"), ("date", "ascending")])  # pyright: ignore[reportUnknownMemberType]


def minus_12_months(dates: list[dt.date]) -> list[dt.date]:
    """Subtract 12 calendar months from each date (29 Feb maps to 28 Feb)."""
    return [(pd.Timestamp(d) - pd.DateOffset(months=12)).date() for d in dates]


def group_bounds(table: pa.Table) -> tuple[list[str], NDArray[np.intp]]:
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
        dates = cast("list[dt.date]", series["date"].to_pylist())
        values = series["value"].to_pylist()
        target = minus_12_months([dates[-1]])[0]
        j = bisect.bisect_right(dates, target) - 1
        prior_date, prior_value = (dates[j], values[j]) if j >= 0 else (None, None)
        out[name] = (dates[-1], values[-1], target, prior_date, prior_value)
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
        target_day = int((np.datetime64(targets[i]) - epoch).astype(int))
        start, end = int(bounds[i]), int(bounds[i + 1])
        j = start + int(np.searchsorted(days[start:end], target_day, side="right")) - 1
        prior = (
            (to_date(int(days[j])), values[j].as_py()) if j >= start else (None, None)
        )
        latest_value = values[int(last[i])].as_py()
        out[name] = (latest[i], latest_value, targets[i], prior[0], prior[1])
    return out


def pandas_merge_asof(table: pa.Table) -> Results:
    """Approach C: one ``pd.merge_asof`` over all series (keys as datetime64)."""
    df = table.to_pandas(types_mapper=pd.ArrowDtype)  # pyright: ignore[reportUnknownMemberType]
    latest = df.groupby("ref_area", sort=False).tail(1)
    left = pd.DataFrame(
        {
            "ref_area": latest.ref_area.to_numpy(),
            "latest_date": latest.date.astype("datetime64[ns]").to_numpy(),
            "latest_value": latest.value.to_numpy(),
        }
    )
    left["target"] = left.latest_date - pd.DateOffset(months=12)
    right = pd.DataFrame(
        {
            "ref_area": df.ref_area.to_numpy(),
            "prior_date": df.date.astype("datetime64[ns]").to_numpy(),
            "prior_value": df.value,
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
        row["ref_area"]: (
            row["latest_date"].date(),
            row["latest_value"],
            row["target"].date(),
            None if pd.isna(row["prior_date"]) else row["prior_date"].date(),
            None if pd.isna(row["prior_value"]) else row["prior_value"],
        )
        for row in merged.to_dict("records")
    }


def arrow_join_asof(table: pa.Table) -> Results:
    """Approach D: ``pa.Table.join_asof`` with the workarounds it needs.

    pyarrow 26 matches nothing when any key is negative (date32 before 1970), rejects
    decimal value columns, and requires a tolerance. Keys are therefore shifted to be
    non-negative, a row index is joined instead of the values, and the tolerance spans
    the whole history.
    """
    _, bounds = group_bounds(table)
    latest = table.take(pa.array(bounds[1:] - 1, pa.int64()))
    targets = minus_12_months(cast("list[dt.date]", latest["date"].to_pylist()))
    days = table["date"].combine_chunks().cast(pa.int32())
    offset = min(pc.min(days).as_py(), 0)
    target_days = pa.array(targets, pa.date32()).cast(pa.int32())
    left = pa.Table.from_arrays(
        [latest["ref_area"], pc.subtract(target_days, offset)],
        names=["ref_area", "target"],
    )
    right = pa.Table.from_arrays(
        [
            table["ref_area"],
            pc.subtract(days, offset),
            pa.array(np.arange(table.num_rows), pa.int64()),
        ],
        names=["ref_area", "target", "row"],
    )
    history = pc.max(table["date"]).as_py() - pc.min(table["date"]).as_py()
    joined: pa.Table = left.sort_by("target").join_asof(  # pyright: ignore[reportUnknownMemberType]
        right.sort_by("target"),  # pyright: ignore[reportUnknownMemberType]
        on="target",
        by="ref_area",
        tolerance=-history.days - 1,
    )
    prior_dates = pc.take(table["date"], joined["row"]).to_pylist()
    prior_values = pc.take(table["value"], joined["row"]).to_pylist()
    joined_targets = pc.add(joined["target"], offset).cast(pa.int32()).cast(pa.date32())
    by_area = {row["ref_area"]: row for row in latest.to_pylist()}
    return {
        area: (by_area[area]["date"], by_area[area]["value"], target, p_date, p_value)
        for area, target, p_date, p_value in zip(
            cast("list[str]", joined["ref_area"].to_pylist()),
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


# Standard SQL, no ASOF JOIN: each series has its own target date, so two aggregates
# find the latest and the prior dates, and two joins fetch their values.
STANDARD_QUERY = """
    WITH latest AS (
        SELECT ref_area, MAX(date) AS latest_date
        FROM obs
        GROUP BY ref_area
    ),
    l AS (
        SELECT ref_area, latest_date,
               CAST(latest_date - INTERVAL '12' MONTH AS DATE) AS target
        FROM latest
    ),
    prior AS (
        SELECT o.ref_area, MAX(o.date) AS prior_date
        FROM obs o JOIN l ON o.ref_area = l.ref_area
        WHERE o.date <= l.target
        GROUP BY o.ref_area
    )
    SELECT l.ref_area, l.latest_date, lv.value, l.target, p.prior_date, pv.value
    FROM l
    JOIN obs lv ON lv.ref_area = l.ref_area AND lv.date = l.latest_date
    LEFT JOIN prior p ON p.ref_area = l.ref_area
    LEFT JOIN obs pv ON pv.ref_area = l.ref_area AND pv.date = p.prior_date
"""


def duckdb_standard(table: pa.Table) -> Results:
    """Approach F: DuckDB with the standard SQL query above (no ASOF JOIN)."""
    con = duckdb.connect()
    con.register("obs", table)
    rows = con.execute(STANDARD_QUERY).fetchall()
    return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in rows}


APPROACHES: dict[str, Callable[[pa.Table], Results]] = {
    f.__name__: f
    for f in (
        bisect_py,
        searchsorted_np,
        pandas_merge_asof,
        arrow_join_asof,
        duckdb_asof,
        duckdb_standard,
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
