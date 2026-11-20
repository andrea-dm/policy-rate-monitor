from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.cli.exit_codes import ExitCode, exit_code_for
from policy_rate_monitor.cli.main import main
from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.core.errors import (
    PolicyRateMonitorError,
    SourceUnavailableError,
)
from policy_rate_monitor.core.models import SeriesMetadata
from policy_rate_monitor.providers import InMemoryProvider

CONFIG = Path(__file__).parents[1] / "configs" / "bis_cbpol.json"


@pytest.mark.unit
def test_help_lists_the_three_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in ("fetch", "transform", "report"):
        assert command in out


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
def test_report_before_transform_gives_a_one_line_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["report", "--countries", "US", "--config", str(CONFIG)]
    argv += ["--data-dir", str(tmp_path), "--out", str(tmp_path / "out")]

    code = main(argv)

    assert code == ExitCode.MISSING_INPUT
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "bis-prates transform" in lines[0]


@pytest.mark.contract
def test_an_unknown_code_lists_the_codes_the_data_has(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    structure = FileConfigSource(CONFIG).load().structure
    rows = [
        {
            "series_key": f"D.{area}",
            "freq": "D",
            "ref_area": area,
            "date": day,
            "value": Decimal("2.5"),
            "obs_status": "A",
            "is_valid": True,
        }
        for area in ("US", "XM")
        for day in (date(2025, 1, 2), date(2026, 1, 2))
    ]
    series = [
        SeriesMetadata(
            series_key=f"D.{area}",
            ref_area=area,
            ref_area_name=name,
            frequency="D",
            title=f"Policy rate of {name}",
        )
        for area, name in (("US", "United States"), ("XM", "Euro area"))
    ]
    provider = InMemoryProvider(structure, rows, series)

    def memory_provider(*_args: object) -> InMemoryProvider:
        return provider

    monkeypatch.setattr(
        "policy_rate_monitor.cli.main.BulkFileProvider", memory_provider
    )
    argv = ["report", "--countries", "US,UK", "--config", str(CONFIG)]
    argv += ["--out", str(tmp_path)]

    code = main(argv)

    assert code == ExitCode.OK
    warning = capsys.readouterr().err
    assert "no series for UK" in warning
    assert "US (United States), XM (Euro area)" in warning
    assert "EA stands for XM" in warning
