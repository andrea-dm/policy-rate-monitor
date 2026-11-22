"""The ``bis-prates`` command: fetch, transform and report on the policy-rate data.

Each subcommand runs one step of the pipeline; ``serve`` runs the HTTP API, and
``config validate`` and ``config schema`` check the configuration and print its
format. Errors raised on purpose end the command with a one-line message and the
exit code of ``exit_codes``; ``--debug`` adds the traceback. Any other error is a
bug: it is logged with its traceback and ends the command with ``INTERNAL_ERROR``.

The report steps (``service``, and with it matplotlib and Jinja) are imported
only by ``report`` and ``serve``, so ``--version``, ``fetch`` and ``transform``
start without them. ``pysdmx`` (the ``sdmx`` extra) is imported only by ``transform``,
when an SDMX rule is enabled, and by ``report`` and ``serve``, when the configuration
has a ``reference`` section, to check the requested codes.

See also: [CLI reference](cli.md) and [the command
line](advanced/operations/workflows.md#the-command-line).
"""

from __future__ import annotations

import logging
import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from policy_rate_monitor import __version__
from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.cli.exit_codes import ExitCode, exit_code_for
from policy_rate_monitor.config import SDMX_RULES, FileConfigSource, requested_codes
from policy_rate_monitor.config.schema import render_json_schema
from policy_rate_monitor.core.errors import (
    ConfigError,
    MissingInputError,
    PolicyRateMonitorError,
)
from policy_rate_monitor.core.models import SnapshotStatus
from policy_rate_monitor.reference import NO_REFERENCE, UnavailableReference
from policy_rate_monitor.settings import (
    CONFIG_VARIABLE,
    DATA_DIR_VARIABLE,
    DEFAULT_CONFIG,
    Origin,
    Setting,
    config_path,
    data_dir,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from policy_rate_monitor.config import DatasetConfig
    from policy_rate_monitor.core.models import DataSnapshot, SeriesMetadata
    from policy_rate_monitor.reference import ReferenceSource

__all__ = ["main"]

# The packages of the web extra: an ImportError naming one means the extra is missing.
_WEB_PACKAGES = frozenset({"fastapi", "plotly", "starlette", "uvicorn"})
_NO_SDMX_EXTRA = "the sdmx extra is not installed: install policy-rate-monitor[sdmx]"
_LAST_PORT = 65535

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
    # httpx, under pysdmx, logs every request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return args.command(args, os.environ if environ is None else environ)
    except PolicyRateMonitorError as error:
        if args.debug:
            logger.exception("the command failed")
        sys.stderr.write(f"error: {_message(error)}\n")
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


def _message(error: PolicyRateMonitorError) -> str:
    """The error's message, with the command to run first when an input is missing."""
    if isinstance(error, MissingInputError) and error.step is not None:
        return f"{error}: run `bis-prates {error.step}` first"
    return str(error)


def _codes(text: str) -> list[str]:
    """Splits ``--countries`` at the commas and checks the codes."""
    try:
        return requested_codes(text.split(","))
    except ValueError as error:
        raise ArgumentTypeError(str(error)) from None


def _port(text: str) -> int:
    msg = f"invalid port {text!r}: use a whole number from 1 to {_LAST_PORT}"
    try:
        port = int(text)
    except ValueError:
        raise ArgumentTypeError(msg) from None
    if not 1 <= port <= _LAST_PORT:
        raise ArgumentTypeError(msg)
    return port


def _parser() -> ArgumentParser:
    debug = ArgumentParser(add_help=False)
    debug.add_argument(
        "--debug", action="store_true", help="show the traceback of an error"
    )
    with_config = ArgumentParser(add_help=False, parents=[debug])
    with_config.add_argument(
        "--config",
        type=Path,
        help=f"the dataset configuration (default: ${CONFIG_VARIABLE}, "
        f"else {DEFAULT_CONFIG})",
    )
    with_data = ArgumentParser(add_help=False, parents=[with_config])
    with_data.add_argument(
        "--data-dir",
        type=Path,
        help=f"the data directory (default: ${DATA_DIR_VARIABLE}, "
        "else dataset.data_dir in the configuration)",
    )

    parser = ArgumentParser(
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
        type=_codes,
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

    serve_cmd = commands.add_parser(
        "serve", parents=[with_data], help="run the HTTP API (needs the web extra)"
    )
    serve_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help="the address to listen on (default: %(default)s)",
    )
    serve_cmd.add_argument(
        "--port",
        type=_port,
        default=8000,
        help="the port to listen on (default: %(default)s)",
    )
    serve_cmd.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="where a report job writes its files (default: out)",
    )
    serve_cmd.set_defaults(command=_serve)

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
    args: Namespace,
    environ: Mapping[str, str],
    *,
    transforms: bool = False,
    checks_codes: bool = False,
) -> tuple[DatasetConfig, BulkFileProvider, ReferenceSource]:
    """Loads the configuration and opens its provider and its SDMX reference source.

    A command gets a reference source only when it needs one: *transforms* (it can
    run ``transform``) when an SDMX rule is enabled, *checks_codes* (it checks
    requested codes) whenever the configuration has a ``reference`` section.
    """
    config = _load(config_path(args.config, environ))
    directory = data_dir(args.data_dir, environ, config)
    rules_on = any(rule.enabled and rule.name in SDMX_RULES for rule in config.rules)
    needed = checks_codes or (transforms and rules_on)
    reference = _reference(config) if needed else NO_REFERENCE
    provider = BulkFileProvider(config, directory.value, reference=reference)
    return config, provider, reference


