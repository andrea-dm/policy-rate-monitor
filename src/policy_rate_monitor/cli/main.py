"""The ``bis-prates`` command: fetch, transform and report on the policy-rate data.

Each subcommand runs one step of the pipeline; ``config validate`` and ``config
schema`` check the configuration and print its format. Errors raised on purpose
end the command with a one-line message and the exit code of ``exit_codes``;
``--debug`` adds the traceback. Any other error is a bug: it is logged with its
traceback and ends the command with ``INTERNAL_ERROR``.

See also: [CLI reference](cli.md).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from pandas import ArrowDtype

from policy_rate_monitor import __version__
from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.cli.exit_codes import ExitCode, exit_code_for
from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.config.schema import render_json_schema
from policy_rate_monitor.core.errors import ConfigError, PolicyRateMonitorError
from policy_rate_monitor.core.models import SnapshotStatus
from policy_rate_monitor.report import Report, ReportArgs
from policy_rate_monitor.settings import (
    CONFIG_VARIABLE,
    DATA_DIR_VARIABLE,
    DEFAULT_CONFIG,
    Origin,
    Setting,
    config_path,
    data_dir,
)
from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots

if TYPE_CHECKING:
    from collections.abc import Mapping

    from policy_rate_monitor.config import DatasetConfig
    from policy_rate_monitor.core.models import DataSnapshot, SeriesMetadata

__all__ = ["main"]

logger = logging.getLogger(__name__)


def main(
    argv: list[str] | None = None, environ: Mapping[str, str] | None = None
) -> int:
    """Runs one ``bis-prates`` command.

    Results go to standard output; warnings, errors and the log (set up here, at
    INFO level) go to standard error.

    Args:
        argv: The arguments after the program name; ``None`` reads ``sys.argv``.
        environ: The environment variables; ``None`` reads ``os.environ``.

    Returns:
        The exit code (see ``ExitCode``).

    Raises:
        SystemExit: Raised by argparse for ``--help`` and ``--version`` (code 0)
            and for invalid arguments (code 2).
    """
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.command(args, os.environ if environ is None else environ)
    except PolicyRateMonitorError as error:
        if args.debug:
            logger.exception("the command failed")
        sys.stderr.write(f"error: {error}\n")
        return exit_code_for(error)
    # The errors raised on purpose are typed; a plain OSError is the file system
    # refusing a read or a write (permissions, a full disk), not a bug.
    except OSError as error:
        if args.debug:
            logger.exception("the command failed")
        sys.stderr.write(f"error: cannot read or write a file: {error}\n")
        return ExitCode.OUTPUT_ERROR
    except Exception as error:
        logger.exception("unexpected error, please report it")
        sys.stderr.write(f"error: {type(error).__name__}: {error}\n")
        return ExitCode.INTERNAL_ERROR


def _parser() -> argparse.ArgumentParser:
    debug = argparse.ArgumentParser(add_help=False)
    debug.add_argument(
        "--debug", action="store_true", help="show the traceback of an error"
    )
    with_config = argparse.ArgumentParser(add_help=False, parents=[debug])
    with_config.add_argument(
        "--config",
        type=Path,
        help=f"the dataset configuration (default: ${CONFIG_VARIABLE}, "
        f"else {DEFAULT_CONFIG})",
    )
    with_data = argparse.ArgumentParser(add_help=False, parents=[with_config])
    with_data.add_argument(
        "--data-dir",
        type=Path,
        help=f"the data directory (default: ${DATA_DIR_VARIABLE}, "
        "else dataset.data_dir in the configuration)",
    )

    parser = argparse.ArgumentParser(
        prog="bis-prates", description="Monitor central-bank policy rates."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(title="commands", required=True)

    fetch_cmd = commands.add_parser(
        "fetch", parents=[with_data], help="download the source ZIP, or use the cache"
    )
    fetch_cmd.set_defaults(command=_fetch)

    transform_cmd = commands.add_parser(
        "transform", parents=[with_data], help="build the staged tables from the ZIP"
    )
    transform_cmd.set_defaults(command=_transform)

    report_cmd = commands.add_parser(
        "report", parents=[with_data], help="write the snapshot report"
    )
    report_cmd.add_argument(
        "--countries",
        required=True,
        help='comma-separated codes of countries or areas, e.g. "US,EA,GB"',
    )
    report_cmd.add_argument(
        "--start",
        type=date.fromisoformat,
        help="first date of the chart, YYYY-MM-DD (default: the whole history)",
    )
    report_cmd.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="the output directory (default: out)",
    )
    report_cmd.set_defaults(command=_report)

    config_cmd = commands.add_parser(
        "config", help="check the configuration, or print its format"
    )
    config_commands = config_cmd.add_subparsers(title="commands", required=True)
    validate_cmd = config_commands.add_parser(
        "validate",
        parents=[with_config],
        help="check the configuration the other commands would load",
    )
    validate_cmd.set_defaults(command=_validate)
    schema_cmd = config_commands.add_parser(
        "schema", parents=[debug], help="print the JSON Schema of the configuration"
    )
    schema_cmd.set_defaults(command=_schema)
    return parser


def _load(path: Setting) -> DatasetConfig:
    """Loads the configuration; on failure, says where its path came from."""
    try:
        return FileConfigSource(path.value).load()
    except ConfigError as error:
        note = f"{path.value}: {_where(path)}"
        if path.origin == Origin.DEFAULT:
            note += f"; choose another file with --config or {CONFIG_VARIABLE}"
        raise ConfigError(f"{error}\n  {note}") from error


def _where(path: Setting) -> str:
    if path.origin == Origin.FLAG:
        return "given with --config"
    if path.origin == Origin.ENVIRONMENT:
        return f"given with {CONFIG_VARIABLE}"
    return "the default"


def _open(
    args: argparse.Namespace, environ: Mapping[str, str]
) -> tuple[DatasetConfig, BulkFileProvider]:
    config = _load(config_path(args.config, environ))
    directory = data_dir(args.data_dir, environ, config)
    return config, BulkFileProvider(config, directory.value)


def _fetch(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    _config, provider = _open(args, environ)
    release = provider.fetch()
    sys.stdout.write(f"{release.location} (sha256 {release.checksum_sha256})\n")
    return ExitCode.OK


def _transform(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    _config, provider = _open(args, environ)
    provider.transform()
    observations = provider.observations().num_rows
    series = len(provider.list_series())
    issues = len(provider.data_quality_issues())
    sys.stdout.write(
        f"{observations} observations of {series} series kept, "
        f"{issues} data-quality findings\n"
    )
    return ExitCode.OK


def _report(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    config, provider = _open(args, environ)
    # Arrow types kept, so values stay Decimal. The stubs leave a parameter of
    # to_pandas untyped.
    table = provider.observations()
    observations = table.to_pandas(types_mapper=ArrowDtype)  # pyright: ignore[reportUnknownMemberType]
    series = [provider.series_metadata(key) for key in provider.list_series()]
    issues = provider.data_quality_issues()
    release = provider.source_release()

    snapshots = compute_snapshots(
        SnapshotArgs(
            observations=observations,
            series=series,
            # compute_snapshots strips, upper-cases and drops the empty codes.
            requested=args.countries.split(","),
            settings=config.snapshot,
            ref_area_aliases=config.ref_area_aliases,
        )
    )
    paths = Report(config).export(
        ReportArgs(
            snapshots=snapshots,
            observations=observations,
            series=series,
            issues=issues,
            release=release,
            out_dir=args.out,
            start=args.start,
        )
    )
    for path in paths:
        sys.stdout.write(f"{path}\n")
    warning = _unknown_ref_area_warning(snapshots, series, config.ref_area_aliases)
    if warning:
        sys.stderr.write(warning)
    if not any(item.status == SnapshotStatus.OK for item in snapshots):
        sys.stderr.write(
            "no requested country or area has a complete result (status ok)\n"
        )
        return ExitCode.NO_RESULT
    return ExitCode.OK


def _validate(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    path = config_path(args.config, environ)
    _load(path)
    sys.stdout.write(f"{path.value}: valid ({_where(path)})\n")
    return ExitCode.OK


def _schema(_args: argparse.Namespace, _environ: Mapping[str, str]) -> int:
    # TODO(andrea-dm): refuse to run under python -OO, which strips the docstrings
    # the schema's descriptions are read from.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    sys.stdout.write(render_json_schema())
    return ExitCode.OK


def _unknown_ref_area_warning(
    snapshots: list[DataSnapshot],
    series: list[SeriesMetadata],
    ref_area_aliases: Mapping[str, str],
) -> str:
    """Names the requested codes the data does not have, and lists the ones it has.

    The user cannot be expected to know the source's codes, e.g. that the BIS
    calls the euro area ``XM``, so the warning shows every code with its name.
    """
    unknown = [
        item.requested_ref_area
        for item in snapshots
        if item.status == SnapshotStatus.UNKNOWN_REF_AREA
    ]
    if not unknown:
        return ""
    names = {item.ref_area: item.ref_area_name for item in series}
    available = ", ".join(f"{code} ({names[code]})" for code in sorted(names))
    aliases = "".join(
        f"; {alias} stands for {code}"
        for alias, code in sorted(ref_area_aliases.items())
    )
    return (
        f"warning: no series for {', '.join(unknown)}. "
        f"Available codes: {available}{aliases}.\n"
    )
