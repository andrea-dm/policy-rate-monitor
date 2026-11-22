from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest

from policy_rate_monitor import __version__
from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.cli.exit_codes import ExitCode, exit_code_for
from policy_rate_monitor.cli.main import main
from policy_rate_monitor.config import SDMX_RULES, FileConfigSource
from policy_rate_monitor.core.errors import (
    MissingInputError,
    PolicyRateMonitorError,
    SourceUnavailableError,
)
from policy_rate_monitor.core.models import (
    SeriesMetadata,
    SourceRelease,
    TransformSummary,
)
from policy_rate_monitor.providers import InMemoryProvider
from policy_rate_monitor.settings import CONFIG_VARIABLE, DATA_DIR_VARIABLE
from policy_rate_monitor.transform import load_data_quality
from tests.bis_zip import write_bis_zip

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyarrow import Table

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "configs" / "bis_cbpol.json"
CHOOSE_ANOTHER = f"choose another file with --config or {CONFIG_VARIABLE}"
STRUCTURE = FileConfigSource(CONFIG).load().structure


class NothingStaged(InMemoryProvider):
    """Raises the missing-input error a provider raises before ``transform``."""

    def __init__(self, step: str | None) -> None:
        super().__init__(STRUCTURE, [], [])
        self.step = step

    @override
    def observations(self, series_keys: Sequence[str] | None = None) -> Table:
        msg = "data/staged/observations.parquet does not exist"
        raise MissingInputError(msg, step=self.step)


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: InMemoryProvider) -> None:
    """Makes the commands use *provider* in place of the bulk-file one."""

    def make(*_args: object, **_kwargs: object) -> InMemoryProvider:
        return provider

    monkeypatch.setattr("policy_rate_monitor.cli.main.BulkFileProvider", make)


def us_and_xm() -> InMemoryProvider:
    """Two series, US and XM, each with a rate a year apart."""
    rows = [
        {
            "series_key": f"D.{ref_area}",
            "freq": "D",
            "ref_area": ref_area,
            "date": day,
            "value": Decimal("2.5"),
            "obs_status": "A",
            "is_valid": True,
        }
        for ref_area in ("US", "XM")
        for day in (date(2025, 1, 2), date(2026, 1, 2))
    ]
    series = [
        SeriesMetadata(
            series_key=f"D.{ref_area}",
            ref_area=ref_area,
            ref_area_name=name,
            frequency="D",
            title=f"Policy rate of {name}",
        )
        for ref_area, name in (("US", "United States"), ("XM", "Euro area"))
    ]
    return InMemoryProvider(STRUCTURE, rows, series)


@pytest.mark.unit
def test_help_lists_the_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in ("fetch", "transform", "report", "serve", "config"):
        assert command in out


@pytest.mark.unit
def test_version_prints_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"bis-prates {__version__}\n"


@pytest.mark.contract
@pytest.mark.parametrize(
    ("argv", "environ", "where"),
    [
        (["--config", str(CONFIG)], {}, "given with --config"),
        ([], {CONFIG_VARIABLE: str(CONFIG)}, f"given with {CONFIG_VARIABLE}"),
        ([], {}, "the default"),
    ],
    ids=["flag", "variable", "default"],
)
def test_config_validate_names_the_file_it_checked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    environ: dict[str, str],
    where: str,
) -> None:
    monkeypatch.chdir(ROOT)

    code = main(["config", "validate", *argv], environ)

    assert code == ExitCode.OK
    assert capsys.readouterr().out.endswith(f"bis_cbpol.json: valid ({where})\n")


