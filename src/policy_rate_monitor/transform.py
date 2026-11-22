"""Turn the cached source ZIP into the tables the later stages read.

ELT in two layers, both under the data directory:

- raw: the CSV inside the ZIP, every column as text exactly as loaded, saved as
  ``raw/<zip stem>.parquet``;
- staged: ``staged/observations.parquet`` (one row per series and date, with typed
  dates and exact decimal values), ``staged/series.json`` (one ``SeriesMetadata`` per
  series), ``staged/data_quality.json`` (the ``DataQualityIssue`` findings) and
  ``staged/release.json`` (the ``SourceRelease`` of the ZIP they were built from).

The SDMX rules also check the data against the SDMX reference, asked once per run
through a ``reference.ReferenceSource``; when it cannot answer, each of them reports
"not evaluated" as a finding.

Every column name, code and rule comes from the configuration. All the files of a run
are written with ``core.files.write_files``, ``release.json`` last: each file is
replaced whole, and a run that fails while writing leaves the files of the last run.
A reader in another process can still, for a moment, pair files of two runs
(decision 0017).

See also: [How it works](how-it-works.md), sections 3 and 4, [Data
model](data-model.md), [Data quality](data-quality.md), [decision
0011](adr/0011-data-quality-rules.md), [decision
0012](adr/0012-elt-raw-and-staged-layers.md) and [the transform
workflow](advanced/operations/workflows.md#bis-prates-transform).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from decimal import Decimal, DecimalException, Inexact, Rounded, localcontext
from typing import TYPE_CHECKING, Literal

from pandas import ArrowDtype, DataFrame, Series, read_csv, read_parquet, to_datetime
from pandas.errors import EmptyDataError, ParserError
from pyarrow import decimal128
from pydantic import TypeAdapter, ValidationError

from policy_rate_monitor.config.models import SDMX_RULES
from policy_rate_monitor.core.errors import (
    MissingInputError,
    ReferenceUnavailableError,
    UnexpectedFormatError,
)
from policy_rate_monitor.core.files import write_files
from policy_rate_monitor.core.models import (
    DataQualityIssue,
    DataQualitySeverity,
    SeriesMetadata,
    SourceRelease,
    TransformSummary,
)
from policy_rate_monitor.fetch import cached_zip, read_release, read_zip_csv
from policy_rate_monitor.reference import NO_REFERENCE

if TYPE_CHECKING:
    from pathlib import Path

    from policy_rate_monitor.config.models import (
        ArtefactRef,
        ColumnsSection,
        DatasetConfig,
        RuleSpec,
    )
    from policy_rate_monitor.reference import DataflowStructure, ReferenceSource

__all__ = [
    "TransformSummary",
    "load_data_quality",
    "load_observations",
    "load_release",
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
    *SDMX_RULES,
)
"""The data-quality rules that have code. A configured rule not listed here is logged
and skipped."""

_SERIES_LIST = TypeAdapter(list[SeriesMetadata])
_ISSUE_LIST = TypeAdapter(list[DataQualityIssue])

# ASCII only: [0-9], not \d, which also matches the digits of other scripts.
_NUMBER = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?")
_WHOLE_NUMBER = re.compile(r"[+-]?[0-9]+")


def transform(
    config: DatasetConfig, data_dir: Path, reference: ReferenceSource = NO_REFERENCE
) -> TransformSummary:
    """Builds the raw and staged layers from the cached ZIP.

    Args:
        config: The dataset configuration.
        data_dir: The data directory holding ``raw/<file_name>``.
        reference: Where the SDMX rules read the reference. It is asked only when
            one of them is enabled, after the file is read and its selection has
            rows. With the default, an enabled SDMX rule reports "not evaluated".

    Returns:
        Row, series and issue counts of the run.

    Raises:
        MissingInputError: The cached ZIP is absent.
        UnexpectedFormatError: The ZIP is corrupt, its CSV cannot be parsed or
            decoded or has no row of the configured selection, a required column is
            missing, or a series row does not make valid series metadata.
        OSError: A file cannot be written; the staged files of the previous run are
            left as they were. Or a file cannot be renamed into place; the files
            renamed before it are new, and the rest stay as they were, each beside
            its temporary file.
    """
    zip_path = cached_zip(config.source, data_dir)
    if not zip_path.is_file():
        msg = f"{zip_path} does not exist"
        raise MissingInputError(msg, step="fetch")
    content = zip_path.read_bytes()
    release = _release_of(content, config, data_dir)
    raw = _read_csv(content, zip_path, config.columns)

    rules = {rule.name: rule for rule in config.rules if rule.enabled}
    for name in rules:
        if name not in IMPLEMENTED_RULES:
            logger.info("data-quality rule %s is not implemented yet", name)

    issues: list[DataQualityIssue] = []
    data = _rename_columns(raw, config, rules, issues)
    data = _select(data, config)
    if data.empty:
        selection = ", ".join(
            f"{k} = {v}" for k, v in config.structure.selection.items()
        )
        msg = f"{zip_path} has no rows with {selection}"
        raise UnexpectedFormatError(msg)
    keys = _series_keys(data, config)
    observations = _stage_observations(data, keys, config, rules, issues)
    series = _series_metadata(data, keys, observations, config)
    _check_reference(data, keys, config, rules, reference, issues)

    staged = data_dir / "staged"
    files = {
        zip_path.with_suffix(".parquet"): _parquet(raw),
        staged / "observations.parquet": _parquet(observations),
        staged / "series.json": _SERIES_LIST.dump_json(series, indent=2),
        staged / "data_quality.json": _ISSUE_LIST.dump_json(issues, indent=2),
    }
    if release is None:
        # Removed before the writes: staged files with no release record have an
        # unknown provenance, never a wrong one.
        (staged / "release.json").unlink(missing_ok=True)
    else:
        files[staged / "release.json"] = release.model_dump_json(indent=2).encode()
    write_files(files)

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


def load_observations(data_dir: Path) -> DataFrame:
    """Reads ``staged/observations.parquet``, keeping its Arrow types.

    Args:
        data_dir: The data directory holding ``staged/``.

    Returns:
        The staged observations, values as Arrow decimals.

    Raises:
        MissingInputError: The file is absent (``step`` is ``"transform"``).
    """
    path = _staged_file(data_dir, "observations.parquet")
    # Read once: read_parquet opens a path twice, and a rename in between would pair
    # the footer of one file with the pages of another.
    return read_parquet(io.BytesIO(path.read_bytes()), dtype_backend="pyarrow")


def load_series(data_dir: Path) -> list[SeriesMetadata]:
    """Reads ``staged/series.json``.

    Args:
        data_dir: The data directory holding ``staged/``.

    Returns:
        The metadata of every staged series.

    Raises:
        MissingInputError: The file is absent (``step`` is ``"transform"``).
    """
    path = _staged_file(data_dir, "series.json")
    return _SERIES_LIST.validate_json(path.read_bytes())


def load_data_quality(data_dir: Path) -> list[DataQualityIssue]:
    """Reads ``staged/data_quality.json``.

    Args:
        data_dir: The data directory holding ``staged/``.

    Returns:
        The data-quality findings of the last transform run.

    Raises:
        MissingInputError: The file is absent (``step`` is ``"transform"``).
    """
    path = _staged_file(data_dir, "data_quality.json")
    return _ISSUE_LIST.validate_json(path.read_bytes())


def load_release(data_dir: Path) -> SourceRelease | None:
    """Reads ``staged/release.json``: the release the staged data was built from.

    Args:
        data_dir: The data directory holding ``staged/``.

    Returns:
        The release; ``None`` when the file is absent, or is not a valid UTF-8 record
        (with a warning in the log).

    Raises:
        OSError: The file exists but cannot be read (permissions, say).
    """
    path = data_dir / "staged" / "release.json"
    if not path.is_file():
        return None
    try:
        return SourceRelease.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, UnicodeDecodeError):
        logger.warning("%s is unreadable; the source release is unknown", path)
        return None


def _staged_file(data_dir: Path, name: str) -> Path:
    path = data_dir / "staged" / name
    if not path.is_file():
        msg = f"{path} does not exist"
        raise MissingInputError(msg, step="transform")
    return path


def _parquet(frame: DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _release_of(
    content: bytes, config: DatasetConfig, data_dir: Path
) -> SourceRelease | None:
    """The release recorded next to the ZIP, if its checksum is that of *content*.

    A ``fetch`` running at the same time may have replaced the record since the ZIP
    was read; the checksum tells. A record with no checksum is not staged either.
    """
    release = read_release(config.source, data_dir)
    if release is None:
        logger.warning(
            "no valid release record next to the ZIP; the release is unknown"
        )
        return None
    if release.checksum_sha256 != hashlib.sha256(content).hexdigest():
        logger.warning("the release record is not that of the ZIP read; not staged")
        return None
    return release


# ──────────────────────────────────────────────────────────────────────────────────── #
# ══════════════════════════════════════════════════════════════════════ Raw layer ═══ #
# ──────────────────────────────────────────────────────────────────────────────────── #


def _read_csv(content: bytes, zip_path: Path, columns: ColumnsSection) -> DataFrame:
    # Read whole before parsing: a CRC error raised inside Arrow's reader threads
    # could abort the process.
    csv_content = read_zip_csv(content, str(zip_path))
    try:
        raw = read_csv(
            io.BytesIO(csv_content),
            sep=columns.delimiter,
            encoding=columns.encoding,
            dtype=str,
            keep_default_na=False,
            engine="pyarrow",
        )
    # A CSV that cannot be parsed raises the first two; one that is not in the
    # configured encoding, UnicodeError.
    except (ParserError, EmptyDataError, UnicodeError) as error:
        msg = f"{zip_path} cannot be read: {error}"
        raise UnexpectedFormatError(msg) from error
    if raw.empty:
        msg = f"{zip_path} holds no rows"
        raise UnexpectedFormatError(msg)
    return raw


# ──────────────────────────────────────────────────────────────────────────────────── #
# ════════════════════════════════════════════════════════════════════════ Staging ═══ #
# ──────────────────────────────────────────────────────────────────────────────────── #


def _rename_columns(
    raw: DataFrame,
    config: DatasetConfig,
    rules: dict[str, RuleSpec],
    issues: list[DataQualityIssue],
) -> DataFrame:
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


def _select(data: DataFrame, config: DatasetConfig) -> DataFrame:
    """Keeps the rows of the configured selection, e.g. the daily series."""
    keep = Series(True, index=data.index)
    for concept_id, code in config.structure.selection.items():
        keep &= _code(data[concept_id], config.columns) == code
    return data[keep].reset_index(drop=True)


def _series_keys(data: DataFrame, config: DatasetConfig) -> Series[str]:
    """Joins the dimension codes of each row with "." in key order, e.g. "D.US"."""
    codes = [_code(data[d.id], config.columns) for d in config.structure.dimensions]
    keys = codes[0]
    for more in codes[1:]:
        keys = keys + "." + more
    return keys


def _code(values: Series[str], columns: ColumnsSection) -> Series[str]:
    """Returns the code part of coded values ("US: United States" -> "US")."""
    if columns.value_label_separator is None:
        return values
    return values.str.partition(columns.value_label_separator)[0]


def _stage_observations(
    data: DataFrame,
    keys: Series[str],
    config: DatasetConfig,
    rules: dict[str, RuleSpec],
    issues: list[DataQualityIssue],
) -> DataFrame:
    structure = config.structure
    columns = config.columns
    measure = structure.measure

    staged = DataFrame({"series_key": keys})
    for dimension in structure.dimensions:
        staged[dimension.column] = _code(data[dimension.id], columns)
    times = data[structure.time_dimension.id]
    # to_datetime also reads digits of other scripts; such a date is not in the format.
    dates = to_datetime(
        times.where(times.str.isascii()),
        format=structure.time_dimension.date_format,
        errors="coerce",
    )
    staged["date"] = dates.astype("date32[pyarrow]")
    multipliers = _unit_multipliers(data, config)
    staged["value"] = _values(data, multipliers, config)
    for attribute in structure.observation_attributes:
        if attribute.keep_as is not None and attribute.id in data.columns:
            attribute_values = data[attribute.id]
            if attribute.coded:
                attribute_values = _code(attribute_values, columns)
            staged[attribute.keep_as] = attribute_values

    has_value = staged.value.notna()
    invalid_status = _invalid_status(data, config)
    staged["is_valid"] = has_value & ~invalid_status

    no_date = staged.date.isna()
    is_missing = data[measure.id].isin(["", *columns.missing_values])
    no_multiplier = multipliers.isna()
    _add_issues(
        issues,
        rules.get("unparseable_date"),
        keys[no_date],
        "observations whose date is not in the configured format; dropped",
    )
    _add_issues(
        issues,
        rules.get("non_numeric_value"),
        keys[~is_missing & ~has_value & ~no_multiplier],
        f"values that are not numbers with at most {measure.scale} decimals and "
        f"{measure.precision} digits; kept as invalid with no value",
    )
    _add_issues(
        issues,
        rules.get("non_numeric_value"),
        keys[~is_missing & no_multiplier],
        f"observations whose {measure.unit_multiplier} is blank or not a whole "
        "number; kept as invalid with no value",
    )
    _add_issues(
        issues,
        rules.get("status_value_mismatch"),
        keys[invalid_status & has_value],
        "observations with an invalid status but a value; kept as invalid",
    )
    # A value that is there but not a number is non_numeric_value's finding alone.
    _add_issues(
        issues,
        rules.get("status_value_mismatch"),
        keys[~invalid_status & is_missing],
        "observations with a valid status but a missing value; kept as invalid",
    )
    _check_expected_values(data, keys, config, rules.get("unexpected_value"), issues)

    # An exact copy repeats the whole source row, every column, not only the series
    # and date; rows that share a series and date but differ are conflicts, below.
    exact_copy = ~no_date & data.duplicated()
    _add_issues(
        issues,
        rules.get("exact_duplicate"),
        keys[exact_copy],
        "rows repeated exactly; the copies were dropped",
    )
    staged = staged[~no_date & ~exact_copy]
    staged = _drop_conflicting(
        staged, config.conflict_keep(), rules.get("conflicting_duplicate"), issues
    )
    return staged.sort_values(["series_key", "date"], kind="stable").reset_index(
        drop=True
    )


def _unit_multipliers(data: DataFrame, config: DatasetConfig) -> Series:
    """The unit multiplier of each row as an int; None where it is blank or not a
    whole number. 0 for every row when no multiplier is configured."""
    concept_id = config.structure.measure.unit_multiplier
    if concept_id is None:
        return Series(0, index=data.index, dtype=object)
    codes = _code(data[concept_id], config.columns)
    numbers = {code: _whole_number(code) for code in codes.unique()}
    # object, not int: pandas would turn the ints into floats next to a None.
    return Series([numbers[code] for code in codes], index=data.index, dtype=object)


def _values(data: DataFrame, multipliers: Series, config: DatasetConfig) -> Series:
    """Parses the measure into exact decimals times 10**unit multiplier.

    A missing value, one with no unit multiplier, or one that is not a number fitting
    the configured precision and scale, becomes null.

    Plain numbers that surely fit (a sign, at most ``precision - scale`` digits, a
    point and at most ``scale`` digits) with a unit multiplier of 0 are cast by Arrow
    in one go. Every other value goes through ``_to_decimal`` one by one, so the
    result is the same as parsing every value with ``_to_decimal``.
    """
    measure = config.structure.measure
    text = data[measure.id]

    whole_digits = measure.precision - measure.scale
    # [0-9], not \d: \d also matches digits of other scripts, which Arrow rejects.
    plain = "[+-]?"
    plain += f"[0-9]{{1,{whole_digits}}}" if whole_digits else "0"
    if measure.scale:
        plain += rf"(\.[0-9]{{1,{measure.scale}}})?"
    fast = text.str.fullmatch(plain, na=False) & (multipliers == 0)
    value_type = ArrowDtype(decimal128(measure.precision, measure.scale))
    values = text.where(fast).astype(value_type)

    slow = ~fast & ~text.isin(["", *config.columns.missing_values])
    slow &= multipliers.notna()
    values[slow] = [
        _to_decimal(value, multiplier, measure.precision, measure.scale)
        for value, multiplier in zip(text[slow], multipliers[slow], strict=True)
    ]
    return values


def _invalid_status(data: DataFrame, config: DatasetConfig) -> Series[bool]:
    """Marks the rows whose status is one of its attribute's invalid codes."""
    invalid = Series(False, index=data.index)
    for attribute in config.structure.observation_attributes:
        if attribute.invalid_codes:
            codes = _code(data[attribute.id], config.columns)
            invalid |= codes.isin(attribute.invalid_codes)
    return invalid


