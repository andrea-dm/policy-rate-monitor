# Deployment

How the package is built, what it needs, what its optional parts add, and how it is run. The
repository defines a package and a process; it defines no container image, infrastructure code or
service unit. The pipelines that build, test and publish the package are in
[Operations](../operations/index.md).

## Packaging

| Item | Value | Source |
|---|---|---|
| Distribution name | `policy-rate-monitor` | `pyproject.toml`, `[project]` |
| Import package | `policy_rate_monitor` (a `src/` layout) | `tach.toml`, `src/` |
| Build backend | hatchling, with no custom hatch settings | `[build-system]` |
| Python | `>=3.12,<3.15`; tested on 3.12, 3.13 and 3.14 | `requires-python`; the `pytest` matrix of `ci.yml` |
| Licence | Apache-2.0 | `[project]` |
| Command | `bis-prates`, which is `policy_rate_monitor.cli.main:main` | `[project.scripts]` |
| Status | `Development Status :: 3 - Alpha` | classifiers |

The page templates of the report are package data under `report/templates/`, loaded with Jinja's
`PackageLoader`, so they travel with the package. The configuration is **not** part of the package
([decision 0007](../../adr/0007-config-driven-design.md)): the shipped `configs/bis_cbpol.json` and
`configs/config.schema.json` are files of the repository, and the default path
`configs/bis_cbpol.json` is relative to the folder the command runs in. A user of an installed
package who does not run from a clone passes `--config` or sets `BIS_PRATES_CONFIG`.

On a push to `develop` and on a published release, CI builds the package with `uv build`, installs
the wheel into an isolated environment outside the checkout, imports it, and checks that the
installed version is the version in `pyproject.toml` (with `.dev<run number>` appended on
`develop`) and that `policy_rate_monitor.__version__` equals the `pyproject.toml` version
(`build-package` and `wheel-smoke` jobs of `.github/workflows/ci.yml`, which run when `route` sets
`publish`). Pull requests do not run it. CI runs on `ubuntu-latest` only; no other platform is
tested by it.

## Dependencies

Runtime dependencies, which a plain install brings:

| Package | Used for |
|---|---|
| `pydantic`, `pydantic-core` | the domain models, the configuration models, the JSON Schema |
| `pandas`, `pyarrow` | the tables, parquet files, exact decimals |
| `numpy` | the fixed-point arrays of `analytics` |
| `matplotlib` | the PNG chart |
| `jinja2` | the report pages |

Each has a lower bound in `pyproject.toml`; versions are locked in `uv.lock`, and `deptry` (a
pre-push hook and a CI job) checks that the declared dependencies match the imports.

### Extras

| Extra | Adds | Needed for | Without it |
|---|---|---|---|
| `fast` | `numba` | compiled search loops in `analytics` | the same functions run on NumPy, with the same results |
| `interactive` | `plotly` | `chart.interactive`: the Plotly figure, and the interactive chart in `report.html` | `report.html` embeds the PNG as an image |
| `web` | `fastapi`, `starlette`, `uvicorn`, and `interactive` | `bis-prates serve` and the `web` module | `serve` prints `error: bis-prates serve needs the web extra: install policy-rate-monitor[web]` and exits 2 |
| `sdmx` | `pysdmx`, `msgspec` | the `sdmx` module: the SDMX rules `ref_area_not_in_code_list` and `dataflow_mismatch` | each enabled SDMX rule reports "not evaluated: the sdmx extra is not installed: install policy-rate-monitor[sdmx]"; `transform` still exits 0 |

