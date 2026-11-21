# The part of numba that policy_rate_monitor.analytics uses (numba ships no types).
from collections.abc import Callable
from typing import Any, Protocol

class _Decorator(Protocol):
    def __call__[F: Callable[..., Any]](self, func: F, /) -> F: ...

def njit(*, cache: bool = ...) -> _Decorator: ...