def _drop_conflicting(
    staged: DataFrame,
    keep: Literal["first", "last"],
    rule: RuleSpec | None,
    issues: list[DataQualityIssue],
) -> DataFrame:
    """Keeps one row per series and date: the last one in the file, or the first.

    *rule* is the enabled rule, for the finding; *keep* applies even without it.
    """
    conflicting = staged.duplicated(["series_key", "date"], keep=keep)
    _add_issues(
        issues,
        rule,
        staged.loc[conflicting, "series_key"],
        "rows dropped for dates with more than one different observation;"
        f" kept the {keep} one",
    )
    return staged[~conflicting]


def _whole_number(text: str) -> int | None:
    """The whole number *text* spells in ASCII digits, spaces around it ignored, or
    None ("", "1.5", "1_0", a non-breaking space)."""
    text = text.strip(" ")
    if not _WHOLE_NUMBER.fullmatch(text):
        return None
    return int(text)


def _to_decimal(
    text: str, multiplier: int, precision: int, scale: int
) -> Decimal | None:
    """Returns the exact value times 10**multiplier, or None if it is not a number
    that fits the configured precision and scale.

    Only a plain ASCII number is read (a sign, digits, a point, an exponent; ASCII
    spaces around it ignored): ``Decimal`` alone would also read "1_5", other scripts'
    digits and "Infinity". Rounding is trapped, so a value with more digits than
    ``Decimal`` keeps (28) is not a number rather than a rounded one.
    """
    text = text.strip(" ")
    if not _NUMBER.fullmatch(text):
        return None
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            value = Decimal(text).scaleb(multiplier)
        rounded = value.quantize(Decimal(1).scaleb(-scale))
    except DecimalException:
        return None
    if rounded != value or len(rounded.as_tuple().digits) > precision:
        return None
    return rounded


