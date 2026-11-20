"""Turn the cached source ZIP into the tables the later stages read.

ELT in two layers, both under the data directory:

- raw: the CSV inside the ZIP, every column as text exactly as loaded, saved as
  ``raw/<zip stem>.parquet``;
- staged: ``staged/observations.parquet`` (one row per series and date, with typed
  dates and exact decimal values), ``staged/series.json`` (one ``SeriesMetadata`` per
  series) and ``staged/data_quality.json`` (the ``DataQualityIssue`` findings).

Every column name, code and rule comes from the configuration.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
from pydantic import TypeAdapter

from policy_rate_monitor.core.errors import MissingInputError, UnexpectedFormatError
from policy_rate_monitor.core.models import (
    DataQualityIssue,
    DataQualitySeverity,
    SeriesMetadata,
)
from policy_rate_monitor.fetch import cached_zip

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from policy_rate_monitor.config.models import (
        ColumnsSection,
        DatasetConfig,
        RuleSpec,
    )

__all__ = [
    "TransformSummary",
    "load_data_quality",
    "load_observations",
    "load_series",
    "transform",
]

logger = logging.getLogger(__name__)

IMPLEMENTED_RULES = (
    "unexpected_column",
    "unparseable_date",
    "non_numeric_value",
    "status_value_mismatch",
    "exact_duplicate",
    "conflicting_duplicate",
    "unexpected_value",
)

_SERIES_LIST = TypeAdapter(list[SeriesMetadata])
_ISSUE_LIST = TypeAdapter(list[DataQualityIssue])


@dataclass(frozen=True)
class TransformSummary:
    """What one transform run produced, for the CLI to print.

    Attributes:
        rows_read: Rows in the source CSV.
        observations: Rows written to ``observations.parquet``.
        series: Series written to ``series.json``.
        issues: Findings written to ``data_quality.json``.
    """

    rows_read: int
    observations: int
    series: int
    issues: int


def transform(config: DatasetConfig, data_dir: Path) -> TransformSummary:
    """Builds the raw and staged layers from the cached ZIP.

    Args:
        config: The dataset configuration.
        data_dir: The data directory holding ``raw/<file_name>``.

    Returns:
        Row, series and issue counts of the run.

    Raises:
        MissingInputError: The cached ZIP is absent.
        UnexpectedFormatError: A required column is missing, or a series attribute
            that must be a whole number is not.
    """
    zip_path = cached_zip(config.source, data_dir)
    raw = _read_csv(zip_path, config.columns)
    raw.to_parquet(zip_path.with_suffix(".parquet"), index=False)

    rules = {rule.name: rule for rule in config.rules if rule.enabled}
    for name in rules:
        if name not in IMPLEMENTED_RULES:
            logger.info("data-quality rule %s is not implemented yet", name)

    issues: list[DataQualityIssue] = []
    data = _rename_columns(raw, config, rules, issues)
    data = _select(data, config)
    keys = _series_keys(data, config)
    observations = _stage_observations(data, keys, config, rules, issues)
    series = _series_metadata(data, keys, observations, config)

    staged = data_dir / "staged"
    staged.mkdir(parents=True, exist_ok=True)
    observations.to_parquet(staged / "observations.parquet", index=False)
    (staged / "series.json").write_bytes(_SERIES_LIST.dump_json(series, indent=2))
    (staged / "data_quality.json").write_bytes(_ISSUE_LIST.dump_json(issues, indent=2))

    summary = TransformSummary(
        rows_read=len(raw),
        observations=len(observations),
        series=len(series),
        issues=len(issues),
    )
    logger.info(
        "read %d rows; kept %d observations of %d series; %d data-quality findings",
        summary.rows_read,
        summary.observations,
        summary.series,
        summary.issues,
    )
    return summary


def load_observations(data_dir: Path) -> pd.DataFrame:
    """Reads ``staged/observations.parquet``, keeping its Arrow types.

    Raises:
        MissingInputError: The file is absent.
    """
    path = _staged_file(data_dir, "observations.parquet")
    return pd.read_parquet(path, dtype_backend="pyarrow")


def load_series(data_dir: Path) -> list[SeriesMetadata]:
    """Reads ``staged/series.json``.

    Raises:
        MissingInputError: The file is absent.
    """
    path = _staged_file(data_dir, "series.json")
    return _SERIES_LIST.validate_json(path.read_bytes())


def load_data_quality(data_dir: Path) -> list[DataQualityIssue]:
    """Reads ``staged/data_quality.json``.

    Raises:
        MissingInputError: The file is absent.
    """
    path = _staged_file(data_dir, "data_quality.json")
    return _ISSUE_LIST.validate_json(path.read_bytes())


def _staged_file(data_dir: Path, name: str) -> Path:
    path = data_dir / "staged" / name
    if not path.is_file():
        msg = f"{path} does not exist: run `bis-prates transform` first"
        raise MissingInputError(msg)
    return path


# --------------------------------------------------------------------------------------
# Raw layer
# --------------------------------------------------------------------------------------


def _read_csv(zip_path: Path, columns: ColumnsSection) -> pd.DataFrame:
    if not zip_path.is_file():
        msg = f"{zip_path} does not exist: run `bis-prates fetch` first"
        raise MissingInputError(msg)
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(csv_names) != 1:
            msg = f"{zip_path} should hold exactly one CSV file, found {len(csv_names)}"
            raise UnexpectedFormatError(msg)
        with archive.open(csv_names[0]) as csv_file:
            return pd.read_csv(
                csv_file,
                sep=columns.delimiter,
                encoding=columns.encoding,
                dtype=str,
                keep_default_na=False,
                engine="pyarrow",
            )


# --------------------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------------------


def _rename_columns(
    raw: pd.DataFrame,
    config: DatasetConfig,
    rules: dict[str, RuleSpec],
    issues: list[DataQualityIssue],
) -> pd.DataFrame:
    """Names each column by its concept id and checks them against the config."""
    separator = config.columns.header_label_separator
    names: list[str] = []
    for header in raw.columns:
        concept_id = str(header)
        if separator is not None:
            concept_id = concept_id.split(separator, 1)[0]
        names.append(concept_id.strip())
    data = raw.copy()
    data.columns = names

    missing = [name for name in config.required_columns() if name not in names]
    if missing:
        msg = f"the source file has no column {', '.join(missing)}"
        raise UnexpectedFormatError(msg)
    for name in config.columns.optional:
        if name not in names:
            logger.warning("optional column %s is absent from the source file", name)

    unexpected = [name for name in names if name not in config.known_columns()]
    if unexpected and "unexpected_column" in rules:
        issues.append(
            DataQualityIssue(
                rule="unexpected_column",
                severity=rules["unexpected_column"].severity,
                detail=f"columns not in the configuration, ignored: "
                f"{', '.join(unexpected)}",
                count=len(unexpected),
            )
        )
    return data


def _select(data: pd.DataFrame, config: DatasetConfig) -> pd.DataFrame:
    """Keeps the rows of the configured selection, e.g. the daily series."""
    keep = pd.Series(True, index=data.index)
    for concept_id, code in config.structure.selection.items():
        keep &= _code(data[concept_id], config.columns) == code
    return data[keep].reset_index(drop=True)


def _series_keys(data: pd.DataFrame, config: DatasetConfig) -> pd.Series[str]:
    """Joins the dimension codes of each row with "." in key order, e.g. "D.US"."""
    codes = [_code(data[d.id], config.columns) for d in config.structure.dimensions]
    keys = codes[0]
    for more in codes[1:]:
        keys = keys + "." + more
    return keys


def _code(values: pd.Series[str], columns: ColumnsSection) -> pd.Series[str]:
    """Returns the code part of coded values ("US: United States" -> "US")."""
    if columns.value_label_separator is None:
        return values
    return values.str.partition(columns.value_label_separator)[0]


def _stage_observations(
    data: pd.DataFrame,
    keys: pd.Series[str],
    config: DatasetConfig,
    rules: dict[str, RuleSpec],
    issues: list[DataQualityIssue],
) -> pd.DataFrame:
    structure = config.structure
    columns = config.columns
    measure = structure.measure

    staged = pd.DataFrame({"series_key": keys})
    for dimension in structure.dimensions:
        staged[dimension.column] = _code(data[dimension.id], columns)
    dates = pd.to_datetime(
        data[structure.time_dimension.id],
        format=structure.time_dimension.date_format,
        errors="coerce",
    )
    staged["date"] = dates.astype("date32[pyarrow]")
    staged["value"] = _values(data, config)
    for attribute in structure.observation_attributes:
        if attribute.keep_as is not None and attribute.id in data.columns:
            attribute_values = data[attribute.id]
            if attribute.coded:
                attribute_values = _code(attribute_values, columns)
            staged[attribute.keep_as] = attribute_values

    has_value = staged["value"].notna()
    invalid_status = _invalid_status(data, config)
    staged["is_valid"] = has_value & ~invalid_status

    no_date = staged["date"].isna()
    is_missing = data[measure.id].isin(["", *columns.missing_values])
    _add_issues(
        issues,
        rules.get("unparseable_date"),
        keys[no_date],
        "observations whose date is not in the configured format; dropped",
    )
    _add_issues(
        issues,
        rules.get("non_numeric_value"),
        keys[~is_missing & ~has_value],
        f"values that are not numbers with at most {measure.scale} decimals and "
        f"{measure.precision} digits; kept as invalid with no value",
    )
    _add_issues(
        issues,
        rules.get("status_value_mismatch"),
        keys[invalid_status & has_value],
        "observations with an invalid status but a value; kept as invalid",
    )
    _add_issues(
        issues,
        rules.get("status_value_mismatch"),
        keys[~invalid_status & ~has_value],
        "observations with a valid status but no value; kept as invalid",
    )
    _check_expected_values(data, keys, config, rules.get("unexpected_value"), issues)

    exact_copy = ~no_date & data.duplicated()
    _add_issues(
        issues,
        rules.get("exact_duplicate"),
        keys[exact_copy],
        "rows repeated exactly; the copies were dropped",
    )
    staged = staged[~no_date & ~exact_copy]
    staged = _drop_conflicting(staged, rules.get("conflicting_duplicate"), issues)
    return staged.sort_values(["series_key", "date"], kind="stable").reset_index(
        drop=True
    )


def _values(data: pd.DataFrame, config: DatasetConfig) -> pd.Series:
    """Parses the measure into exact decimals times 10**unit multiplier.

    A missing value, or one that is not a number fitting the configured precision
    and scale, becomes null.

    Plain numbers that surely fit (a sign, at most ``precision - scale`` digits, a
    point and at most ``scale`` digits) with no unit multiplier are cast by Arrow
    in one go. Every other value goes through ``_to_decimal`` one by one, so the
    result is the same as parsing every value with ``Decimal``.
    """
    measure = config.structure.measure
    text = data[measure.id]
    if measure.unit_multiplier is None:
        multipliers = pd.Series(0, index=data.index)
    else:
        codes = _code(data[measure.unit_multiplier], config.columns)
        unique = [str(code) for code in codes.unique()]
        numbers = _whole_numbers(unique, measure.unit_multiplier)
        multipliers = codes.map(dict(zip(unique, numbers, strict=True)))

    whole_digits = measure.precision - measure.scale
    # [0-9], not \d: \d also matches digits of other scripts, which Arrow rejects.
    plain = "[+-]?"
    plain += f"[0-9]{{1,{whole_digits}}}" if whole_digits else "0"
    if measure.scale:
        plain += rf"(\.[0-9]{{1,{measure.scale}}})?"
    fast = text.str.fullmatch(plain, na=False) & (multipliers == 0)
    value_type = pd.ArrowDtype(pa.decimal128(measure.precision, measure.scale))
    values = text.where(fast).astype(value_type)

    slow = ~fast & ~text.isin(["", *config.columns.missing_values])
    values[slow] = [
        _to_decimal(value, multiplier, measure.precision, measure.scale)
        for value, multiplier in zip(text[slow], multipliers[slow], strict=True)
    ]
    return values


def _invalid_status(data: pd.DataFrame, config: DatasetConfig) -> pd.Series[bool]:
    """Marks the rows whose status is one of its attribute's invalid codes."""
    invalid = pd.Series(False, index=data.index)
    for attribute in config.structure.observation_attributes:
        if attribute.invalid_codes:
            codes = _code(data[attribute.id], config.columns)
            invalid |= codes.isin(attribute.invalid_codes)
    return invalid


