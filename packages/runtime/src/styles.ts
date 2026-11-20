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
tf-chart, tf-crosstab, tf-kpi { display: block; min-width: 0; background: var(--tf-surface);
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
`;
