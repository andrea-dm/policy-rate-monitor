"""The steps the command line and the HTTP API share.

Both adapters read the staged data once with ``load_staged``, then call the
functions here, so ``bis-prates report`` and the API give the same numbers. The
adapters keep only their own concerns: the CLI its arguments, printing and exit
codes, the API its routes and HTTP statuses.

See also: [Architecture](architecture.md).
"""

from __future__ import annotations

from dataclasses import dataclass

# pydantic resolves the annotations at runtime, so ``date`` is a runtime import.
from datetime import date
from typing import TYPE_CHECKING

from pandas import ArrowDtype
from pydantic import BaseModel, ConfigDict

from policy_rate_monitor.chart.lines import valid_rows
from policy_rate_monitor.core.models import Rate, SeriesMetadata
from policy_rate_monitor.report import Report, ReportArgs
from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pandas import DataFrame

    from policy_rate_monitor.config import DatasetConfig
    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        DataSnapshot,
        SourceRelease,
    )
    from policy_rate_monitor.providers import DataProvider

__all__ = [
    "Observation",
    "RefArea",
    "StagedData",
    "TimeSeries",
    "load_staged",
    "ref_areas",
    "summary",
    "timeseries",
    "write_report",
]


@dataclass(frozen=True)
class StagedData:
    """Everything a summary, a time series or a report is computed from.

    The API's threads share one, so the series and the findings are tuples.

    Attributes:
        observations: The staged observations, values as Arrow decimals.
        series: Metadata of every staged series.
        issues: The data-quality findings of the transform.
        release: Where and when the source file was retrieved; ``None`` if unknown.
    """

    observations: DataFrame
    series: tuple[SeriesMetadata, ...]
    issues: tuple[DataQualityIssue, ...]
    release: SourceRelease | None


class RefArea(BaseModel):
    """A country or economic area the data has a series for.

    Attributes:
        ref_area: The code used in the data, e.g. ``XM``.
        ref_area_name: Its name, e.g. ``Euro area``.
        aliases: Other codes that may be requested for it, e.g. ``EA``.
    """

    model_config = ConfigDict(frozen=True)

    ref_area: str
    ref_area_name: str
    aliases: tuple[str, ...] = ()


class Observation(BaseModel):
    """One valid observation of a series.

    Attributes:
        date: The observation date.
        value: The rate in per cent, exact.
    """

    model_config = ConfigDict(frozen=True)

    date: date
    value: Rate


class TimeSeries(BaseModel):
    """The valid observations of one country or economic area, oldest first.

    Attributes:
        requested_ref_area: The code as requested, without the spaces.
        series: The series' metadata.
        observations: The valid observations.
    """

    model_config = ConfigDict(frozen=True)

    requested_ref_area: str
    series: SeriesMetadata
    observations: tuple[Observation, ...]


def load_staged(provider: DataProvider) -> StagedData:
    """Reads the staged data through the provider.

    Args:
        provider: Where the data comes from.

    Returns:
        The observations, the series, the findings and the release.

    Raises:
        MissingInputError: Nothing is staged yet.
    """
    # Arrow types kept, so values stay Decimal. The stubs leave a parameter of
    # to_pandas untyped.
    table = provider.observations()
    observations = table.to_pandas(types_mapper=ArrowDtype)  # pyright: ignore[reportUnknownMemberType]
    return StagedData(
        observations=observations,
        series=tuple(provider.series_metadata(key) for key in provider.list_series()),
        issues=tuple(provider.data_quality_issues()),
        release=provider.source_release(),
    )


def summary(
    config: DatasetConfig, data: StagedData, countries: Sequence[str]
) -> list[DataSnapshot]:
    """Computes the snapshot of each requested country or economic area.

    Args:
        config: The dataset configuration (the snapshot settings and the aliases).
        data: The staged data.
        countries: The codes as the user typed them.

    Returns:
        One snapshot per distinct requested code, in request order.
    """
    return compute_snapshots(
        SnapshotArgs(
            observations=data.observations,
            series=data.series,
            requested=countries,
            settings=config.snapshot,
            ref_area_aliases=config.ref_area_aliases,
        )
    )


def ref_areas(config: DatasetConfig, data: StagedData) -> list[RefArea]:
    """Lists the countries and economic areas the data has a series for, by code.

    Args:
        config: The dataset configuration (the aliases).
        data: The staged data.

    Returns:
        One entry per series, with the aliases that stand for it.
    """
    return [
        RefArea(
            ref_area=item.ref_area,
            ref_area_name=item.ref_area_name,
            aliases=tuple(
                sorted(
                    alias
                    for alias, code in config.ref_area_aliases.items()
                    if code == item.ref_area
                )
            ),
        )
        for item in sorted(data.series, key=lambda item: item.ref_area)
    ]


def timeseries(
    config: DatasetConfig, data: StagedData, country: str, start: date | None = None
) -> TimeSeries | None:
    """Returns the valid observations of one country or economic area, from *start* on.

    Args:
        config: The dataset configuration (the aliases).
        data: The staged data.
        country: The code as the user typed it; matched like a summary's.
        start: The first date to keep; ``None`` for the whole history.

    Returns:
        The time series, or ``None`` when the data has no series for the code.
    """
    given = country.strip()
    code = given.upper()
    ref_area = config.ref_area_aliases.get(code, code)
    found = [item for item in data.series if item.ref_area == ref_area]
    if not found:
        return None
    series = found[0]
    rows = valid_rows(data.observations, series.series_key, start)
    return TimeSeries(
        requested_ref_area=given,
        series=series,
        observations=tuple(
            Observation(date=day, value=value)
            for day, value in zip(rows.date, rows.value, strict=True)
        ),
    )


def write_report(
    config: DatasetConfig,
    data: StagedData,
    snapshots: Sequence[DataSnapshot],
    out_dir: Path,
    start: date | None = None,
) -> list[Path]:
    """Writes the summary files, the chart and the Markdown and HTML reports.

    Args:
        config: The dataset configuration.
        data: The staged data.
        snapshots: One snapshot per requested country or area, from ``summary``.
        out_dir: The output folder, created if needed.
        start: First date on the chart; ``None`` for the whole history.

    Returns:
        The paths of the files written.
    """
    return Report(config).export(
        ReportArgs(
            snapshots=snapshots,
            observations=data.observations,
            series=data.series,
            issues=data.issues,
            release=data.release,
            out_dir=out_dir,
            start=start,
        )
    )