@pytest.mark.contract
@pytest.mark.parametrize("content", [None, "{"], ids=["missing", "malformed"])
@pytest.mark.parametrize(
    ("argv", "environ", "note"),
    [
        (["--config", "bad.json"], {}, "bad.json: given with --config"),
        ([], {CONFIG_VARIABLE: "bad.json"}, f"bad.json: given with {CONFIG_VARIABLE}"),
        ([], {}, f"bis_cbpol.json: the default; {CHOOSE_ANOTHER}"),
    ],
    ids=["flag", "variable", "default"],
)
def test_a_bad_configuration_says_where_its_path_came_from(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str | None,
    argv: list[str],
    environ: dict[str, str],
    note: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    if content is not None:
        (tmp_path / "bad.json").write_text(content)
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "bis_cbpol.json").write_text(content)

    code = main(["config", "validate", *argv], environ)

    assert code == ExitCode.CONFIG_ERROR
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert err.rstrip().endswith(note)
    assert "Traceback" not in err


@pytest.mark.contract
def test_config_schema_prints_the_committed_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    committed = (ROOT / "configs" / "config.schema.json").read_text(encoding="ascii")
    monkeypatch.chdir(tmp_path)

    code = main(["config", "schema"], {})

    assert code == ExitCode.OK
    assert capsys.readouterr().out == committed


@pytest.mark.contract
def test_the_data_directory_can_come_from_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["report", "--countries", "US", "--config", str(CONFIG)]
    argv += ["--out", str(tmp_path / "out")]
    environ = {DATA_DIR_VARIABLE: str(tmp_path / "elsewhere")}

    code = main(argv, environ)

    assert code == ExitCode.MISSING_INPUT
    assert "elsewhere" in capsys.readouterr().err


@pytest.mark.unit
def test_an_error_maps_to_its_exit_code() -> None:
    class OfflineError(SourceUnavailableError):
        pass

    assert exit_code_for(OfflineError("down")) == ExitCode.SOURCE_UNAVAILABLE
    assert exit_code_for(PolicyRateMonitorError("?")) == ExitCode.INTERNAL_ERROR


@pytest.mark.contract
def test_a_bug_ends_with_the_internal_error_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_fetch(*_args: object) -> None:
        raise KeyError("ref_area")

    monkeypatch.setattr(BulkFileProvider, "fetch", broken_fetch)

    code = main(["fetch", "--config", str(CONFIG)])

    assert code == ExitCode.INTERNAL_ERROR
    assert "error: KeyError: 'ref_area'" in capsys.readouterr().err


@pytest.mark.contract
@pytest.mark.parametrize(
    ("step", "hint"),
    [("transform", ": run `bis-prates transform` first"), (None, "")],
    ids=["with-step", "without-step"],
)
def test_a_missing_input_gives_a_one_line_error_naming_the_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    step: str | None,
    hint: str,
) -> None:
    use_provider(monkeypatch, NothingStaged(step))
    argv = ["report", "--countries", "US", "--config", str(CONFIG)]

    code = main([*argv, "--out", str(tmp_path / "out")])

    assert code == ExitCode.MISSING_INPUT
    assert capsys.readouterr().err == (
        f"error: data/staged/observations.parquet does not exist{hint}\n"
    )


