# @tabfree/runtime

The dashboard runtime: everything that runs in the browser around a TabFree dashboard.

| Module | Runs in | What it does |
|---|---|---|
| [`protocol.ts`](src/protocol.ts) | both | Typed messages between the sandboxed frame and the host, with strict validators for both directions. |
| [`host.ts`](src/host.ts) | portal | `mountDashboard()`: creates the sandboxed iframe and runs the handshake. Accepts only declared queries, filters and parameters, and turns selections into the manifest's actions. Applies table calcs, then calls your `runQuery`/`runDomain` (the governed query API). |
| [`sdk.ts`](src/sdk.ts) | frame | `TabfreeClient`: `query(name)`, `domain(filter)`, `setFilter`, `setParameter`, `select`, plus `state` events. Results are shared per state, so several components that name the same query make one request. |
| [`components.ts`](src/components.ts) | frame | `<tf-dashboard>`, `<tf-chart>`, `<tf-crosstab>`, `<tf-kpi>` (with an optional `change` measure shown as ▲/▼), `<tf-map>`, `<tf-filter>`, `<tf-parameter>`. |
| [`layout.ts`](src/layout.ts) | frame | Layout and content objects (P3.6): `<tf-container>`, `<tf-float>`, `<tf-legend>`, `<tf-text>`, `<tf-image>`, `<tf-export>`. |
| [`map.ts`](src/map.ts) | frame | A US state tile map for `<tf-map query geo color>`; it needs no boundary files. Geographic maps arrive in Phase 4. |
| [`chart.ts`](src/chart.ts) | frame | A dependency-free SVG renderer for bar, line, area and point marks (see below). |
| [`tablecalc.ts`](src/tablecalc.ts) | portal | Re-exports [`@tabfree/tablecalc`](../tablecalc/src/index.ts), the table-calculation engine ([ADR-004](../../docs/adr/0004-table-calcs-sql-and-js-engine.md)): every quick table calc, custom table-calc formulas, Compute Using (table, pane, cell, specific, at the level, restarting every), secondary calcs, densification and table-calc filters. |
| [`format.ts`](src/format.ts) | both | Tableau/Excel number formats from the model (`$#,##0`, `0.0%`). |

`npm run build` writes `dist/` (ES modules and types) and two bundles:
- `dist/bundle/frame.js`: one IIFE. The API serves it inside each dashboard's frame document on the usercontent origin (`TABFREE_USERCONTENT_ORIGIN`); without one (development), the host inlines it into the frame's `srcdoc`, because the frame cannot load anything.
- `dist/bundle/host.js`: an ES module for the portal.

## Writing a dashboard

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

Attributes name queries, filters and parameters from `tabfree.json`, and output columns of those queries. `uv run tabfree-validate path/to/bundle/` checks every reference before you publish.

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

Try it locally with the example dashboard and generated data:

```bash
npm run build -w @tabfree/runtime
TABFREE_PREVIEW=1 uv run uvicorn tabfree_api.main:app --port 8765   # open http://localhost:8765/preview
```

The preview has no sign-in: you pick the viewer, so you can see row-level security change what each user gets. The real governed API with sign-in arrives in P1.13.

## Charts

BUILD_PLAN names Vega-Lite and ECharts. The P1.15 authoring eval chooses between them, so Phase 1 ships a small built-in SVG renderer rather than committing early. It is dependency-free and deterministic (the same SVG in the browser and in PDF snapshots), and it sits behind `renderChart(spec, data, options)`, so a library can replace it without changing `<tf-chart>`.

## Security model ([ADR-002](../../docs/adr/0002-html-bundle-with-governed-data-contract.md))

- **Isolated frame:** dashboards render with `sandbox="allow-scripts"` and no `allow-same-origin`, so they get an opaque origin with no cookies, storage or access to the portal. A CSP is injected first in `<head>`: `default-src 'none'`, `connect-src 'none'`, inline scripts and styles only, and images and fonts from `data:` only.
- **Separate origin (P3.6):** in production the frame loads a signed, five-minute URL on the usercontent origin, whose response carries the same CSP as a header plus `sandbox allow-scripts` and `frame-ancestors <portal>`. The portal page itself then runs with `script-src 'self'`: no dashboard code ever shares its origin or policy.
- **Private channel:**
  - The frame's origin is `"null"`, so the host identifies its hello by `event.source === iframe.contentWindow`.
  - It then transfers a `MessagePort` and sends everything over that port.
  - A document that replaces the dashboard never receives data.
- **Named queries only:**
  - The frame asks by query name; the host refuses undeclared queries, filters, parameters and value kinds.
  - The server (`tabfree_compiler.dashboard`) re-checks everything against the manifest and compiles with the viewer's row-level security.
- **Strict validation:** both sides validate every message. Unknown types, unexpected keys, non-finite numbers, oversized payloads and `__proto__` keys are dropped.
- **Untrusted data:** components build the DOM with `createElement` and `textContent`. Data values never pass through `innerHTML`.
- **Known residual risk:**
  - Custom JavaScript in a dashboard can leak data the viewer may see, by navigating its own frame elsewhere.
  - Component-only dashboards contain no custom JS.
  - Custom JS needs a static scan and human review before publishing (P4.5).

```bash
npm test -w @tabfree/runtime        # protocol, host+SDK handshake, components, charts, formats, table calcs
npm run typecheck -w @tabfree/runtime
```
