"""The ``bis-prates`` command: fetch, transform and report on the policy-rate data.

Each subcommand runs one step of the pipeline. Errors raised on purpose end the
command with a one-line message and the exit code of ``exit_codes``; ``--debug``
adds the traceback. Any other error is a bug: it is logged with its traceback and
ends the command with ``INTERNAL_ERROR``.

See also: [CLI reference](cli.md).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.cli.exit_codes import ExitCode, exit_code_for
from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.core.errors import PolicyRateMonitorError
from policy_rate_monitor.core.models import SnapshotStatus
from policy_rate_monitor.report import Report, ReportArgs
from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots

if TYPE_CHECKING:
    from collections.abc import Mapping

    from policy_rate_monitor.core.models import DataSnapshot, SeriesMetadata

__all__ = ["main"]

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("configs/bis_cbpol.json")
"""The configuration used without ``--config``, relative to the current folder."""


def main(argv: list[str] | None = None) -> int:
    """Runs one ``bis-prates`` command.

    Results go to standard output; warnings, errors and the log (set up here, at
    INFO level) go to standard error.

    Args:
        argv: The arguments after the program name; ``None`` reads ``sys.argv``.

    Returns:
        The exit code (see ``ExitCode``).

    Raises:
        SystemExit: Raised by argparse for ``--help`` (code 0) and for invalid
            arguments (code 2).
    """
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.command(args)
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
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"the dataset configuration (default: {DEFAULT_CONFIG})",
    )
    common.add_argument(
        "--data-dir",
        type=Path,
        help="the data directory (default: dataset.data_dir in the configuration)",
    )
    common.add_argument(
        "--debug", action="store_true", help="show the traceback of an error"
    )

    parser = argparse.ArgumentParser(
        prog="bis-prates", description="Monitor central-bank policy rates."
    )
    commands = parser.add_subparsers(title="commands", required=True)

    fetch_cmd = commands.add_parser(
        "fetch", parents=[common], help="download the source ZIP, or use the cache"
    )
    fetch_cmd.set_defaults(command=_fetch)

    transform_cmd = commands.add_parser(
        "transform", parents=[common], help="build the staged tables from the ZIP"
    )
    transform_cmd.set_defaults(command=_transform)

    report_cmd = commands.add_parser(
        "report", parents=[common], help="write the snapshot report"
    )
    report_cmd.add_argument(
        "--countries",
        required=True,
        help='comma-separated area codes, e.g. "US,EA,GB"',
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
    return parser


def _fetch(args: argparse.Namespace) -> int:
    config = FileConfigSource(args.config).load()
    provider = BulkFileProvider(config, args.data_dir or config.dataset.data_dir)
    release = provider.fetch()
    sys.stdout.write(f"{release.location} (sha256 {release.checksum_sha256})\n")
    return ExitCode.OK


def _transform(args: argparse.Namespace) -> int:
    config = FileConfigSource(args.config).load()
    provider = BulkFileProvider(config, args.data_dir or config.dataset.data_dir)
    provider.transform()
    observations = provider.observations().num_rows
    series = len(provider.list_series())
    issues = len(provider.data_quality_issues())
    sys.stdout.write(
        f"{observations} observations of {series} series kept, "
        f"{issues} data-quality findings\n"
    )
    return ExitCode.OK


def _report(args: argparse.Namespace) -> int:
    config = FileConfigSource(args.config).load()
    provider = BulkFileProvider(config, args.data_dir or config.dataset.data_dir)
    # Arrow types kept, so values stay Decimal. The stubs leave a parameter of
    # to_pandas untyped.
    table = provider.observations()
    observations = table.to_pandas(types_mapper=pd.ArrowDtype)  # pyright: ignore[reportUnknownMemberType]
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
            area_aliases=config.area_aliases,
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
    warning = _unknown_area_warning(snapshots, series, config.area_aliases)
    if warning:
        sys.stderr.write(warning)
    if not any(item.status == SnapshotStatus.OK for item in snapshots):
        sys.stderr.write("no requested area has a complete result (status ok)\n")
        return ExitCode.NO_RESULT
    return ExitCode.OK


def _unknown_area_warning(
    snapshots: list[DataSnapshot],
    series: list[SeriesMetadata],
    area_aliases: Mapping[str, str],
) -> str:
    """Names the requested codes the data does not have, and lists the ones it has.

    The user cannot be expected to know the source's codes, e.g. that the BIS
    calls the euro area ``XM``, so the warning shows every code with its name.
    """
    unknown = [
        item.requested_ref_area
        for item in snapshots
        if item.status == SnapshotStatus.UNKNOWN_AREA
    ]
    if not unknown:
        return ""
    names = {item.ref_area: item.ref_area_name for item in series}
    available = ", ".join(f"{code} ({names[code]})" for code in sorted(names))
    aliases = "".join(
        f"; {alias} stands for {code}" for alias, code in sorted(area_aliases.items())
    )
    return (
        f"warning: no series for {', '.join(unknown)}. "
        f"Available codes: {available}{aliases}.\n"
    )
