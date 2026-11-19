"""The one parser every configuration source calls.

``parse_config`` turns a JSON document into a validated ``DatasetConfig``, or raises
``ConfigError``. The message starts with the document's origin and puts each problem
on its own line, located the way the JSON is written (``rules[1] ("name").enabled``),
so every source gives the same messages.

Examples:
    >>> try:
    ...     parse_config('{"dataset": ', origin="inline")
    ... except ConfigError as error:
    ...     print(str(error).splitlines()[0])
    inline: invalid JSON at line 1, column 13: Expecting value
"""

from __future__ import annotations

import codecs
import json
import re
from typing import TYPE_CHECKING, NoReturn, cast

from pydantic import ValidationError

from policy_rate_monitor.config._problems import KINDS
from policy_rate_monitor.config.models import DatasetConfig
from policy_rate_monitor.core.errors import ConfigError

if TYPE_CHECKING:
    from pydantic_core import ErrorDetails

__all__ = ["parse_config"]

_ECHO_LIMIT = 60
_EXCERPT_RADIUS = 40  # characters quoted before a JSON syntax error, and from it on
_KEY_MARKER = "[key]"  # pydantic's last location part for an invalid mapping key
# A key written as is in a location; any other key is quoted: selection["a b"].
_PLAIN_KEY = re.compile(r"[A-Za-z0-9_@$-]+")


class _DuplicateKeyError(ValueError):
    """An object in the document repeats a key."""


class _NonStandardConstantError(ValueError):
    """The document uses NaN or Infinity, which are not JSON."""


def parse_config(document: str | bytes, *, origin: str) -> DatasetConfig:
    """Parses and validates one JSON configuration document.

    Args:
        document: The JSON text. Bytes are decoded as UTF-8; a byte-order mark is
            accepted.
        origin: Where the document came from (a path, a URL). Every message starts
            with it.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: The bytes are not UTF-8, the text is not JSON, an object repeats a
            key, or validation fails. The original exception is the ``__cause__``.
    """
    text = _decode(document, origin)
    raw = _load(text, origin)
    try:
        return DatasetConfig.model_validate_json(text)
    except ValidationError as error:
        raise ConfigError(_validation_message(error, raw, origin)) from error


def _decode(document: str | bytes, origin: str) -> str:
    if isinstance(document, str):
        return document.removeprefix("\ufeff")
    try:
        return document.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        byte = error.object[error.start]
        # utf-8-sig counts from after the byte-order mark; report the file's own offset.
        bom = len(codecs.BOM_UTF8) if document.startswith(codecs.BOM_UTF8) else 0
        msg = (
            f"{origin}: not UTF-8 text "
            f"(invalid byte 0x{byte:02x} at offset {error.start + bom})"
        )
        raise ConfigError(msg) from error


def _load(text: str, origin: str) -> object:
    # The standard parser gives the line and column of a syntax error, and its hooks
    # catch what pydantic's parser lets through: a repeated key (pydantic keeps the
    # last one) and the non-standard NaN and Infinity.
    try:
        document: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ConfigError(_syntax_message(error, origin)) from error
    except (_DuplicateKeyError, _NonStandardConstantError) as error:
        msg = f"{origin}: invalid JSON: {error}"
        raise ConfigError(msg) from error
    # TODO(andrea-dm): give pydantic's JSON errors this form too; fix the comment below.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    except RecursionError as error:
        # Deep nesting ("[[[[...") exhausts the standard parser before pydantic's limit.
        msg = f"{origin}: invalid JSON: nested too deeply"
        raise ConfigError(msg) from error
    except ValueError as error:
        # TODO(andrea-dm): report the digits of a long integer, not Python's advice.
        # https://github.com/andrea-dm/policy-rate-monitor/issues/8
        # The rest of json.loads' ValueErrors, e.g. an integer longer than Python's
        # digit limit (4,300 by default). The three handled above are subclasses.
        msg = f"{origin}: invalid JSON: {error}"
        raise ConfigError(msg) from error
    return document


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            msg = f"duplicate key {key!r}"
            raise _DuplicateKeyError(msg)
        result[key] = value
    return result


def _reject_constant(name: str) -> NoReturn:
    msg = f"{name!r} is not a JSON number"
    raise _NonStandardConstantError(msg)


