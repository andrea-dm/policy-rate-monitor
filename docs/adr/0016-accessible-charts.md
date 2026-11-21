# 0016 Accessible charts: tableau-colorblind10, dash patterns and line labels, shared by both charts

- **Status:** Accepted
- **Decided:** 2026-11-21 (#29)

## Context

`policy_rates.png` (matplotlib) and the Plotly chart (`/chart` and `report.html`) used each
library's default colours (`tab10` and Plotly's colorway). Colour was the only cue that told the
lines apart, several pairs look alike with a colour vision deficiency, the two charts gave the same
line different colours, and the chart had no text alternative beyond the dataset title. About one
man in twelve has a red-green deficiency.

WCAG 2.2 asks for a contrast of 3:1 against the background for the parts of a chart needed to read
it (1.4.11), for a cue other than colour (1.4.1), and for a text alternative (1.1.1).

## Decision

- **One style module, `chart.style`, with no plotting library in it,** used by both charts, so the
  base install and the `interactive` extra draw the n-th line the same way:
  - `PALETTE`, the line colours, `LABEL_COLOURS`, the colours of their labels, and `DASHES`,
    the dash patterns;
  - `line_style(n)`, the colour and dash of the n-th charted line;
  - `alt_text(...)`, the text alternative; `label_positions(...)`, where the line labels go;
  - `NO_DATA`, the message of a chart with nothing to draw.
- **The palette is matplotlib's `tableau-colorblind10`, without the colours under 3:1 on
  white,** in this order: blue `#006BA4`, dark orange `#C85200`, mid grey `#898989`, dark grey
  `#595959`. The six others (orange, light grey, light blue, pale blue, peach, pale grey; 1.56:1
  to 2.88:1) are left out. The order keeps the first lines furthest apart. The palette ships with
  matplotlib, which the base install already uses, and was designed by Tableau for colour vision
  deficiencies.
- **Six dash patterns** after the colours: solid, dash, dot, dash-dot, long dash, long dash-dot,
  defined once as on-off lengths in pixels, so matplotlib and Plotly draw the same dashes. A line
  takes the next colour, and the next dash once the four colours are used: the first **24 lines**
  of a chart all differ in colour or dash, and the default request (five) draws its fifth line in
  blue, dashed. Beyond 24 the pairs repeat; a chart with that many lines cannot be read anyway,
  and the summary table carries the figures.
- **Lines 2 px wide.**
- **Each line is named at its right end,** next to its latest rate, in its colour, besides the
  legend, so a reader does not have to match colours. Labels closer than a minimum gap are
  pushed apart, in the order of the rates. The gap is 5 % of the y axis in the PNG, and 5 % of
  the spread of the latest rates in the Plotly figure, whose axis is only known once the browser
  draws it. A label is text, so it needs 4.5:1
  (WCAG 1.4.3), not the 3:1 of a line: the mid grey (`#898989`, 3.5:1) writes its label in
  `#767676` (4.54:1), the lightest grey that passes; the three other colours already pass
  (dark orange 4.50:1, blue 5.77:1, dark grey 7.00:1).
- **A text alternative** that names the chart type, the countries or economic areas and the
  period: the `alt` of the PNG in `report.md`, the PNG's `Description` metadata, the caption of
  the chart's `<figure>` in `report.html`, and `layout.meta.description` of the figure `/chart`
  returns, for the front end. In `report.html` the text is a visible caption, which also names the
  figure for a screen reader, for the interactive chart and the PNG alike; the PNG's `alt` there
  stays the dataset's title, so a screen reader does not read the same sentence twice. An
  ARIA `role="img"` around the Plotly chart was not used: it would hide the chart's own controls
  from assistive technology.
- **The Plotly hover** shows the date and the rate with the series' decimals, so a value can be read
  without judging a colour.
- **An empty chart says so** in both libraries, with the same `NO_DATA` text.
- **The palette stays in code.** It is a style choice, not a fact of the dataset; a dataset that
  needs its own colours can get an `output.chart.palette` in the configuration when one does.

## How the palette was chosen

Each candidate was simulated for protanopia, deuteranopia and tritanopia at full severity, with
the matrices of Machado, Oliveira and Fernandes (2009) applied in linear RGB. The distance between
two colours is CIEDE2000 (ΔE00) in CIELAB; about 10 or more reads as clearly different. The
contrast is the WCAG 2.2 ratio against white.

| Candidate | Colours at 3:1 or more | Worst ΔE00 under the three simulations |
|---|---|---|
| matplotlib tableau-colorblind10 (10) | 4: blue, dark orange, mid grey, dark grey | 18.9 (deuteranopia, the two greys) |
| seaborn colorblind (10) | 5: blue, vermillion, grey, pink, green | 9.2 (deuteranopia) |
| seaborn muted (10) | 5: blue, red, purple, brown, grey | 4.7 (protanopia) |

Darkening the pale colours to 3:1 instead of leaving them out makes them collapse into the others:
the worst ΔE00 falls to 0 for tableau-colorblind10 (its two light greys become the same grey),
2.8 for seaborn colorblind and 2.3 for seaborn muted.

In the chosen order, the worst ΔE00 over the four visions (normal and the three simulations) is
47.8 for the first two lines, 22.5 for three and 18.9 for four: the four colours stay clearly
apart for every vision tested, the widest margin of the candidates.

tableau-colorblind10 was chosen because it ships with the plotting library the project already
uses and keeps the widest margin; it costs fewer colours (four), which the dashes make up for.
seaborn colorblind keeps five colours, but two of them fall under 10 with deuteranopia; seaborn
muted is not made for colour vision deficiencies.

## Alternatives

- Each library's own colour-blind option, matplotlib's `tableau-colorblind10` style in matplotlib
  and Plotly's `Safe` colorway in Plotly: the two lists differ, so the charts would still not
  match. The chosen colours are given to both libraries instead.
- seaborn colorblind and muted: see above.
- The pale colours darkened to 3:1: they become hard to tell apart.
- Markers instead of dashes: with daily data, markers merge into a thick band.
- Ten dash patterns (40 lines): the later patterns cannot be told apart.
- Four dash patterns (16 lines): two fewer distinct patterns for no gain in readability.
- A fixed colour per country or economic area: 49 series cannot each get a distinct, colour-blind-safe
  colour; the colour follows the position in the request.
- A palette in the configuration now: no dataset needs one yet.

## Consequences

- The PNG, the Plotly chart and `report.html` give the n-th line the same colour and dash.
- The charts look different from before; `out/` is regenerated.
- The figure `/chart` returns has the line labels as annotations, the legend below the plot, and
  `layout.meta.description`.

## Evidence

- `src/policy_rate_monitor/chart/style.py`.
- `tests/chart/test_style.py`: every colour at 3:1 or more on white; the worst ΔE00 under the
  three simulations; 24 distinct lines; the label positions; the text alternative.
- `tests/chart/test_interactive.py` and `tests/test_report.py`: both charts draw the n-th line with
  `line_style(n)`, label the line ends, say `NO_DATA` when empty, and carry the text alternative.
