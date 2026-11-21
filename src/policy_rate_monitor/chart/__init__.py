"""The policy-rate charts: the PNG, the interactive chart and the style they share.

- lines: which series get a line, and their valid rows;
- style: the colours, dashes, line labels and text alternative, with no plotting
  library, so both charts draw the n-th line the same way;
- static: the PNG, with matplotlib (base install);
- interactive: the Plotly figure (the interactive extra). Nothing else in the
  package imports it at start, so the base install never loads Plotly.

See also: [How it works](how-it-works.md), section 10, and [decision
0016](adr/0016-accessible-charts.md).
"""
