# policy-rate-monitor

`bis-prates` downloads the central bank policy rates published by the Bank for International
Settlements (BIS), stages them as an exact, validated dataset, and reports for a list of countries
or economic areas the latest policy rate, the rate 12 months earlier and the change in basis
points, with a chart and a short report, from the command line or over an HTTP API.

## Quick start

=== "CLI"

    ```bash
    uv sync --no-dev
    uv run bis-prates fetch
    uv run bis-prates transform
    uv run bis-prates report --countries "US,EA,GB,JP,CH" --start 2015-01-01
    ```

    The five files land in `out/`: `summary.csv`, `summary.json`, `policy_rates.png`,
    `report.md` and `report.html`. Every command and option is in the [CLI reference](cli.md).

=== "Jupyter Notebook"

    ```bash
    uv sync --all-groups --all-extras
    uv run jupyter lab
    ```

    In a notebook opened at the repository root, download and stage the data:

    ```python
    from pathlib import Path

    from policy_rate_monitor.bulk import BulkFileProvider
    from policy_rate_monitor.config import FileConfigSource

    config = FileConfigSource("configs/bis_cbpol.json").load()
    provider = BulkFileProvider(config, Path(config.dataset.data_dir))
    provider.fetch()
    provider.transform()
    ```

    Compute the 12-month change:

    ```python
    import pandas as pd

    from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots

    observations = provider.observations().to_pandas(types_mapper=pd.ArrowDtype)
    series = [provider.series_metadata(key) for key in provider.list_series()]
    snapshots = compute_snapshots(
        SnapshotArgs(
            observations=observations,
            series=series,
            requested=["US", "EA", "GB", "JP", "CH"],
            settings=config.snapshot,
            ref_area_aliases=config.ref_area_aliases,
        )
    )
    pd.DataFrame([snapshot.model_dump(exclude={"series"}) for snapshot in snapshots])
    ```

    Plot the rates since 2015:

    ```python
    from datetime import date

    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(figsize=(10, 5.5), layout="constrained")
    for snapshot in snapshots:
        if snapshot.series is None:
            continue
        rows = observations[
            (observations["series_key"] == snapshot.series.series_key)
            & observations["is_valid"]
            & (observations["date"] >= date(2015, 1, 1))
        ]
        axes.step(
            rows["date"],
            rows["value"].astype(float),
            where="post",
            label=snapshot.series.ref_area_name,
        )
    axes.set_ylabel("Per cent")
    axes.legend()
    ```

    The same steps, with outputs, are in the notebooks in `examples/`: `fetch_and_transform.ipynb`,
    then `policy_rates.ipynb`.

## Where to read next

| To | Read |
|---|---|
| Understand what each part does for the user, what can go wrong and how it is tested | [How it works](how-it-works.md) |
| See how the code is organised | [Architecture](architecture.md) |
| Know what is stored, in which file, and what the BIS data looks like | [Data model](data-model.md) |
| Know what the tool checks in the data, what a finding means and what to do about it | [Data quality](data-quality.md) |
| Run the commands, with every option and exit code | [CLI reference](cli.md) |
| Call the same steps over HTTP: the endpoints, the jobs and the errors | [HTTP API](api.md) |
| Change or write a dataset configuration | [Configuration](configuration.md) |
| Use the package from Python or a notebook | [API reference](reference.md#use-from-python) |
| Look up a term | [Glossary](glossary.md) |
| Know why it was built this way | [Decisions](adr/index.md) |
| Check how it is built, what it guarantees, how it fails and how to operate it | [Advanced](advanced/index.md) |
| See what is planned | [Milestones](milestones.md) |

Installation, development and testing are in the
[README](https://github.com/andrea-dm/policy-rate-monitor#readme).
