# P1.15 authoring eval: pilot of the HTML + `<tf-*>` arm (PRELIMINARY)

**Date:** 2026-11-20. **Status:** a pilot only.

BUILD_PLAN P1.15 compares HTML + components with a JSON spec, and Vega-Lite with ECharts, over 20 corpus dashboards with visual-similarity scoring. This pilot ran **only the HTML + components arm**, on 3 dashboards with 11 scripted change requests. It measured whether Claude can make realistic edits safely and what gets in the way. ADR-002 stays "Accepted — pending validation".

## Setup

- **Dashboards:** the three P1.12 dashboards (`sales-overview`, `dimensional-hierarchy`, `sales-analysis`), as scratch copies.
- **Authors:** three independent Claude agents, one per dashboard, acting as the dashboard author.
- **Inputs:** each read the runtime README, the manifest schema and the semantic model. Each could edit only `tabfree.json` and `index.html`.
- **Check:** after every change, the agent ran a checker that validates the bundle (`tabfree-validate`: every `<tf-*>` reference against the manifest) and runs every query and filter-member list on local Superstore data.
- **Recording:** each agent wrote down its outcome and friction in `RESULTS.md`.

Change requests:

| Dashboard | Requests |
|---|---|
| sales-overview | 1. KPI tile for Quantity. 2. Monthly → quarterly trend, keeping the running total. 3. Segment filter on everything. |
| dimensional-hierarchy | 4. Profit by Region bar chart, sorted. 5. Top 50 products plus a Profit Ratio column. 6. Ship Mode filter on all but one chart. 7. Clicking a region filters products. |
| sales-analysis | 8. A fifth BAN (Profit, current vs previous). 9. Orders-by-month line. 10. Clicking a group bar filters top products. 11. Map coloured by Orders. |

## Results

- **11 of 11 requests were completed.** Every change passed the checker on the first run. None needed custom JavaScript, and none touched SQL or the semantic model: new logic went into query `expr` measures in Tableau formula syntax.
- **Cost:** about 111k, 116k and 129k tokens per dashboard (3–4 requests each), including reading the docs. Per request that is roughly 30–40k tokens, most of it reading context.
- **The checker was too weak, which is the main finding.** It runs queries with no filter values, no defaults, no selections and no table calcs. As a result, three real problems passed it, and the agents found them only by running extra queries:
  - **Top N with filter actions.** Clicking a state or group intersected the *overall* top 10 with the selection, giving 0–3 rows. This is Tableau's order of operations (Top N before dimension and action filters), faithfully reproduced, and it broke the P1.12 `sales-analysis` map action as shipped.
  - **Top N with a non-context filter.** A Segment filter returned 4 of the top 10; it needed `context: true`.
  - **Defaults.** With the dashboard's own default date filter, the trend returned 0 rows on the checker's anchor date.
- **Smaller issues found:**
  - Quarterly axes were labelled as months.
  - `control` values were not validated, so a typo fell back silently.
  - Action field mappings were not validated (`"Grp"` passed).
  - `clear: keep_filter` behaved like `show_all`, and its schema default differed from Tableau's ("show all values").
  - The README did not document `<tf-kpi>`, `<tf-map>` or `control` values.
  - The map tooltip shows only the colour measure.
  - A ±2% "flat" threshold on KPI changes was hard-coded and undocumented.

## Fixed in this round

- **`sales-analysis`:** top products now rank within the current selection (`sort` + `limit` and a measure filter) instead of a Top N filter.
- **Validator:** it now checks `control` values on `<tf-filter>` and `<tf-parameter>`, and that every action field is an output of its source query.
- **`keep_filter`:** the host keeps the last selection for `keep_filter` actions. The schema default is now `show_all`, matching Tableau's default for new filter actions.
- **Quarter labels:** quarterly series read "2023 Q2".
- **README:** a component reference covering all attributes, plus the Top N, context-filter, `appliesTo` and action-field gotchas.

## What the full eval must still do

1. **Checker:** apply manifest defaults, sample filter values and each declared action's selections, run the table calcs, and fail on empty or degenerate results. Without this, "passes the checker" overstates success (R5).
2. **The JSON-spec arm,** for the comparison ADR-002 needs: the same requests against a JSON dashboard spec, with tokens and success measured the same way.
3. **Vega-Lite vs ECharts** behind `renderChart`, with visual similarity against Tableau PNGs. This needs P1.5 ground truth, so it waits for the Tableau Server.
4. **Scale:** 20 corpus dashboards, and authoring from scratch as well as edits.
5. **Schema and runtime features the requests wanted:**
  - `appliesTo` "all except"
  - a tooltip attribute on `<tf-map>`
  - a configurable change threshold
  - a date-label format on `<tf-chart>`
