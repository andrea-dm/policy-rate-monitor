# 0015 The report pages from templates, with an offline chart and a PNG fallback

- **Status:** Accepted
- **Decided:** 2026-11-21 (#12)

## Context

`report.md` was built line by line in `Report._markdown`. #12 adds `report.html` with the same
content and an interactive chart. Written the same way, the HTML page would repeat the layout of
the Markdown one in code.

Plotly is optional (the `interactive` extra, [0003](0003-web-extension-stack.md)), so a base
install cannot draw the interactive chart. The report is opened from a local folder, often
without a network. This record settles the open point of
[0002](0002-charting-and-report-format.md): how `report.html` embeds Plotly's JavaScript.

## Decision

- **Jinja templates**, `report.md.j2` and `report.html.j2` in `src/policy_rate_monitor/templates/`,
  shipped in the package and filled from one context: the summary rows, the release, the method
  lines, the statuses, the data-quality findings and the limitations. The code prepares the values
  and the templates hold the layout. `jinja2` joins the base install, since both pages use it.
- **Autoescaping is on for HTML and off for Markdown** (`select_autoescape(["html.j2"])`). Every
  value from the data or the configuration is escaped in `report.html`; the one value marked safe
  is the chart Plotly writes. The backticks of the method lines become `<code>` through a template
  macro, so no value is marked safe to get them.
- **`report.html` is always written.** With the `interactive` extra it embeds the Plotly figure of
  `charts.rate_figure`, the one `/chart` returns. Without it, it embeds `policy_rates.png` as a
  base64 `data:` URI. `report` imports `charts` inside the function that draws the chart, so the
  base install never imports Plotly.
- **Plotly's JavaScript is embedded in the page** (`include_plotlyjs=True`), so the chart works
  offline and the file can be moved on its own. The chart's height is fixed (`34rem`) because
  Plotly's box takes the height of its parent, which the page leaves unset. Plotly's code holds
  two control characters (U+0001 and U+001A, in the byte signatures it uses to recognise image
  files), which HTML forbids; `report` writes each as its JavaScript escape (`\u001a`), which
  stands for the same character inside a string, so the page is valid HTML.
- **`out/report.html` is committed** with the other outputs. The pre-commit hook
  `check-added-large-files` (2,000 KB) excludes it, and so does `trailing-whitespace`: Plotly's
  bundle has lines ending in spaces, and the hook must not change Plotly's code.
- **The page follows the [HTML Living Standard](https://html.spec.whatwg.org/) and
  [WCAG 2.2](https://www.w3.org/TR/WCAG22/) level AA**, with the [W3C WAI
  tutorials](https://www.w3.org/WAI/tutorials/) for tables, images and figures:
  - `lang="en"`, `<meta charset="utf-8">` first and a viewport meta tag, so the page reads the
    same in every browser and on a phone;
  - landmarks: a `<header>`, a `<main>` and one `<section>` per heading, labelled by it, so a
    screen reader can jump between the parts;
  - each table has a `<caption>` and `<th scope="col">` column headers, and the summary table has
    the country or area name as `<th scope="row">` (the code, for a code the data does not
    have), so each cell is read with its headers; numbers are right-aligned in tabular figures;
  - a wide table scrolls in a box that can take the keyboard focus (`tabindex="0"`, with a role
    and a name), so a keyboard user can scroll it (WCAG 2.1.1);
  - the chart is a `<figure>` with a caption that points to the summary table for the latest and
    prior rates; the PNG's `alt` is the dataset's title, and a full text alternative for the chart
    is left to #29;
  - the generation time is a `<time>` element with a machine-readable `datetime`;
  - the statuses are a description list (`<dl>`), a term and its meaning;
  - text contrast is at least 4.5:1 (body text 15.8:1, the caption 6.1:1) and the table borders
    and the focus ring at least 3:1 (WCAG 1.4.3 and 1.4.11);
  - the styles are in one `<style>` element and use the system fonts, so the page needs no other
    file; the colour scheme is light, matching Plotly's default chart.
- **Emoji beside a status, a flag and a finding's severity**, on both pages, so a reader sees at
  a glance which rows need a look: ✅ ok, ❓ unknown code, 🚫 no valid data, ⚠️ insufficient
  history or a warning, ⏳ a stale rate, ℹ️ information, ❌ an error. One map in `report.py`
  (`ICONS`) gives the emoji of every status, flag and severity, and a test checks none is
  missing.
  - The emoji is never the only cue: the name is always written beside it (WCAG 1.3.3), so a
    reader who does not know an emoji, or whose system does not draw it, loses nothing.
  - The list of statuses shows the same emoji, so it doubles as the key. On both pages it has a
    sub-heading of its own, "Status legend", so a reader, or a screen reader, can jump to it.
  - In the HTML page the emoji is hidden from screen readers (`aria-hidden="true"`), which would
    otherwise read its name before each value. Markdown has no such attribute, so a screen
    reader reads the emoji's name there; the HTML page is the one to use with one.
  - Headings have none, because screen readers list the headings by their text.
- **The Markdown page keeps its text.** The switch to the template changed nothing apart from the
  generation time: a test compared the page with pages the code wrote before the templates, from
  the same inputs. The emoji were added after, in their own change.

## Alternatives

- Keep building the pages in Python: two pages with the same content would repeat the layout.
- `string.Template` from the standard library: no loops, no conditions and no escaping, which a
  table of findings and an HTML page need.
- Convert `report.md` to HTML with a Markdown library: one template only, but another dependency,
  no captions or header scopes in the tables, and Markdown lets raw HTML through, so text from the
  data would not be escaped.
- Skip `report.html` without Plotly: a base install would get no HTML page.
- Plotly in the base install: it reverses 0003's optional extras.
- Plotly's JavaScript from a CDN: a small file, but no chart without a network. A setting to
  choose it is left out until someone needs it.
- A link to the PNG next to the page: the page breaks once moved on its own.
- Do not commit `out/report.html`: a reader of the repository would have to run the pipeline to
  see the page.
- Raise the size limit for every file: it would let any large file through.
- Emoji instead of the status names: a reader would have to learn them, and one that does not
  render would leave the cell empty.
- A dark colour scheme as well: the Plotly chart stays light, so the page would mix the two.

## Consequences

- `report.html` is about 5 MB with the extra (4.6 MB of it Plotly's JavaScript; 6.7 MB for five
  countries or areas over the whole history). Without the extra it is about 60 KB: the PNG in
  base64, a third larger than the file, and a few KB of text.
- `write_report` and the API's report job write five files.
- A new output format is a new template plus a test.
- The full text alternative for the chart (what each line shows) and the colour-blind-safe palette
  belong to #29.

## Evidence

- `src/policy_rate_monitor/report.py` (`Report`, `_interactive_chart`) and
  `src/policy_rate_monitor/templates/`.
- `tests/test_report.py`: the Markdown page against `tests/data/report/full.md` and `empty.md`,
  written by the code before the templates, with the emoji added after; the sections of the HTML page; the escaping; the PNG
  fallback with Plotly blocked; no `<script src>` or `<link>` in the page; every emoji hidden
  from screen readers.
- The contrast ratios, computed with the WCAG 2.2 formula for relative luminance.
- The [W3C Nu HTML Checker](https://validator.github.io/validator/) finds no errors in the page,
  with the interactive chart, with the PNG, and with no release record and no findings. Before
  the escapes, it reported the two control characters in Plotly's code.

## Updates

- 2026-11-22: the code named above has moved. The templates are in
  `src/policy_rate_monitor/report/templates/`; `Report` and `_interactive_chart` are in
  `report/export.py`, and `ICONS` in `report/pages.py`, now a read-only mapping (#34); the
  interactive figure is `chart.interactive.rate_figure`, which `report` imports only when
  Plotly is installed. The full text alternative and the colour-blind-safe palette left to #29
  are built ([0016](0016-accessible-charts.md)).
