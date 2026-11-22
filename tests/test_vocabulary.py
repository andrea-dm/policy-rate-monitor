"""One name for a country or economic area: ``ref_area``, after SDMX's REF_AREA.

The shorter word alone is too narrow, and "country" is wrong for the euro area, so
code says ``ref_area`` (``RefArea``, ``REF_AREA``) and text says "country or
economic area" ("country or area" when short). These tests keep it that way; this
module is the one place that has to spell out the word it looks for.

The rule is in the glossary (`docs/glossary.md`) and in decision 0004. It dates from
2026-11-21. A decision record is not edited once accepted (`docs/adr/index.md`), so the
records decided before that day are checked only in their update notes from that day on,
and in the records and the CHANGELOG, text in double quotes is a quotation, not checked.
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).parents[1]
PYTHON_FILES = sorted(
    path
    for folder in ("src", "tests")
    for path in (ROOT / folder).rglob("*.py")
    if "__pycache__" not in path.parts and path.name != Path(__file__).name
)
RECORDS = sorted((ROOT / "docs" / "adr").glob("*.md"))
TEXT_FILES = sorted(
    [
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        *(path for path in (ROOT / "docs").rglob("*.md") if path not in RECORDS),
    ]
)
CHANGELOG = ROOT / "CHANGELOG.md"
CONFIG_FILES = sorted((ROOT / "configs").glob("*.json"))
NOTEBOOKS = sorted((ROOT / "examples").glob("*.ipynb"))

# A name may contain "area" only as part of ref_area, or of euro_area and the like.
GOOD_NAME = re.compile(r"ref_?area|euro_area|economic_area", re.IGNORECASE)
# The word "area" in text, unless it is one of the phrases below ("or-areas" is the
# anchor of a heading that says "or areas").
LONE_AREA = re.compile(
    r"(?<!euro )(?<!euro-)(?<!economic )(?<!or )(?<!or-)(?<!reference )(?<!reference-)"
    r"(?<!ref-)\bareas?\b",
    re.IGNORECASE,
)

# The day the rule was decided (decision 0004, Updates).
RULE_DATE = "2026-11-21"
DECIDED = re.compile(r"^- \*\*Decided:\*\* (\d{4}-\d{2}-\d{2})", re.MULTILINE)
UPDATE_NOTE = re.compile(r"^- (\d{4}-\d{2}-\d{2})")
# A quotation: a message as an older version printed it, or the word itself ("area").
# Old names (`area_aliases`, `unknown_area`, `AreaAliases`) never match LONE_AREA.
QUOTED = re.compile(r'"[^"]*"')

pytestmark = pytest.mark.unit


def _names_and_text(source: str) -> tuple[list[str], list[str]]:
    names: list[str] = []
    text: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            names.append(token.string)
        elif token.type in {tokenize.STRING, tokenize.COMMENT}:
            text.append(token.string)
    return names, text


def _record_lines(text: str) -> list[str]:
    """The lines of a decision record that were written under the rule."""
    decided = DECIDED.search(text)
    lines = text.splitlines()
    if decided is None or decided.group(1) >= RULE_DATE:
        return lines
    checked: list[str] = []
    in_updates = False
    in_new_note = False
    for line in lines:
        if line.startswith("## "):
            in_updates = line == "## Updates"
            in_new_note = False
        elif in_updates and line.startswith("- "):
            note = UPDATE_NOTE.match(line)
            in_new_note = note is not None and note.group(1) >= RULE_DATE
        if in_updates and in_new_note:
            checked += [line]
    return checked


def _keys(value: object) -> list[str]:
    if isinstance(value, dict):
        document = cast("dict[str, object]", value)
        return [name for key, item in document.items() for name in (key, *_keys(item))]
    if isinstance(value, list):
        return [name for item in cast("list[object]", value) for name in _keys(item)]
    return []


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda path: path.name)
def test_python_names_say_ref_area(path: Path) -> None:
    names, _text = _names_and_text(path.read_text(encoding="utf-8"))

    wrong = {
        name for name in names if "area" in name.lower() and not GOOD_NAME.search(name)
    }

    assert not wrong, f"{path.relative_to(ROOT)}: rename {sorted(wrong)} to ref_area"


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda path: path.name)
def test_python_text_says_country_or_economic_area(path: Path) -> None:
    _names, text = _names_and_text(path.read_text(encoding="utf-8"))

    wrong = [line for line in text if LONE_AREA.search(line)]

    assert not wrong, f"{path.relative_to(ROOT)}: {wrong}"


@pytest.mark.parametrize(
    "path", TEXT_FILES, ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_docs_say_country_or_economic_area(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()

    wrong = [line for line in lines if LONE_AREA.search(line)]

    assert not wrong, f"{path.relative_to(ROOT)}: {wrong}"


@pytest.mark.parametrize("path", [*RECORDS, CHANGELOG], ids=lambda path: path.name)
def test_records_and_changelog_say_country_or_economic_area(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = _record_lines(text) if path in RECORDS else text.splitlines()

    wrong = [line for line in lines if LONE_AREA.search(QUOTED.sub("", line))]

    assert not wrong, f"{path.relative_to(ROOT)}: {wrong}"


@pytest.mark.whitebox
def test_old_records_are_checked_only_in_their_new_update_notes() -> None:
    record = """# 0001 Old
- **Decided:** 2026-11-17

Every area.

## Updates

- 2026-11-20: an area.
- 2026-11-22: a country or area,
  and an area.
"""

    assert _record_lines(record) == [
        "- 2026-11-22: a country or area,",
        "  and an area.",
    ]


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda path: path.name)
def test_configuration_keys_say_ref_area(path: Path) -> None:
    keys = _keys(json.loads(path.read_text(encoding="utf-8")))

    wrong = {key for key in keys if "area" in key.lower() and not GOOD_NAME.search(key)}

    assert not wrong, f"{path.relative_to(ROOT)}: rename {sorted(wrong)}"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.name)
def test_notebooks_say_ref_area_and_country_or_economic_area(path: Path) -> None:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    names: list[str] = []
    text: list[str] = []
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if cell["cell_type"] == "code":
            cell_names, cell_text = _names_and_text(source)
            names += cell_names
            text += cell_text
        else:
            text += source.splitlines()

    wrong_names = {
        name for name in names if "area" in name.lower() and not GOOD_NAME.search(name)
    }
    wrong_text = [line for line in text if LONE_AREA.search(line)]

    assert not wrong_names, f"{path.relative_to(ROOT)}: rename {sorted(wrong_names)}"
    assert not wrong_text, f"{path.relative_to(ROOT)}: {wrong_text}"
