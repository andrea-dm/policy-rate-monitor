# diagnostics

Exploratory notebooks about the source data. This is not part of the package: nothing in `src/`
imports from here, and ruff, pyright and tach do not check it.

| Notebook | Purpose |
|---|---|
| `eda_cbpol.ipynb` | EDA of the BIS policy-rate bulk file (`WS_CBPOL`, CSV flat): structure, coding, missing values, duplicates, metadata, calendar density, recency, a prototype of the 12-month look-back, and the design implications (§17). |

## Run

1. Put the unzipped bulk file at `resources/WS_CBPOL_csv_flat.csv`. `resources/` is gitignored.
   Get the file from <https://data.bis.org/bulkdownload> under *Central bank policy rates (CSV, flat)*.
2. Sync the environment (pandas is a runtime dependency; matplotlib and JupyterLab come from the
   `jupyter` group), then start Jupyter from the repository root:

   ```bash
   uv sync --all-groups
   uv run jupyter lab diagnostics/eda_cbpol.ipynb
   ```

The notebook finds the repository root by itself. A full run takes about 30 s, and loading the file
needs about 100 MB of memory.

The notebook is committed **without outputs**, to keep diffs readable and the repository small.