The base layers never import an extra: the `numba` import is in a `try` in `analytics`, `plotly` is
imported inside the function that draws the HTML chart, `web` only inside `serve`, and `sdmx`
only by `transform` and `serve` when an SDMX rule is enabled
([System overview](system_overview.md#the-layering-rule)).

### Dependency groups

Groups are for development and are not installed by a user of the package.

| Group | Content |
|---|---|
| `lint` | deptry, ruff, pyright, tach, the pandas and pyarrow stubs |
| `test` | pytest, pytest-mock, pytest-cov, hypothesis, httpx2 (FastAPI's test client) |
| `dev` | `lint` and `test` |
| `jupyter` | JupyterLab and a kernel, for the notebooks in `examples/` |
| `docs` | MkDocs Material, mkdocstrings, griffe |
| `diagnostics` | Cython, DuckDB, setuptools: only the scripts in `diagnostics/` import them |

The project's checks need every group and every extra
(`uv sync --all-groups --all-extras`), because the tests and the type checker cover the optional
parts. A plain `uv sync` installs the `dev` group; `uv sync --no-dev` installs the package and its
runtime dependencies only.

## Installing

```bash
uv sync --no-dev                       # from a clone: the package and its runtime dependencies
uv sync --no-dev --extra web           # with the HTTP API
uv sync --no-dev --extra fast          # with Numba
uv sync --no-dev --extra sdmx          # with the SDMX checks (pysdmx)
pip install './policy_rate_monitor-<version>-py3-none-any.whl[web]'  # the wheel of a GitHub release
```

`uv.lock` is resolved for Linux only (`[tool.uv] environments` in `pyproject.toml`), so `uv sync`
from a clone works on Linux; the wheel of a release is `py3-none-any` and installs with pip
elsewhere, but no other platform is tested.

The package is not on a package index: the job that would publish it to PyPI is off until the
repository variable `PYPI_PUBLISH` is set
([Release engineering](../operations/release_engineering.md#what-the-release-run-does)). Each
GitHub release carries the wheel and the source archive.

The installation steps are in the
[README](https://github.com/andrea-dm/policy-rate-monitor#readme).

## Running

### As a pipeline

```bash
bis-prates fetch && bis-prates transform
bis-prates report --countries "US,EA,GB,JP,CH" --out out
```

The data directory (`data/` by default) and the output folder are created on the first write; the
process needs write permission on both. A scheduler can use the exit codes to decide on a retry
(only 6 means "try again later"); an example is in
[How it works, section 11](../../how-it-works.md#11-run-it-from-a-script-or-a-scheduler-2).

### As a service

```bash
export BIS_PRATES_CONFIG=/etc/bis-prates/config.json
export BIS_PRATES_DATA_DIR=/var/lib/bis-prates
bis-prates serve --host 127.0.0.1 --port 8000 --out /var/lib/bis-prates/out
```

What the process needs and what it does:

- **One process.** The jobs and the staged-data cache are in the memory of the process; several
  uvicorn workers would each have their own ([Runtime behaviour](runtime_behavior.md#the-api-process)).
- **Network.** It listens on `--host` and `--port` (default `127.0.0.1:8000`); the `fetch` job
  needs outbound HTTPS to the BIS site.
- **Disk.** It reads `data/staged/` and writes `data/raw/`, `data/staged/` (through the jobs) and
  `--out`. The three can be on different volumes. With the `fast` extra, Numba also writes its
  compiled-code cache on the first calculation (`analytics.py`, `njit(cache=True)`): beside the
  package in `__pycache__`, or in its own cache folder when that one cannot be written.
- **First use.** With nothing staged, the read endpoints answer `409 missing_input`; run
  `POST /jobs/fetch` then `POST /jobs/transform`.
- **Stopping.** Ctrl+C (SIGINT) or `kill <pid>` (SIGTERM) lets the running job finish, since
  uvicorn handles both signals alike, and the process then ends with status 130 or 143, not 0;
  SIGKILL (`kill -9`) does not let the job finish
  ([Runtime behaviour](runtime_behavior.md#start-and-stop)).
- **Exposure.** There is no authentication and no CORS header: keep the default address or put a
  proxy that checks who calls in front ([Security](security.md#the-apis-exposure)).

### Settings

Everything is taken from the command line, two environment variables and the configuration
([CLI reference](../../cli.md#where-the-options-come-from)). No other environment variable is read
by the package, and there are no secrets to supply.

| Setting | Flag | Variable | Default |
|---|---|---|---|
| Configuration file | `--config` | `BIS_PRATES_CONFIG` | `configs/bis_cbpol.json` |
| Data directory | `--data-dir` | `BIS_PRATES_DATA_DIR` | `dataset.data_dir` (`data`) |
| Output folder | `--out` | - | `out` |
| Address and port of the API | `--host`, `--port` | - | `127.0.0.1`, `8000` |

## Compatibility notes

- With Numba installed, the first call of the two compiled searches compiles them (0.68 s) and
  caches the compiled code on disk (0.17 s in later processes) ([Performance](performance.md)).
- A staged layer built with another configuration is refused by `BulkFileProvider.observations`
  with a message to run `transform` again; there is no migration.
- The package declares no upper bounds on its dependencies other than Python's; `uv.lock` fixes
  the versions the project is tested with.