def _drop_conflicting(
    staged: pd.DataFrame, rule: RuleSpec | None, issues: list[DataQualityIssue]
) -> pd.DataFrame:
    """Keeps one row per series and date: the last one in the file, or the first."""
    keep = "last"
    if rule is not None and rule.params.get("keep") == "first":
        keep = "first"
    conflicting = staged.duplicated(["series_key", "date"], keep=keep)
    _add_issues(
        issues,
        rule,
        staged.loc[conflicting, "series_key"],
        f"dates with more than one different observation; kept the {keep} one",
    )
    return staged[~conflicting]


def _whole_numbers(codes: Iterable[str], concept_id: str) -> list[int]:
    numbers: list[int] = []
    for code in codes:
        try:
            numbers.append(int(code))
        except ValueError:
            msg = f"{concept_id} should be a whole number, got {code!r}"
            raise UnexpectedFormatError(msg) from None
    return numbers


def _to_decimal(
    text: str, multiplier: int, precision: int, scale: int
) -> Decimal | None:
    """Returns the exact value times 10**multiplier, or None if it is not a number
    that fits the configured precision and scale."""
    try:
        value = Decimal(text).scaleb(multiplier)
        rounded = value.quantize(Decimal(1).scaleb(-scale))
    except InvalidOperation:
        return None
    if not value.is_finite() or rounded != value:
        return None
    if len(rounded.as_tuple().digits) > precision:
        return None
    return value