def _reference(config: DatasetConfig) -> ReferenceSource:
    """The SDMX reference source of the configuration; ``pysdmx`` is loaded here."""
    if config.reference is None:
        return NO_REFERENCE
    try:
        from policy_rate_monitor.sdmx import (  # noqa: PLC0415 - an optional extra
            SdmxReference,
        )
    except ImportError as error:
        if (error.name or "").partition(".")[0] != "pysdmx":
            raise
        return UnavailableReference(_NO_SDMX_EXTRA)
    return SdmxReference(config.reference)


def _fetch(args: Namespace, environ: Mapping[str, str]) -> int:
    _config, provider, _ = _open(args, environ)
    release = provider.fetch()
    sys.stdout.write(f"{release.location} (sha256 {release.checksum_sha256})\n")
    return ExitCode.OK


def _transform(args: Namespace, environ: Mapping[str, str]) -> int:
    _config, provider, _ = _open(args, environ, transforms=True)
    counts = provider.transform()
    sys.stdout.write(
        f"{counts.observations} observations of {counts.series} series kept, "
        f"{counts.issues} data-quality findings\n"
    )
    return ExitCode.OK


def _report(args: Namespace, environ: Mapping[str, str]) -> int:
    # Imported here: the report steps load matplotlib and Jinja.
    from policy_rate_monitor.service import (  # noqa: PLC0415
        load_staged,
        summary,
        write_report,
    )

    config, provider, reference = _open(args, environ, checks_codes=True)
    data = load_staged(provider)
    # The summary upper-cases the codes and drops the repeats.
    snapshots = summary(config, data, args.countries, reference)
    for path in write_report(config, data, snapshots, args.out, args.start):
        sys.stdout.write(f"{path}\n")
    warning = _unknown_ref_area_warning(snapshots, data.series, config.ref_area_aliases)
    if warning:
        sys.stderr.write(warning)
    if not any(item.status == SnapshotStatus.OK for item in snapshots):
        sys.stderr.write(
            "no requested country or area has a complete result (status ok)\n"
        )
        return ExitCode.NO_RESULT
    return ExitCode.OK


def _serve(args: Namespace, environ: Mapping[str, str]) -> int:
    try:
        from policy_rate_monitor.web import (  # noqa: PLC0415 - an optional extra
            ListenError,
            serve,
        )
    except ImportError as error:
        if (error.name or "").partition(".")[0] not in _WEB_PACKAGES:
            raise
        sys.stderr.write(
            "error: bis-prates serve needs the web extra: "
            "install policy-rate-monitor[web]\n"
        )
        return ExitCode.USAGE_ERROR
    # The API's transform job runs the SDMX rules too.
    config, provider, reference = _open(
        args, environ, transforms=True, checks_codes=True
    )
    try:
        serve(
            config,
            provider,
            host=args.host,
            port=args.port,
            out_dir=args.out,
            reference=reference,
        )
    except ListenError as error:
        sys.stderr.write(f"error: {error}\n")
        return ExitCode.USAGE_ERROR
    return ExitCode.OK


def _validate(args: Namespace, environ: Mapping[str, str]) -> int:
    path = config_path(args.config, environ)
    _load(path)
    sys.stdout.write(f"{path.value}: valid ({_where(path)})\n")
    return ExitCode.OK


def _schema(_args: Namespace, _environ: Mapping[str, str]) -> int:
    # TODO(andrea-dm): refuse to run under python -OO, which strips the docstrings
    # the schema's descriptions are read from.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    sys.stdout.write(render_json_schema())
    return ExitCode.OK


def _unknown_ref_area_warning(
    snapshots: Sequence[DataSnapshot],
    series: Sequence[SeriesMetadata],
    ref_area_aliases: Mapping[str, str],
) -> str:
    """Names the requested codes the data does not have, and lists the ones it has.

    The user cannot be expected to know the source's codes, e.g. that the BIS
    calls the euro area ``XM``, so the warning shows every code with its name. A
    code the code list has is named; one it does not have gets its suggestions.
    """
    unknown = [
        item for item in snapshots if item.status == SnapshotStatus.UNKNOWN_REF_AREA
    ]
    if not unknown:
        return ""
    names = {item.ref_area: item.ref_area_name for item in series}
    lines = [_unknown_line(item, names) for item in unknown]
    available = ", ".join(f"{code} ({names[code]})" for code in sorted(names))
    aliases = "".join(
        f"; {alias} stands for {code}"
        for alias, code in sorted(ref_area_aliases.items())
    )
    return "".join(lines) + f"Available codes: {available}{aliases}.\n"


def _unknown_line(snapshot: DataSnapshot, names: Mapping[str, str]) -> str:
    code = snapshot.requested_ref_area
    if snapshot.ref_area_name is not None:
        return f"warning: no series for {code} ({snapshot.ref_area_name}).\n"
    if snapshot.suggestions:
        offered = " or ".join(
            f"{found} ({names[found]})" if found in names else found
            for found in snapshot.suggestions
        )
        return (
            f"warning: {code} is not in the country or area code list; "
            f"did you mean {offered}?\n"
        )
    return f"warning: no series for {code}.\n"
