# Advanced

For skilled users, reviewers and auditors who need to know how `bis-prates` is built, what it
guarantees and what it does when something goes wrong. Every statement in this tier is traceable to
a file in the repository, which the page names. The pages for a first read are
[How it works](../how-it-works.md) (the problem, the solution and the tests, step by step) and
[Architecture](../architecture.md) (the modules and one run).

## The parts

| Part | What it answers | Start at |
|---|---|---|
| Implementation design | What the system is made of, what each part promises, which files and tables it reads and writes, how it behaves at run time and when it fails, what it costs, how it is installed, observed and defended. | [Implementation design](implementation_design/index.md) |
| Operations | How a run is fed, what it feeds, and the public workflows from entry point to lowest-level call. | [Operations](operations/index.md) |
| Decision records | Why each choice was made, the alternatives rejected, and what each choice costs. | [Decisions](../adr/index.md) |

The decision records are the files in `docs/adr/`, numbered 0001 to 0017 in the order the decisions
were taken. They are listed in the site navigation under Advanced, and their index says which are
built, which are only partly built and which are not built yet. A record is not edited once it is
accepted, apart from its links and a dated update note that points to a later record.

## Why there is no methodology part

A methodology part is a paper: a method, its evaluation and the literature behind it. This project
applies no method of its own that needs one. The calculation is a calendar subtraction and a binary
search over exact integers; the one question that was open, how to find the last observation on or
before a date for every series, was settled by measuring five approaches on the real data
([decision 0006](../adr/0006-twelve-month-look-back.md), and the benchmark in
`diagnostics/README.md`), which [Performance](implementation_design/performance.md) summarises. The
reasons for the other choices are in the decision records, each with its alternatives.

## How the tier relates to the other pages

| Need | Page |
|---|---|
| The data a user sees, and what can go wrong for them | [How it works](../how-it-works.md) |
| Every file and column, and the facts about the BIS file | [Data model](../data-model.md) |
| What the tool checks in the data, and what a finding means | [Data quality](../data-quality.md) |
| Commands, options and exit codes | [CLI reference](../cli.md) |
| Endpoints, jobs and error bodies | [HTTP API](../api.md) |
| Configuration sections and their checks | [Configuration](../configuration.md) |
| Signatures and docstrings | [API reference](../reference.md) |
| What is planned | [Milestones](../milestones.md) |

The pages of this tier do not repeat those pages. They state the contracts, the invariants and the
limits that those pages leave out, and link back for the rest.