def _check_expected_values(
    data: pd.DataFrame,
    keys: pd.Series[str],
    config: DatasetConfig,
    rule: RuleSpec | None,
    issues: list[DataQualityIssue],
) -> None:
    """Counts the rows whose code differs from the one expected for its column."""
    if rule is None:
        return
    expected = rule.params.get("expected")
    if not isinstance(expected, dict):
        logger.warning("rule unexpected_value needs params.expected; skipped")
        return
    uncoded = {
        concept.id
        for concept in [
            *config.structure.observation_attributes,
            *config.structure.series_attributes,
        ]
        if not concept.coded
    }
    for concept_id, code in expected.items():
        if concept_id not in data.columns:
            continue
        values = data[concept_id]
        if concept_id not in uncoded:
            values = _code(values, config.columns)
        _add_issues(
            issues,
            rule,
            keys[values != str(code)],
            f"observations whose {concept_id} is not {code!r}",
        )


def _add_issues(
    issues: list[DataQualityIssue],
    rule: RuleSpec | None,
    affected: pd.Series[str],
    detail: str,
) -> None:
    """Adds one issue per series, with the number of affected observations."""
    if rule is None:
        return
    counts = affected.value_counts().sort_index()
    for series_key, count in counts.items():
        issues.append(
            DataQualityIssue(
                rule=rule.name,
                severity=DataQualitySeverity(rule.severity),
                series_key=str(series_key),
                detail=f"{count} {detail}",
                count=int(count),
            )
        )


