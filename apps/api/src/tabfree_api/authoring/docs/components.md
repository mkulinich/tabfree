# Components (index.html)

```html
<tf-dashboard>
  <tf-filter name="region" control="dropdown"></tf-filter>
  <tf-parameter name="top_n"></tf-parameter>
  <tf-kpi query="kpis" measure="Sales"></tf-kpi>
  <tf-chart query="by_region" mark="bar" x="Sales" y="Region" color="Profit Ratio"></tf-chart>
  <tf-chart query="trend" mark="line" x="Month" y="Running Sales"></tf-chart>
  <tf-crosstab query="top_customers"></tf-crosstab>
</tf-dashboard>
```

Attributes name queries, filters and parameters from `tabfree.json`, and output columns of those queries. `validate_dashboard` (or `tabfree-validate`) checks every reference.

- **Charts:** a dimension on one axis and a measure on the other gives bars, lines or areas; the bars are horizontal when the dimension is on `y`. Two measures give a scatter plot.
- **Colour:** `color` takes a dimension (one series each, with a legend) or a measure (shaded marks).
- **Interaction:** clicking a mark or a table row reports a selection, and the host runs the manifest's filter and highlight actions. Clicking it again clears the selection.

### Component reference

| Element | Attributes |
|---|---|
| `<tf-dashboard>` | The root element. It applies the theme and resizes the frame to fit. `size="automatic"` (default) fills the frame; `size="fixed" width height` keeps Tableau's fixed size and scrolls. |
| `<tf-container>` | A tiled layout container: `direction="horizontal"` (default) or `vertical`, `gap` (px). Children share the space by their `weight` attribute (default 1). |
| `<tf-float>` | A floating object: `x`, `y`, `width`, `height` in px within the dashboard. |
| `<tf-chart>` | `query`; `mark` = `bar` \| `line` \| `area` \| `point`; `x` and `y` (output columns, table-calc outputs included); optional `color` (a dimension gives series, a measure gives shading); `height` (px); `title`; `sort` = `asc` \| `desc` (by the measure; otherwise the query's row order); `drill` = a query to show in place after a mark is clicked (a manifest filter action from this query to it does the narrowing; ◀ Back returns). |
| `<tf-crosstab>` | `query`, `max-rows` (default 1000), `title`, `columns` (a dimension pivoted across, with the measures under each member). Repeated row-header values are merged; the query's `totals` show as Total and Grand Total rows after their group. Click a header to sort (flat tables); click a row to select its dimensions. |
| `<tf-legend>` | `query`, `field` (the chart's `color` dimension), `title`. Same colours as the chart; click a member to select it. |
| `<tf-text>` | Text object. With `query`, `{Column}` placeholders are filled from the first row, as text. |
| `<tf-image>` | `src` (a `data:image/...;base64,` URL only), `alt`, `fit` = `contain` \| `cover` \| `fill`. |
| `<tf-export>` | A download button: `format` = `csv` \| `crosstab` (with `query`) \| `png` \| `pdf`, `label`. The portal checks the viewer's download capabilities. |
| `<tf-kpi>` | `query`, `measure`, optional `label` and `change`. `change` names a ratio column, shown as ▲ above +2%, ▼ below −2% and ■ in between, always as `0.0%`. |
| `<tf-map>` | `query`, `geo` (a state column: names or postal codes), `color` (a measure), `title`. Click a state to select it. |
| `<tf-filter>` | `name` (a filter in `tabfree.json`); `control` = `dropdown` \| `radio` \| `slider` (one member) \| `checklist` \| `multi-dropdown` (several) \| `search` (type-in). Range filters take `control="date"` for a date range; relative-date and wildcard filters pick their own control. |
| `<tf-parameter>` | `name`. A list gives a dropdown, a range gives a slider, and anything else gives an input box. |

**Gotchas from the P1.15 pilot:**
- **Top N and selections:** a `top_n` filter ranks before dimension filters and filter actions, as in Tableau, so "top 10 within the clicked state" needs `sort` + `limit`, or a context filter.
- **Filters and FIXED LODs:** filters that FIXED LODs or Top N should respect need `"context": true`.
- **`appliesTo`:** it is `"all"` or an explicit list of queries; there is no "all except".
- **Filter actions:** `fields` names output columns of the source query (as `source`) and model fields (as `target`). With `clear: "show_all"` (the default), clearing a selection removes the filter; `keep_filter` keeps it.
