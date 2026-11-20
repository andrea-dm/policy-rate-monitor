# policy-rate-monitor

`bis-prates` downloads the central bank policy rates published by the Bank for International
Settlements (BIS), stages them as an exact, validated dataset, and reports for a list of countries
or areas the latest policy rate, the rate 12 months earlier and the change in basis points, with a
chart and a short report.

```bash
uv sync --no-dev
uv run bis-prates fetch
uv run bis-prates transform
uv run bis-prates report --countries "US,EA,GB,JP,CH" --start 2015-01-01
```

The four files land in `out/`: `summary.csv`, `summary.json`, `policy_rates.png` and `report.md`.

## Where to read next

| To | Read |
|---|---|
| Understand what each part does for the user, what can go wrong and how it is tested | [How it works](how-it-works.md) |
| See how the code is organised | [Architecture](architecture.md) |
| Know what is stored, in which file, and what the BIS data looks like | [Data model](data-model.md) |
| Run the commands, with every option and exit code | [CLI reference](cli.md) |
| Change or write a dataset configuration | [Configuration](configuration.md) |
| Use the package from Python | [API reference](reference.md) |
| Look up a term | [Glossary](glossary.md) |
| Know why it was built this way | [Decisions](adr/index.md) |
| See what is planned | [Milestones](milestones.md) |

Installation, development and testing are in the
[README](https://github.com/andrea-dm/policy-rate-monitor#readme).
