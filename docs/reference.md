# API reference

The public classes and functions of the package, generated from their docstrings. The examples in
the docstrings run as tests, so they are kept true.

## Use from Python

The package can be used without the command line, for example in a notebook. Two example
notebooks, which need the `jupyter` dependency group, do what the commands do:

- [`examples/fetch_and_transform.ipynb`](https://github.com/andrea-dm/policy-rate-monitor/blob/develop/examples/fetch_and_transform.ipynb)
  downloads the BIS file and stages it, as `fetch` and `transform` do;
- [`examples/policy_rates.ipynb`](https://github.com/andrea-dm/policy-rate-monitor/blob/develop/examples/policy_rates.ipynb)
  loads the staged data through the provider, computes the 12-month change and plots the rates,
  as `report` does.

The same steps as a script:

```python
from pathlib import Path

import pandas as pd

from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots

config = FileConfigSource("configs/bis_cbpol.json").load()
provider = BulkFileProvider(config, Path(config.dataset.data_dir))
provider.fetch()      # downloads the BIS file, unless the cached copy is recent
provider.transform()  # stages it

# Arrow types kept, so the rates stay exact decimals.
observations = provider.observations().to_pandas(types_mapper=pd.ArrowDtype)
series = [provider.series_metadata(key) for key in provider.list_series()]

snapshots = compute_snapshots(
    SnapshotArgs(
        observations=observations,
        series=series,
        requested=["US", "EA"],
        settings=config.snapshot,
        area_aliases=config.area_aliases,
    )
)
for snapshot in snapshots:
    print(snapshot.ref_area, snapshot.latest_value, snapshot.change_bps, snapshot.status)
```

## Domain models and errors

::: policy_rate_monitor.core.models

::: policy_rate_monitor.core.errors

## Configuration

::: policy_rate_monitor.config.source

::: policy_rate_monitor.config.models

::: policy_rate_monitor.config.parsing

::: policy_rate_monitor.config.schema

## Data providers

::: policy_rate_monitor.providers.base

::: policy_rate_monitor.providers.memory

::: policy_rate_monitor.providers.schema

::: policy_rate_monitor.bulk

## Pipeline steps

::: policy_rate_monitor.fetch

::: policy_rate_monitor.transform

## Analytics and snapshot

::: policy_rate_monitor.analytics

::: policy_rate_monitor.snapshot

## Report

::: policy_rate_monitor.report

## Command line

::: policy_rate_monitor.settings

::: policy_rate_monitor.cli.main

::: policy_rate_monitor.cli.exit_codes