def _series_metadata(
    data: pd.DataFrame,
    keys: pd.Series[str],
    observations: pd.DataFrame,
    config: DatasetConfig,
) -> list[SeriesMetadata]:
    """Builds one SeriesMetadata per kept series, from its last row in the file."""
    kept = set(observations["series_key"])
    last_rows = keys.drop_duplicates(keep="last").sort_values()
    records = data.loc[last_rows.index].to_dict("records")
    series: list[SeriesMetadata] = []
    for series_key, record in zip(last_rows, records, strict=True):
        if series_key in kept:
            row = {str(name): str(value) for name, value in record.items()}
            series.append(_one_series(series_key, row, config))
    return series


def _one_series(
    series_key: str, row: dict[str, str], config: DatasetConfig
) -> SeriesMetadata:
    bound = config.structure.series_metadata
    separator = config.columns.value_label_separator
    coded = {c.id for c in config.structure.series_attributes if c.coded}

    def cell(concept_id: str | None) -> str:
        if concept_id is None:
            return ""
        value = row.get(concept_id, "").strip()
        if concept_id in coded:
            value, _ = _split(value, separator)
        return value

    ref_area, ref_area_name = _split(row[bound.ref_area], separator)
    frequency, _ = _split(row[bound.frequency], separator)
    bound_ids = {bound.title, bound.unit, bound.decimals}
    values = {c.id: cell(c.id) for c in config.structure.series_attributes}
    return SeriesMetadata(
        series_key=series_key,
        ref_area=ref_area,
        ref_area_name=ref_area_name or ref_area,
        frequency=frequency,
        title=cell(bound.title) or series_key,
        unit=_unit(row, bound.unit, separator, coded=bound.unit in coded),
        decimals=_decimals(cell(bound.decimals), bound.decimals),
        attributes={
            key: value
            for key, value in values.items()
            if key not in bound_ids and value
        },
    )


def _unit(
    row: dict[str, str], concept_id: str | None, separator: str | None, *, coded: bool
) -> str | None:
    """The unit's label for a coded unit ("368: Per cent per year"), else its text."""
    unit = row.get(concept_id or "", "").strip()
    if coded:
        code, label = _split(unit, separator)
        unit = label or code
    return unit or None


def _decimals(text: str, concept_id: str | None) -> int | None:
    if not text:
        return None
    return _whole_numbers([text], concept_id or "")[0]


def _split(value: str, separator: str | None) -> tuple[str, str]:
    """Splits a coded value into code and label ("US: United States")."""
    if separator is None:
        return value.strip(), ""
    code, _, label = value.partition(separator)
    return code.strip(), label.strip()
