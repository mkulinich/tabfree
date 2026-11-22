/** Default component styles, themed through CSS variables. Dashboards can override any of them. */
export const STYLES = `
:root {
  color-scheme: light;
  --tf-bg: #ffffff;
  --tf-surface: #f7f8fa;
  --tf-text: #1f2328;
  --tf-muted: #5f6b7a;
  --tf-grid: #e3e6ea;
  --tf-border: #d0d5db;
  --tf-accent: #2f6fde;
  --tf-error: #b42318;
  --tf-c0: #3b6fd8; --tf-c1: #e2862f; --tf-c2: #3a9a6a; --tf-c3: #c94a5e;
  --tf-c4: #7c5cc4; --tf-c5: #2a9fb0; --tf-c6: #b58a1f; --tf-c7: #7d8794;
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --tf-bg: #15181c;
  --tf-surface: #1d2126;
  --tf-text: #e6e8eb;
  --tf-muted: #9aa4af;
  --tf-grid: #2c3238;
  --tf-border: #3a4148;
  --tf-accent: #6b9bff;
  --tf-error: #ff8a80;
  --tf-c0: #6b9bff; --tf-c1: #f0a050; --tf-c2: #5cc48e; --tf-c3: #ee7387;
  --tf-c4: #a68bea; --tf-c5: #52c6d6; --tf-c6: #d6b04a; --tf-c7: #a3acb6;
}
html, body { margin: 0; background: var(--tf-bg); color: var(--tf-text); }
tf-dashboard { display: block; padding: 16px; }
tf-chart, tf-crosstab, tf-kpi, tf-map { display: block; min-width: 0; background: var(--tf-surface);
  border: 1px solid var(--tf-border); border-radius: 8px; padding: 12px; }
tf-filter, tf-parameter { display: inline-flex; flex-direction: column; gap: 4px; min-width: 160px; }
[aria-busy="true"] { opacity: 0.6; }
.tf-title { font-weight: 600; margin-bottom: 8px; }
.tf-label, .tf-kpi-label { color: var(--tf-muted); font-size: 12px; }
.tf-kpi-value { font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tf-input { font: inherit; padding: 4px 6px; border: 1px solid var(--tf-border); border-radius: 6px;
  background: var(--tf-bg); color: var(--tf-text); }
.tf-checklist { display: flex; flex-direction: column; max-height: 160px; overflow: auto; }
.tf-check { display: flex; gap: 6px; align-items: center; }
.tf-range, .tf-slider { display: flex; gap: 6px; align-items: center; }
.tf-range .tf-input { width: 6em; }
.tf-error { color: var(--tf-error); }
.tf-kpi-change { font-size: 12px; font-weight: 600; }
.tf-up { color: var(--tf-c2); } .tf-down { color: var(--tf-c3); } .tf-flat { color: var(--tf-muted); }
.tf-empty { fill: var(--tf-grid); }
.tf-svg .tf-tile-label { fill: var(--tf-text); font-size: 10px; pointer-events: none; }
.tf-note { color: var(--tf-muted); font-size: 12px; margin-top: 6px; }
.tf-svg { display: block; max-width: 100%; height: auto; overflow: visible; }
.tf-svg text { fill: var(--tf-muted); font-size: 11px; }
.tf-grid { stroke: var(--tf-grid); }
.tf-zero { stroke: var(--tf-border); }
.tf-mark { cursor: pointer; }
.tf-mark:hover { stroke: var(--tf-text); stroke-width: 1; }
.tf-dim { opacity: 0.25; }
.tf-line { fill: none; stroke-width: 2; }
.tf-area { stroke: none; opacity: 0.2; }
${Array.from({ length: 8 }, (_, i) => `.tf-c${i} { fill: var(--tf-c${i}); } .tf-line.tf-c${i} { fill: none; stroke: var(--tf-c${i}); }`).join("\n")}
.tf-scroll { overflow: auto; max-height: 420px; }
.tf-table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
.tf-table th, .tf-table td { padding: 4px 8px; border-bottom: 1px solid var(--tf-grid); text-align: left; }
.tf-table th { position: sticky; top: 0; background: var(--tf-surface); cursor: pointer; user-select: none; }
.tf-table th[aria-sort="ascending"]::after { content: " ▲"; }
.tf-table th[aria-sort="descending"]::after { content: " ▼"; }
.tf-table tbody tr { cursor: pointer; }
.tf-table tbody tr:hover { background: var(--tf-grid); }
.tf-num { text-align: right !important; }
.tf-table .tf-rowhead { font-weight: 500; text-align: left; position: static; cursor: default; background: none; }
.tf-table tr.tf-total { font-weight: 600; cursor: default; }
.tf-table tr.tf-total td, .tf-table tr.tf-total th { border-top: 1px solid var(--tf-border); }
tf-container { min-width: 0; }
tf-container > * { margin: 0; }
tf-legend, tf-text, tf-image { display: block; min-width: 0; }
tf-image .tf-image { width: 100%; height: 100%; display: block; }
.tf-legend-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.tf-legend-item { display: flex; gap: 6px; align-items: center; border: none; background: none; color: inherit;
  font: inherit; padding: 2px 4px; cursor: pointer; border-radius: 4px; text-align: left; }
.tf-legend-item[aria-pressed="true"] { background: var(--tf-grid); }
.tf-legend-item.tf-dim { opacity: 0.4; }
.tf-swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
${Array.from({ length: 8 }, (_, i) => `.tf-bg-c${i} { background: var(--tf-c${i}); }`).join("\n")}
.tf-button { font: inherit; padding: 4px 10px; border: 1px solid var(--tf-border); border-radius: 6px;
  background: var(--tf-bg); color: var(--tf-text); cursor: pointer; }
.tf-button:hover { border-color: var(--tf-accent); }
.tf-drill { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.tf-search { display: flex; flex-direction: column; gap: 4px; }
.tf-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.tf-chip { font: inherit; font-size: 12px; border: 1px solid var(--tf-border); border-radius: 10px;
  background: var(--tf-surface); color: var(--tf-text); padding: 1px 8px; cursor: pointer; }
/* P6.11: overlays, extra marks, device layouts */
.tf-reference { stroke: var(--tf-text); stroke-dasharray: 4 3; stroke-width: 1; opacity: 0.7; }
.tf-reference-label { font-size: 11px; fill: var(--tf-text); opacity: 0.8; }
.tf-band { fill: var(--tf-text); opacity: 0.08; }
.tf-trend { stroke: var(--tf-text); stroke-width: 1.5; stroke-dasharray: 6 3; opacity: 0.6; }
.tf-whisker, .tf-median, .tf-target { stroke: var(--tf-text); stroke-width: 2; }
.tf-label { font-size: 11px; fill: var(--tf-text); pointer-events: none; }
tf-when[hidden], [hidden] { display: none !important; }
.tf-phone tf-container { flex-direction: column !important; }
.tf-phone tf-container > * { flex: 1 1 auto !important; width: 100% !important; }
.tf-phone tf-float { position: static !important; width: auto !important; height: auto !important; }
.tf-viz-tooltip { position: fixed; z-index: 10; background: var(--tf-surface); border: 1px solid var(--tf-border);
  border-radius: 6px; padding: 6px; box-shadow: 0 4px 16px rgba(0,0,0,.2); pointer-events: none; }
/* P7.4: accessibility */
.tf-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.tf-mark:focus-visible { outline: 3px solid var(--tf-text); outline-offset: 2px; }
.tf-button:focus-visible, .tf-chip:focus-visible, .tf-input:focus-visible { outline: 3px solid var(--tf-text); outline-offset: 2px; }
`;
