# Queries, filters, parameters and actions (tabfree.json)

`learn("schema")` returns the full JSON Schema; this is the short version.

```json
{
  "schema": "tabfree/dashboard@0",
  "id": "sales-overview",
  "title": "Sales Overview",
  "models": { "sales": "superstore_sales" },
  "parameters": {
    "top_n": { "label": "Top N", "type": "integer", "default": 10,
               "allowed": { "range": { "min": 1, "max": 50, "step": 1 } } }
  },
  "filters": {
    "region": { "label": "Region", "field": "Region", "kind": "categorical", "multi": true, "appliesTo": "all" },
    "order_date": { "field": "Order Date", "kind": "relative_date", "context": true, "appliesTo": "all",
                    "default": { "relative": { "period": "month", "from": -11, "to": 0 } } }
  },
  "queries": {
    "kpis": { "measures": ["Sales", "Profit", "Profit Ratio"] },
    "by_region": { "dimensions": ["Region"], "measures": ["Sales", "Profit Ratio"],
                   "sort": [{ "by": "Sales", "dir": "desc" }] },
    "trend": {
      "dimensions": [{ "field": "Order Date", "dateTrunc": "month", "as": "Month" }],
      "measures": ["Sales"],
      "tableCalcs": [{ "name": "Running Sales", "type": "running_sum", "of": "Sales",
                       "computeUsing": "specific", "addressing": ["Month"] }],
      "sort": [{ "by": "Month", "dir": "asc" }]
    },
    "top_customers": {
      "dimensions": ["Customer Name"], "measures": ["Sales"],
      "where": [{ "field": "Customer Name", "kind": "top_n",
                  "topN": { "n": { "param": "top_n" }, "by": "SUM([Sales])" } }]
    }
  },
  "actions": [
    { "type": "filter", "source": ["by_region"], "targets": ["trend", "top_customers"], "fields": ["Region"] },
    { "type": "highlight", "source": ["top_customers"], "targets": ["top_customers"],
      "fields": ["Customer Name"], "trigger": "hover" }
  ]
}
```

## Queries

- `dimensions`: field names, or `{ "field", "dateTrunc" | "datePart", "as" }`.
- `measures`: model measures by name (they carry their aggregation), or
  `{ "field", "aggregation", "as", "format" }`, or `{ "expr": "SUM([Sales]) / SUM([Quantity])", "as" }`
  with Tableau's calculation syntax (LODs such as `{FIXED [Region]: SUM([Sales])}` work).
- `where`: query-level filters (`categorical`, `range`, `relative_date`, `top_n`, `wildcard`).
- `sort`, `limit`, `totals` (Total and Grand Total rows), `tableCalcs` (running sums, ranks, percent of
  total, moving averages, YoY via `difference`/`percent_difference`, or `custom` expressions).
- A component's columns are the query's outputs: dimension and measure names (or their `as`) and
  table-calc names.

## Filters and parameters

- `kind`: `categorical`, `range`, `relative_date` or `wildcard`; `appliesTo` is `"all"` or a list of
  query names. `"context": true` makes FIXED LODs and Top N respect the filter.
- Parameters have a `type`, a `default`, and `allowed` (`"any"`, a `list` or a `range`). Queries use
  them as `{ "param": "top_n" }`.

## Actions

- `filter`: a selection in `source` filters `targets` on `fields` (source output → target field).
  `clear`: `show_all` (default), `keep_filter` or `exclude_all`.
- `highlight`: dims everything else in `targets`.
- `url`: opens a URL template (it needs a human review).

## Year over year

Add the date at year grain plus a `percent_difference` table calc along it:

```json
{ "dimensions": [{ "field": "Order Date", "dateTrunc": "year", "as": "Year" }],
  "measures": ["Sales"],
  "tableCalcs": [{ "name": "Sales YoY", "type": "percent_difference", "of": "Sales",
                   "computeUsing": "specific", "addressing": ["Year"] }] }
```
