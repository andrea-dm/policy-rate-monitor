"""Contract tests of the typed errors."""

from __future__ import annotations

import pytest

from policy_rate_monitor.core import errors
from policy_rate_monitor.core.errors import PolicyRateMonitorError

DOMAIN_ERRORS = [
    errors.ConfigError,
    errors.SourceUnavailableError,
    errors.UnexpectedFormatError,
    errors.MissingInputError,
]


@pytest.mark.unit
@pytest.mark.parametrize("error_type", DOMAIN_ERRORS)
def test_every_domain_error_is_a_policy_rate_monitor_error(
    error_type: type[PolicyRateMonitorError],
) -> None:
    with pytest.raises(PolicyRateMonitorError, match="run fetch first"):
        raise error_type("run fetch first")


@pytest.mark.unit
def test_the_public_errors_are_exactly_the_documented_ones() -> None:
    # A new error needs an exit code in cli.exit_codes: this test is the reminder.
    assert set(errors.__all__) == {
        "PolicyRateMonitorError",
        *(error_type.__name__ for error_type in DOMAIN_ERRORS),
    }