def _check_expected_values(
    data: DataFrame,
    keys: Series[str],
    config: DatasetConfig,
    rule: RuleSpec | None,
    issues: list[DataQualityIssue],
) -> None:
    """Counts the rows whose code differs from the one expected for its column."""
    if rule is None:
        return
    expected = rule.params.get("expected")
    if not isinstance(expected, dict):  # checked when the configuration is loaded
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
            keys[values != code],
            f"observations whose {concept_id} is not {code!r}",
        )


def _check_reference(
    data: DataFrame,
    keys: Series[str],
    config: DatasetConfig,
    rules: dict[str, RuleSpec],
    reference: ReferenceSource,
    issues: list[DataQualityIssue],
) -> None:
    """Runs the enabled SDMX rules; each asks the reference once."""
    code_list = config.reference.ref_area_code_list if config.reference else None
    not_evaluated: dict[str, list[str]] = {}
    for name in SDMX_RULES:
        rule = rules.get(name)
        # The configuration refuses an SDMX rule without a reference section.
        if rule is None or code_list is None:
            continue
        try:
            if name == "ref_area_not_in_code_list":
                codes = reference.ref_area_codes()
                _check_ref_area_codes(data, keys, config, rule, codes, issues)
            else:
                definition = reference.structure()
                _check_dataflow(config, rule, definition, code_list, issues)
        except ReferenceUnavailableError as error:
            not_evaluated.setdefault(error.reason, []).append(name)
            issues.append(
                DataQualityIssue(
                    rule=name,
                    severity=rule.severity,
                    detail=f"not evaluated: {error.reason}",
                    count=0,
                )
            )
    for reason, names in not_evaluated.items():
        logger.warning(
            "data-quality rules not evaluated: %s: %s", ", ".join(names), reason
        )


