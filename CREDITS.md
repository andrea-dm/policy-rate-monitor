# Credits

## Maintainer

- **del Monaco, Andrea** (GitHub: `andrea-dm`)

## Data

- Central bank policy rates (dataflow `WS_CBPOL`), published by the
  [Bank for International Settlements](https://data.bis.org/) (BIS).

## Key dependencies

| Package | Used for |
|---|---|
| [pandas](https://pandas.pydata.org/) | Tables of observations |
| [pyarrow](https://arrow.apache.org/docs/python/) | CSV reading, exact decimal and date types, parquet files |
| [pydantic](https://docs.pydantic.dev/) | Configuration and domain models, validated at load |
| [matplotlib](https://matplotlib.org/) | The chart in the report |

Development tools: [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/),
[pyright](https://microsoft.github.io/pyright/), [pytest](https://docs.pytest.org/),
[deptry](https://deptry.com/), [tach](https://github.com/gauge-sh/tach) and
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, quality gates and release procedure.
