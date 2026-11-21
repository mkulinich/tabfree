# @tabfree/runtime

The dashboard runtime: everything that runs in the browser around a TabFree dashboard.

| Module | Runs in | What it does |
|---|---|---|
| [`protocol.ts`](src/protocol.ts) | both | Typed messages between the sandboxed frame and the host, with strict validators for both directions. |
| [`host.ts`](src/host.ts) | portal | `mountDashboard()`: creates the sandboxed iframe and runs the handshake. Accepts only declared queries, filters and parameters, and turns selections into the manifest's actions. Applies table calcs, then calls your `runQuery`/`runDomain` (the governed query API). |
| [`sdk.ts`](src/sdk.ts) | frame | `TabfreeClient`: `query(name)`, `domain(filter)`, `setFilter`, `setParameter`, `select`, plus `state` events. Results are shared per state, so several components that name the same query make one request. |
| [`components.ts`](src/components.ts) | frame | `<tf-dashboard>`, `<tf-chart>`, `<tf-crosstab>`, `<tf-kpi>` (with an optional `change` measure shown as ▲/▼), `<tf-map>`, `<tf-filter>`, `<tf-parameter>`. |
| [`map.ts`](src/map.ts) | frame | A US state tile map for `<tf-map query geo color>`; it needs no boundary files. Geographic maps arrive in Phase 4. |
| [`chart.ts`](src/chart.ts) | frame | A dependency-free SVG renderer for bar, line, area and point marks (see below). |
| [`tablecalc.ts`](src/tablecalc.ts) | portal | Table calculations, which the JS engine owns ([ADR-004](../../docs/adr/0004-table-calcs-sql-and-js-engine.md)): running, difference, percent of total, moving and window aggregates, ranks, index, first, last, size and total. |
| [`format.ts`](src/format.ts) | both | Tableau/Excel number formats from the model (`$#,##0`, `0.0%`). |

`npm run build` writes `dist/` (ES modules and types) and two bundles:
- `dist/bundle/frame.js`: one IIFE of about 27 KB. The host inlines it into each dashboard's `srcdoc`, because the frame cannot load anything.
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
