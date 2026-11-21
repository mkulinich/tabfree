# P1.12: dashboards recreated from Tableau workbooks

Phase 1 proves the loop that migrates a Tableau dashboard by hand:
1. Parse the workbook to the IR (`tabfree-migrate parse`).
2. Write a semantic model and a `tabfree.json` from the IR.
3. Write `index.html` with `<tf-*>` components.
4. Check the numbers.

The plan calls for three dashboards from our own Tableau Server, checked against PNG and CSV ground truth from P1.5. That needs the server, so this round uses public corpus workbooks instead, with Tableau's Sample - Superstore as data (`corpus/derived/superstore`). Superstore's totals are published, which gives the numbers a reference to check against.

| TabFree dashboard | Recreated from | Shows | Ground truth checked |
|---|---|---|---|
| [`dimensional-hierarchy`](../../packages/schemas/examples/dashboards/dimensional-hierarchy) | *Dimensional Hierarchy Example*, `Dimensional-Flat example Hierarchy Navigator_v2018.2.twbx` | FIXED LOD total, LOD share of category, percent-of-total and rank table calcs, drill-down filter actions, average discount by segment and ship mode | Total sales $2,297,200.86; Technology $836,154, Furniture $741,999, Office Supplies $719,047; Furniture profit ratio 2.49%; the FIXED total ignores filters and drills |
| [`sales-analysis`](../../packages/schemas/examples/dashboards/sales-analysis) | *Sales Analysis*, `eBike Sales Analysis.twbx` | Prorated current-vs-previous month BANs with ▲/▼ change, a date parameter, a View By parameter (`CASE` on a parameter), a 12-month trend with a moving average, a state map with a filter action, Top N products | BANs and % change match an independent SQL computation for two anchor dates |
| [`sales-overview`](../../packages/schemas/examples/dashboards/sales-overview) | Hand-written example (Phase 0) | KPIs, filter and highlight actions, Top N parameter, row-level security | Per-manager RLS (P1.13 tests) |

The tests are `apps/api/tests/test_recreations.py`. Open the dashboards in the portal (`TABFREE_DEV_LOGIN=1`) as Erin (Executives) to see all three.

## Deviations from the originals

- **Hierarchy Navigator extension:** Tableau extensions cannot run in TabFree. Drill-down filter actions replace it (category, then sub-category, then products). Its parameter-driven calculations (`Dim for current level`, `Filter by ID`, and `REGEXP_*` over a path parameter) are not needed.
- **Tree map:** shown as a bar chart until `<tf-chart>` gains a treemap mark. Charts are the P1.15 decision.
- **eBike data source:** eBike's published data source (`sqlproxy`) is not in the corpus. The same formulas run on Superstore instead:
  - Order Placed Date → Order Date
  - Units → Quantity
  - Return Flag → Returned
  - OrderID → Order ID
- **Min/max dots on the area charts:** these need custom table calcs (`WINDOW_MIN(...) = SUM(...)`). They are not reproduced yet.
- **Maps:** a US state tile map (`<tf-map>`) replaces Tableau's filled map. Geographic maps come in Phase 4.
- **Fixed dashboard sizes:** the originals are 1080×600 and automatic; the recreations use responsive grids.

## What the round taught us

- **The compiler handled every calculation as written** after the field renames: `{fixed: sum([Sales])}`, nested `IF … ELSEIF` with `DATETRUNC`/`DATEDIFF` against a date parameter, `COUNTD(IF … END)`, aggregates over the returns table through a relationship, and `CASE` on a parameter.
- **Three gaps showed up.** All are fixed now:
  - Per-query row limits were treated as truncation.
  - A format set on a query's measure was not applied.
  - Rank columns inherited the currency format.
- **Still missing for real workbooks:**
  - `REGEXP_*` functions, the top compiler gap in the corpus (see the P1.6 report).
  - Custom table-calc expressions.
  - Treemap and dual-axis marks.
  - Sets used as filters.
