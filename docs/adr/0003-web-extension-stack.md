# 0003 The web extension: a FastAPI service and a small React front end

- **Status:** Accepted; the API is built (M6, #27, [0014](0014-http-api.md)), the front end is still to come (M8)
- **Decided:** 2026-11-17; packaging as optional extras on 2026-11-18

## Context

Extension 1 of the brief asks for a web interface that drives the workflow: choose areas, run the
retrieval, the transformation and the report, see the status and the errors, a table of the latest
rates and an interactive chart. The brief prefers React, and says the panel looks at how the front
end and the back end are integrated and kept apart.

The owner knows Python well and can handle a little JavaScript, but is not a React developer.

## Decision

- **Two parts with one boundary.** A FastAPI service (M6) exposes over HTTP what the CLI does: the
  available areas, the summary for a list of areas, the time series of one area, the chart, and
  `fetch`, `transform` and `report` as background jobs with a status endpoint. A React front end (M8) calls
  only that API.
- **The front end computes nothing.** Every number and every chart comes from the API. The chart
  is a Plotly figure built in Python ([0002](0002-charting-and-report-format.md)) and drawn in the
  browser with `react-plotly.js`.
- **Small and plain.** React with Vite, in plain JavaScript, with a few small components: no
  TypeScript, no state library, no router.
- **Where it lives.** The front end is in `web/` at the repository root, with its own toolchain
  (Node, `package.json`); it is not part of the Python package, and its build output is not
  committed.
- **Optional extras.** The base install runs `fetch`, `transform` and `report`. The API comes with
  the `web` extra (FastAPI, Starlette, uvicorn), which includes `interactive` (Plotly). A module that needs an
  extra lives in its own sub-package that nothing in the base layers imports, and the CLI imports
  such a module only inside the command that needs it, with a clear message when the extra is
  missing.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Plotly Dash | Python only: no React code to show, and it hides the boundary between front end and back end that the panel looks at |
| TypeScript | Extra friction for a developer who is not a React expert, for a few small components |
| Charts drawn in JavaScript (Recharts) | A second definition of each chart, which can drift from the report's |
| One milestone for API and UI | Larger pull requests; separate milestones make the API the contract and the UI its consumer |
| The front end inside `src/` | Mixes a Node toolchain into the Python package |
| Everything in the base install | A CLI user would install FastAPI and uvicorn for nothing |

## Consequences

- The API can be used and tested without the UI, against the in-memory provider.
- The HTTP API and the CLI must give the same numbers on the same data; an API test checks it.
- A server should prepare the analytics arrays and compile the searches once at start-up
  ([0013](0013-fast-analytics-numpy-numba.md)). The first API does not: it builds the arrays per
  request, and caching them waits for a front end that needs it ([0014](0014-http-api.md)).

## Evidence

- `docs/milestones.md`, M6 and M8.
- `pyproject.toml`: the `fast` extra shows the pattern the `web` and `interactive` extras follow.

## Updates

- 2026-11-21 (#27): the API is built as decided: FastAPI with the `web` and `interactive`
  extras, the same pieces as the CLI through a shared `service` module, and `bis-prates serve`
  importing it lazily. The details (routes, jobs, errors, what is cached, why no CORS) are in
  [0014](0014-http-api.md). The chart is returned as a Plotly figure in JSON, as decided here;
  the front end (M8) is not started.
