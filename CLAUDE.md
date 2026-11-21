# CLAUDE.md

Guidance for Claude Code in this repository.

## Quick Start

```bash
uv sync --group dev                                   # create/refresh the venv
uv sync --locked --only-group lint --no-install-project   # CI-style lint env
```

The venv lives outside the repo at `~/tools/uv/envs/policy_rate_monitor` (set by `.envrc` as
`UV_PROJECT_ENVIRONMENT`, and by `policy_rate_monitor.code-workspace` for VS Code). Editor settings
go in the workspace file, not `.vscode/settings.json` (folder settings override it and VS Code
rejects machine-scoped keys there).

## Checks

```bash
uv run tach check && \
uv run ruff check && \
uv run ruff format --check && \
uv run deptry src/ tests/ && \
uv run pyright src/ tests/ && \
uv run pytest && \
uv run mkdocs build --strict
```

The checks run in this order, each stopping the chain when it fails: layering first, the slowest
(pyright, pytest) last. The pre-commit/pre-push hooks and CI follow the same order.

## Code Style

- Ruff (strict rule set, see `pyproject.toml`), Pyright strict, deptry, tach (module layering in `tach.toml`).
- `from __future__ import annotations`; heavy imports under `TYPE_CHECKING`.
- Google-style docstrings on public API; comments only when the WHY is non-obvious.
- Pytest markers: `unit`, `contract`, `integration`, `whitebox`, `slow`. Docstring `>>>` examples
  under `src/` run as doctests (marked `unit`); modules must import cleanly with no side effects.

## Layout

`src/policy_rate_monitor/` (`core` foundation, `config` above it), `tests/`, `docs/` (MkDocs),
`stubs/` (pyright stubPath), `tmp/` (gitignored scratch).

## Commits

Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`, `perf:`). No
`Co-Authored-By` trailer. Provide `git add` + `git commit` commands for the user to run.

## Agent rules

Every `uv` command is user-run: tell the user the exact command and ask for the output.

## Running Dev Tools (agents)

`housekeeping/` (gitignored, local only) holds the runners agents use instead of `uv run`:

```bash
PY="$HOME/tools/uv/envs/policy_rate_monitor/bin/python"
"$PY" housekeeping/static_checks.py [--overlay DIR] [--no-fix] [TOOL ...] [-- PATH ...]
"$PY" housekeeping/run_tests.py [--overlay DIR] [--tests PATH ...] [--select EXPR]
"$PY" housekeeping/docs_checks.py [--overlay DIR] [mkdocs] [separators]
```