def _check_ref_area_codes(
    data: DataFrame,
    keys: Series[str],
    config: DatasetConfig,
    rule: RuleSpec,
    codes: frozenset[str],
    issues: list[DataQualityIssue],
) -> None:
    """Counts the rows whose reference-area code is not in the code list."""
    concept_id = config.structure.series_metadata.ref_area
    ref_areas = _code(data[concept_id], config.columns)
    _add_issues(
        issues,
        rule,
        keys[~ref_areas.isin(codes)],
        f"observations whose {concept_id} code is not in the code list",
    )


def _check_dataflow(
    config: DatasetConfig,
    rule: RuleSpec,
    definition: DataflowStructure,
    code_list: ArtefactRef,
    issues: list[DataQualityIssue],
) -> None:
    """Compares the configured structure with the dataflow definition.

    The time dimension is left out: ``pysdmx`` does not read it from every API.
    """
    structure = config.structure
    time_id = structure.time_dimension.id
    declared: dict[str, tuple[str, bool | None]] = {
        dimension.id: ("dimension", dimension.coded)
        for dimension in structure.dimensions
    }
    declared[structure.measure.id] = ("measure", None)
    for attribute in [
        *structure.observation_attributes,
        *structure.series_attributes,
    ]:
        declared[attribute.id] = ("attribute", attribute.coded)
    defined = {c.id: c for c in definition.components if c.id != time_id}

    differences: list[str] = []
    for concept_id, (role, coded) in declared.items():
        component = defined.get(concept_id)
        if component is None:
            differences.append(f"{concept_id} is not in the definition")
            continue
        if component.role != role:
            differences.append(
                f"{concept_id} is {_a(component.role)} in the definition, "
                f"{_a(role)} in the configuration"
            )
        elif coded is not None and component.coded != coded:
            differences.append(
                f"{concept_id} is {_coded(component.coded)} in the definition, "
                f"{_coded(coded)} in the configuration"
            )
    differences += [
        f"{concept_id} is in the definition, not in the configuration"
        for concept_id in defined
        if concept_id not in declared and concept_id not in config.columns.unmapped
    ]
    ref_area = defined.get(structure.series_metadata.ref_area)
    if (
        ref_area is not None
        and ref_area.code_list is not None
        and ref_area.code_list != code_list
    ):
        differences.append(
            f"{ref_area.id} is coded by {ref_area.code_list} in the definition, "
            f"not by the configured {code_list}"
        )
    if differences:
        plural = "s" if len(differences) > 1 else ""
        issues.append(
            DataQualityIssue(
                rule=rule.name,
                severity=rule.severity,
                detail=f"{len(differences)} difference{plural} from the definition "
                f"{definition.structure}: {'; '.join(differences)}",
                count=len(differences),
            )
        )


