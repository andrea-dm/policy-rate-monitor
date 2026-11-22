# Credits

## Maintainer

- **del Monaco, Andrea** (GitHub: `andrea-dm`)

## Data

- Central bank policy rates (dataflow `WS_CBPOL`), published by the
  [Bank for International Settlements](https://data.bis.org/) (BIS).

## Key dependencies

| Package | Used for | Licence |
|---|---|---|
| [pandas](https://pandas.pydata.org/) | Tables of observations | BSD-3-Clause |
| [pyarrow](https://arrow.apache.org/docs/python/) | CSV reading, exact decimal and date types, parquet files | Apache-2.0 |
| [NumPy](https://numpy.org/) | The fixed-point arrays and searches of the analytics | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) |
| [pydantic](https://docs.pydantic.dev/) | Configuration and domain models, validated at load | MIT |
| [matplotlib](https://matplotlib.org/) | The PNG chart | Matplotlib licence (based on the PSF licence) |
| [Jinja](https://jinja.palletsprojects.com/) | The templates of `report.md` and `report.html` | BSD-3-Clause |
| [Plotly](https://plotly.com/python/) | The interactive chart (`interactive` extra) | MIT |
| [FastAPI](https://fastapi.tiangolo.com/) | The HTTP API (`web` extra) | MIT |
| [pysdmx](https://sdmx.io/tools/pysdmx) | Reads the BIS structural metadata for the SDMX checks (`sdmx` extra) | Apache-2.0 |

Licences as declared in each package's metadata.

Development tools: [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/),
[pyright](https://microsoft.github.io/pyright/), [pytest](https://docs.pytest.org/),
[deptry](https://deptry.com/), [tach](https://github.com/gauge-sh/tach) and
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, quality gates and release procedure.