@pytest.mark.contract
def test_fetch_prints_where_the_release_came_from_and_its_checksum(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    release = SourceRelease(
        location="https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip",
        checksum_sha256="ab" * 32,
        retrieved_at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    use_provider(monkeypatch, InMemoryProvider(STRUCTURE, [], [], release=release))

    code = main(["fetch", "--config", str(CONFIG)])

    assert code == ExitCode.OK
    assert capsys.readouterr().out == (
        f"https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip (sha256 {'ab' * 32})\n"
    )


class CountedTransform(NothingStaged):
    """Returns fixed counts from ``transform``; reading the staged data fails."""

    @override
    def transform(self) -> TransformSummary:
        return TransformSummary(rows_read=10, observations=7, series=3, issues=2)


@pytest.mark.contract
def test_transform_prints_the_counts_the_run_returns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_provider(monkeypatch, CountedTransform(step="transform"))

    code = main(["transform", "--config", str(CONFIG)])

    assert code == ExitCode.OK
    assert capsys.readouterr().out == (
        "7 observations of 3 series kept, 2 data-quality findings\n"
    )


@pytest.mark.contract
def test_an_output_folder_that_is_a_file_exits_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    use_provider(monkeypatch, us_and_xm())
    not_a_folder = tmp_path / "out"
    not_a_folder.write_text("")
    argv = ["report", "--countries", "US", "--config", str(CONFIG)]

    code = main([*argv, "--out", str(not_a_folder)])

    assert code == ExitCode.OUTPUT_ERROR
    err = capsys.readouterr().err
    assert err.startswith("error: cannot read or write a file: ")
    assert "Traceback" not in err


@pytest.mark.contract
@pytest.mark.parametrize("bad", ['=HYPERLINK("x")', "<img src=x>", "U S"])
def test_report_refuses_a_code_outside_the_code_pattern(
    capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["report", "--countries", f"US,{bad}", "--config", str(CONFIG)])

    assert exit_info.value.code == ExitCode.USAGE_ERROR
    err = capsys.readouterr().err
    assert f"invalid code {bad!r}" in err


@pytest.mark.contract
@pytest.mark.parametrize("blank", ["", " , "])
def test_report_refuses_a_list_with_no_code_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, blank: str
) -> None:
    argv = ["report", "--countries", blank, "--config", str(CONFIG)]

    with pytest.raises(SystemExit) as exit_info:
        main([*argv, "--out", str(tmp_path)])

    assert exit_info.value.code == ExitCode.USAGE_ERROR
    assert "name at least one country or area code" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.contract
def test_report_strips_the_codes_and_drops_the_empty_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_provider(monkeypatch, us_and_xm())
    argv = ["report", "--countries", " us, ,XM ", "--config", str(CONFIG)]

    code = main([*argv, "--out", str(tmp_path)])

    assert code == ExitCode.OK
    snapshots = json.loads((tmp_path / "summary.json").read_text())["snapshots"]
    assert [item["requested_ref_area"] for item in snapshots] == ["us", "XM"]


@pytest.mark.contract
def test_an_unknown_code_lists_the_codes_the_data_has(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    use_provider(monkeypatch, us_and_xm())
    argv = ["report", "--countries", "US,UK", "--config", str(CONFIG)]
    argv += ["--out", str(tmp_path)]

    code = main(argv)

    assert code == ExitCode.OK
    warning = capsys.readouterr().err
    assert "no series for UK" in warning
    assert "US (United States), XM (Euro area)" in warning
    assert "EA stands for XM" in warning


@pytest.mark.contract
def test_serve_without_the_web_extra_names_it_and_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # As if FastAPI were not installed: the web package must be imported afresh.
    for name in [
        name for name in sys.modules if name.startswith("policy_rate_monitor.web")
    ]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "fastapi", None)

    code = main(["serve", "--config", str(CONFIG)])

    assert code == ExitCode.USAGE_ERROR
    assert "policy-rate-monitor[web]" in capsys.readouterr().err


@pytest.mark.contract
def test_serve_with_a_bad_configuration_exits_4(tmp_path: Path) -> None:
    code = main(["serve", "--config", str(tmp_path / "nope.json")])

    assert code == ExitCode.CONFIG_ERROR


@pytest.mark.contract
def test_serve_runs_the_api_with_the_resolved_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_serve(config: object, provider: object, **options: object) -> None:
        calls.append({"config": config, "provider": provider, **options})

    monkeypatch.setattr("policy_rate_monitor.web.serve", fake_serve)
    environ = {DATA_DIR_VARIABLE: str(tmp_path)}

    code = main(["serve", "--config", str(CONFIG), "--port", "9000"], environ)

    assert code == ExitCode.OK
    (call,) = calls
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 9000
    assert call["out_dir"] == Path("out")
    assert isinstance(call["provider"], BulkFileProvider)


@pytest.mark.contract
@pytest.mark.parametrize("port", ["0", "65536", "-1", "http", "80.5"])
def test_serve_refuses_a_port_outside_1_to_65535(
    capsys: pytest.CaptureFixture[str], port: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["serve", "--config", str(CONFIG), "--port", port])

    assert exit_info.value.code == ExitCode.USAGE_ERROR
    assert f"invalid port {port!r}" in capsys.readouterr().err


@pytest.mark.contract
def test_serve_on_a_busy_port_exits_2_naming_the_host_and_port(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {DATA_DIR_VARIABLE: str(tmp_path)}
    with socket.create_server(("127.0.0.1", 0)) as busy:
        port = busy.getsockname()[1]

        code = main(["serve", "--config", str(CONFIG), "--port", str(port)], environ)

    assert code == ExitCode.USAGE_ERROR
    err = capsys.readouterr().err
    assert err.startswith(f"error: cannot listen on 127.0.0.1, port {port}: ")
    assert len(err.splitlines()) == 1


def without_sdmx_rules(directory: Path) -> Path:
    """Writes the committed configuration with its SDMX rules off; returns its path."""
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    for rule in document["rules"]:
        if rule["name"] in SDMX_RULES:
            rule["enabled"] = False
    path = directory / "no_sdmx.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.contract
# With nothing fetched, transform ends at once with exit 5: no network is needed.
@pytest.mark.parametrize(
    ("argv", "loaded"),
    [
        (["--version"], []),
        (["config", "validate", "--config", str(CONFIG)], []),
        (["transform", "--config", "SDMX_OFF"], []),
        (["transform", "--config", str(CONFIG)], ["pysdmx"]),
    ],
    ids=["version", "validate", "transform_sdmx_off", "transform_sdmx_on"],
)
def test_the_base_commands_load_neither_the_web_extra_nor_the_report_steps(
    tmp_path: Path, argv: list[str], loaded: list[str]
) -> None:
    argv = [str(without_sdmx_rules(tmp_path)) if a == "SDMX_OFF" else a for a in argv]
    # A fresh interpreter, as the modules this test suite imported stay loaded.
    code = (
        "import sys\n"
        "from policy_rate_monitor.cli.main import main\n"
        "try:\n"
        f"    main({argv!r})\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(sorted({name.split('.')[0] for name in sys.modules} & "
        "{'fastapi', 'plotly', 'starlette', 'uvicorn', 'matplotlib', 'jinja2', "
        "'pysdmx'}))\n"
    )
    environ = {**os.environ, DATA_DIR_VARIABLE: str(tmp_path)}

    result = subprocess.run(  # noqa: S603 - our own interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=environ,
    )

    assert result.stdout.splitlines()[-1] == str(loaded)


@pytest.mark.contract
def test_transform_without_the_sdmx_extra_reports_not_evaluated_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # As if pysdmx were not installed: the sdmx module must be imported afresh.
    monkeypatch.delitem(sys.modules, "policy_rate_monitor.sdmx", raising=False)
    # Its submodules too: an import finds a loaded submodule without its parent.
    for name in ["pysdmx", *(n for n in sys.modules if n.startswith("pysdmx."))]:
        monkeypatch.setitem(sys.modules, name, None)
    write_bis_zip(tmp_path, FileConfigSource(CONFIG).load().source.file_name)

    code = main(
        ["transform", "--config", str(CONFIG)], {DATA_DIR_VARIABLE: str(tmp_path)}
    )

    assert code == ExitCode.OK
    details = {
        issue.rule: issue.detail
        for issue in load_data_quality(tmp_path)
        if issue.rule in SDMX_RULES
    }
    assert details == dict.fromkeys(
        SDMX_RULES,
        "not evaluated: the sdmx extra is not installed: "
        "install policy-rate-monitor[sdmx]",
    )


@pytest.mark.contract
def test_an_sdmx_rule_without_a_reference_section_fails_validation_with_exit_4(
    tmp_path: Path,
) -> None:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    del document["reference"]
    path = tmp_path / "no_reference.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    code = main(["config", "validate", "--config", str(path)])

    assert code == ExitCode.CONFIG_ERROR
