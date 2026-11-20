# Contributing

Please read the [README](README.md) first: it covers cloning, installing the dependencies and the
local development workflow.

---

## Workflow

1. **Open an issue** describing the change, or pick an existing one.
2. **Branch** off `develop`, named after the issue: `<number>-<short-description>`, e.g.
   `10-fetch-and-transform-the-policy-rate-data`. GitHub can create the branch from the issue.
3. **Implement** the change in `src/policy_rate_monitor/`. Keep each module's responsibility
   intact; `tach.toml` declares which module may import which.
4. **Test**: add tests in `tests/` with the right marker (see [Testing](#testing)). One test per
   behaviour, each meaningful on its own; no test written only to raise the coverage.
5. **Lint and type-check** before pushing:
   ```bash
   uv run ruff check src tests --fix
   uv run ruff format src tests
   uv run pyright src/ tests/
   uv run deptry src/ tests/
   uv run tach check
   uv run pytest
   ```
   The hooks in `.pre-commit-config.yaml` run ruff at every commit and pyright, deptry and tach at
   every push (pytest is not a hook); install both once with `uvx pre-commit install`.
6. **Update the changelog**: add an entry under `## [Unreleased]` in `CHANGELOG.md`.
7. **Open a pull request** against `develop`, saying what changed and why, and linking the issue
   (`Closes #<number>`).
8. Once CI is green and the review threads are resolved, the maintainer merges the pull request.

---

## Code Standards

- **Python 3.12** syntax and later (the package supports 3.12, 3.13 and 3.14).
- `from __future__ import annotations` in every module; imports needed only for type hints go
  under `TYPE_CHECKING`.
- Public modules, classes and functions have Google-style docstrings; comments explain only a
  non-obvious *why*.
- Pydantic models are immutable (`frozen=True`) and reject unknown fields (`extra="forbid"`).
- Nothing specific to the dataset (column names, codes, URLs) in the code: it belongs in the
  configuration.
- Values are exact: rates stay decimals in the staged data, the calculations and the summary
  files. Only the chart converts them to floats, for drawing.
- Plain, everyday Python: no abstraction with a single implementation, no cleverness a reviewer
  has to decode.

---

## Quality Gates

CI (`.github/workflows/ci.yml`) runs these checks on every pull request, except the package
check, which runs on pushes to `develop` and on releases. On pull requests the security audits
and the secret detection are informational; the other checks must pass before a merge. The
commands are the local equivalents.

| Check | Command | Scope |
|---|---|---|
| Secret detection | (CI job `secret-detection`, informational) | Pull requests and branch pushes |
| Security audit | `uv export --locked --no-dev --all-extras --no-emit-project --output-file requirements-audit.txt` then `uvx --from 'pip-audit>=2.7,<3' pip-audit --strict --disable-pip --requirement requirements-audit.txt` | Runtime dependencies (CI job `audit`); blocking on `main` and `develop` |
| Security audit, development groups | The same with `--all-groups` (CI job `audit-dev`) | Every dependency group and extra |
| Lint | `uv run ruff check` | `src/`, `tests/`, `diagnostics/` (`include` in `pyproject.toml`) |
| Format | `uv run ruff format --check` | Same as lint |
| Dependencies | `uv run deptry src/ tests/` | Declared dependencies match the imports |
| Import graph | `uv run tach check` | Module boundaries declared in `tach.toml` |
| Commit format | (CI job `commit-format`) | Every non-merge commit of a pull request |
| Docs build | `uv run mkdocs build --strict` | Broken links and anchors |
| Type check | `uv run pyright src/ tests/` | Strict mode |
| Tests | `uv run pytest` | Python 3.12, 3.13 and 3.14, with the docstring examples (the other checks run on 3.12) |
| Package | `uv build`, then import the wheel in a clean environment (CI jobs `build-package`, `wheel-smoke`) | Pushes to `develop` (dev version) and releases |

Do not silence a lint or type error without a reason; when a trade-off is needed, explain it in
the pull request.

---

## Testing

`pytest` runs the tests in `tests/` and the examples of the docstrings in `src/`
(`--doctest-modules`, `testpaths = ["tests", "src"]` in `pyproject.toml`). The tests never use the
network.

```bash
uv run pytest                   # everything
uv run pytest -m unit           # fast, isolated unit tests only
uv run pytest -m "not slow"     # skip slow tests
```

| Marker | Description |
|---|---|
| `unit` | Quick tests, no I/O, no external dependencies. |
| `contract` | Tests at a boundary, using local stand-ins (temp files, a fake web). |
| `integration` | Tests that talk to real external services (network). |
| `whitebox` | Tests that inspect internal state (use sparingly). |
| `slow` | Long-running tests. |

---

## Labels

| Label | Use |
|---|---|
| `bug`, `enhancement`, `documentation` | What kind of change it is |
| `draft` | A backlog item whose design may still change |
| `in progress` | Being worked on |
| `ready` | A pull request ready for review |

A closed issue is done.

---

## Commit Messages

[Conventional Commits](https://www.conventionalcommits.org/), checked by the `commit-format` CI
job on every non-merge commit of a pull request:

```
<type>(<scope>): <short summary>
```

- `type` is one of `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `test`.
- `scope` is optional and lowercase, e.g. `(fetch)`, `(transform)`, `(report)`, `(cli)`.
- The summary starts with a lowercase letter and does not end with a period. Append `!` after the
  scope to flag a breaking change.
- Reverts use the header `revert: <original header>`.

---

## Documentation

The documentation site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/)
from `docs/`. To preview it:

```bash
uv run mkdocs serve
```

---

## Releasing a New Version

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

1. Make sure `develop` is green.
2. Set `version` in `pyproject.toml` and `__version__` in `src/policy_rate_monitor/__init__.py` to
   `X.Y.Z`, and run `uv lock`.
3. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] — YYYY-MM-DD`, add a new empty
   `## [Unreleased]` above it, and update the links at the bottom.
4. Commit (`build: release X.Y.Z`), push, and open a pull request from `develop` to `main`.
5. Once CI is green, merge it.
6. On GitHub, create a **Release** with a new tag `vX.Y.Z` on `main`, named `X.Y.Z`, with the
   changelog section as its description. Publishing the release starts the CI release run, which
   checks that the tag matches the version in `pyproject.toml`, builds the package and tests the
   wheel.

---

## Reporting Issues

Open an issue on GitHub, or contact the maintainer (see [CREDITS.md](CREDITS.md)).