def _a(role: str) -> str:
    return f"an {role}" if role[0] in "aeiou" else f"a {role}"


def _coded(coded: bool) -> str:  # noqa: FBT001 - a plain yes or no
    return "coded" if coded else "not coded"


def _add_issues(
    issues: list[DataQualityIssue],
    rule: RuleSpec | None,
    affected: Series[str],
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
    data: DataFrame,
    keys: Series[str],
    observations: DataFrame,
    config: DatasetConfig,
) -> list[SeriesMetadata]:
    """Builds one SeriesMetadata per kept series, from its last row in the file."""
    # Any row would do if the series attributes are constant within a series; that is
    # assumed, not checked, until the series_attribute_not_constant rule is built.
    kept = set(observations.series_key)
    last_rows = keys.drop_duplicates(keep="last").sort_values()
    records = data.loc[last_rows.index].to_dict("records")
    series: list[SeriesMetadata] = []
    for series_key, record in zip(last_rows, records, strict=True):
        if series_key in kept:
            row = {str(name): str(value) for name, value in record.items()}
            try:
                series.append(_one_series(series_key, row, config))
            except ValidationError as error:
                msg = f"series {series_key} has invalid metadata: {error}"
                raise UnexpectedFormatError(msg) from error
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
        decimals=_decimals(cell(bound.decimals), bound.decimals, series_key),
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


def _decimals(text: str, concept_id: str | None, series_key: str) -> int | None:
    """The display precision, or None (with a warning) if it is not a whole number."""
    if concept_id is None:
        return None
    decimals = _whole_number(text)
    if decimals is None or decimals < 0:
        logger.warning(
            "%s of series %s is %r, not a whole number of 0 or more; "
            "its decimals are unknown",
            concept_id,
            series_key,
            text,
        )
        return None
    return decimals


def _split(value: str, separator: str | None) -> tuple[str, str]:
    """Splits a coded value into code and label ("US: United States")."""
    if separator is None:
        return value.strip(), ""
    code, _, label = value.partition(separator)
    return code.strip(), label.strip()