def _syntax_message(error: json.JSONDecodeError, origin: str) -> str:
    # json counts lines by "\n" only; splitlines() would also split on "\u2028" and
    # others, and quote the wrong line.
    lines = error.doc.split("\n")
    line = ""
    # TODO(andrea-dm): this check can never fail; remove it.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    if error.lineno <= len(lines):
        line = lines[error.lineno - 1].removesuffix("\r")
    # A long line (a minified document) is quoted around the error only; the line and
    # column in the first line of the message stay exact.
    start = max(0, error.colno - 1 - _EXCERPT_RADIUS)
    end = error.colno - 1 + _EXCERPT_RADIUS
    # TODO(andrea-dm): write "..." only when more than three characters are cut.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    head = "..." if start > 0 else ""
    tail = "..." if end < len(line) else ""
    # Each non-printable character (a tab, "\r", an escape sequence, "\u2028") is
    # shown as one space: the caret lines up, and the document cannot move the cursor
    # or add lines.
    shown = "".join(char if char.isprintable() else " " for char in line[start:end])
    number = str(error.lineno)
    # TODO(andrea-dm): wide characters (CJK, emoji) shift the caret; document the limit.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    caret = " " * (len(head) + error.colno - 1 - start) + "^"
    return "\n".join(
        [
            (
                f"{origin}: invalid JSON at line {error.lineno}, "
                f"column {error.colno}: {error.msg}"
            ),
            f"    {number} | {head}{shown}{tail}",
            f"    {' ' * len(number)} | {caret}",
        ]
    )


def _validation_message(error: ValidationError, raw: object, origin: str) -> str:
    details = error.errors(include_url=False)
    noun = "error" if len(details) == 1 else "errors"
    lines = [f"{origin}: invalid configuration ({len(details)} {noun})"]
    lines.extend(f"  {_describe(detail, raw)}" for detail in details)
    return "\n".join(lines)


def _describe(detail: ErrorDetails, raw: object) -> str:
    # The message can quote document text (this package's templates do), so it is
    # escaped like every other piece of document text (_printable).
    text = f"{_location(detail['loc'], raw)}: {_printable(detail['msg'])}"
    value: object = detail["input"]
    if detail["type"] == "missing" or detail["type"] in KINDS:
        return text
    if value is None or isinstance(value, str | int | float):
        shown = _quoted(value) if isinstance(value, str) else json.dumps(value)
        if len(shown) > _ECHO_LIMIT:
            shown = shown[: _ECHO_LIMIT - 3] + "..."
        text += f" (got {shown})"
    return text


def _location(loc: tuple[int | str, ...], raw: object) -> str:
    if not loc:
        return "(top level)"
    text = ""
    node = raw
    for part in loc:
        if isinstance(part, int):
            node = _item(node, part)
            text += f"[{part}]" + _label(node)
        # TODO(andrea-dm): read "[key]" as the marker only after a key, at the end.
        # https://github.com/andrea-dm/policy-rate-monitor/issues/8
        elif part == _KEY_MARKER:
            text += _KEY_MARKER
        elif _PLAIN_KEY.fullmatch(part):
            text += f".{part}" if text else part
            node = _member(node, part)
        else:
            text += f"[{_quoted(part)}]"
            node = _member(node, part)
    return text


def _member(node: object, key: str) -> object:
    if isinstance(node, dict):
        return cast(dict[str, object], node).get(key)
    return None


def _item(node: object, index: int) -> object:
    if isinstance(node, list):
        items = cast(list[object], node)
        return items[index] if index < len(items) else None
    return None


def _label(node: object) -> str:
    # A list item that has a name or an id is labelled with it: rules[7] ("x").
    if isinstance(node, dict):
        item = cast(dict[str, object], node)
        for key in ("name", "id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return f" ({_quoted(value)})"
    return ""


def _quoted(text: str) -> str:
    # Text from the document, quoted as JSON writes it (quotes and newlines escaped).
    return _printable(json.dumps(text, ensure_ascii=False))


def _printable(text: str) -> str:
    # Every non-printable character ("\n", "\u2028", an escape sequence) is escaped,
    # so text from the document stays on its error line and cannot add or fake lines.
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in text
    )
