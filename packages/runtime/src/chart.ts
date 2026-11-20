/**
 * A small, dependency-free SVG chart renderer for `<tf-chart>`.
 *
 * Phase 1 needs bars, lines, areas and points that render identically in the browser and in
 * headless PDF snapshots. The P1.15 eval compares Vega-Lite and ECharts as the long-term renderer;
 * both can sit behind {@link renderChart}'s signature.
 */

import { s } from "./dom.js";
import { formatAxis, formatValue } from "./format.js";
import type { Column, Scalar, Tuple } from "./protocol.js";

export type Mark = "bar" | "line" | "area" | "point";

export interface ChartSpec {
  mark: Mark;
  x: string;
  y: string;
  color?: string | undefined;
  title?: string | undefined;
}

export interface ChartData {
  columns: Column[];
  rows: Scalar[][];
}

export interface ChartOptions {
  width: number;
  height: number;
  /** Tuples to emphasise; other marks are dimmed. */
  highlight?: Tuple[] | null | undefined;
  onSelect?: ((tuple: Tuple | null) => void) | undefined;
}

export class ChartError extends Error {}

/** A neutral categorical palette (colour-blind-safe order), defined as CSS variables in styles.ts. */
export const PALETTE_SIZE = 8;

const MARGIN = { top: 12, right: 16, bottom: 36, left: 48 };

function isMeasure(col: Column): boolean {
  if (col.role) return col.role === "measure";
  return col.type === "integer" || col.type === "float" || col.type === "decimal";
}

function numeric(value: Scalar): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** "Nice" axis ticks covering [lo, hi]. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (lo === hi) {
    hi = lo === 0 ? 1 : lo + Math.abs(lo);
    if (lo > 0) lo = 0;
  }
  const raw = (hi - lo) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * magnitude >= raw) ?? 10) * magnitude;
  const start = Math.floor(lo / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= hi + step * 1e-9; t += step) ticks.push(Number(t.toPrecision(12)));
  if ((ticks.at(-1) ?? hi) < hi) ticks.push((ticks.at(-1) ?? hi) + step);
  return ticks;
}

function key(value: Scalar): string {
  return value === null ? "\u0000null" : String(value);
}

function matches(tuple: Tuple, highlight: Tuple[]): boolean {
  return highlight.some((h) => Object.entries(h).every(([k, v]) => !(k in tuple) || tuple[k] === v));
}

/** Render a chart into a fresh <svg>. Throws ChartError when the spec does not fit the data. */
export function renderChart(spec: ChartSpec, data: ChartData, options: ChartOptions): SVGSVGElement {
  const index = new Map(data.columns.map((c, i) => [c.name, i]));
  const col = (name: string | undefined): [Column, number] | null => {
    if (name === undefined) return null;
    const i = index.get(name);
    const column = i === undefined ? undefined : data.columns[i];
    if (i === undefined || column === undefined) throw new ChartError(`the query has no column "${name}"`);
    return [column, i];
  };
  const xc = col(spec.x);
  const yc = col(spec.y);
  if (!xc || !yc) throw new ChartError("a chart needs x and y");
  const colorc = col(spec.color);

  const { width, height } = options;
  const svg = s("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    role: "img",
    "aria-label": spec.title ?? `${spec.y} by ${spec.x}`,
    class: "tf-svg",
  });

  const xIsMeasure = isMeasure(xc[0]);
  const yIsMeasure = isMeasure(yc[0]);
  if (xIsMeasure && yIsMeasure) {
    renderScatter(svg, data, xc, yc, colorc, options);
    return svg;
  }
  // One categorical axis and one value axis. Horizontal when the category is on y.
  const horizontal = !yIsMeasure;
  const [catCol, catIdx] = horizontal ? yc : xc;
  const [valCol, valIdx] = horizontal ? xc : yc;
  if (!isMeasure(valCol)) throw new ChartError(`"${valCol.name}" must be a measure`);

  const seriesIdx = colorc && !isMeasure(colorc[0]) ? colorc[1] : null;
  const shadeIdx = colorc && isMeasure(colorc[0]) ? colorc[1] : null;

  const categories: Scalar[] = [];
  const seen = new Set<string>();
  for (const row of data.rows) {
    const c = row[catIdx] ?? null;
    if (!seen.has(key(c))) {
      seen.add(key(c));
      categories.push(c);
    }
  }
  const series: Scalar[] = [];
  if (seriesIdx !== null) {
    const seenSeries = new Set<string>();
    for (const row of data.rows) {
      const v = row[seriesIdx] ?? null;
      if (!seenSeries.has(key(v))) {
        seenSeries.add(key(v));
        series.push(v);
      }
    }
  }

  const values = data.rows.map((r) => numeric(r[valIdx] ?? null)).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values));
  const lo = ticks[0] ?? 0;
  const hi = ticks.at(-1) ?? 1;

  const labelWidth = horizontal
    ? Math.min(180, 12 + 7 * Math.max(1, ...categories.map((c) => categoryFormatter(categories, catCol)(c).length)))
    : MARGIN.left + 8;
  const left = horizontal ? labelWidth : Math.max(MARGIN.left, 10 + 7 * formatAxis(hi, valCol).length);
  const plot = {
    x: left,
    y: MARGIN.top,
    w: Math.max(10, width - left - MARGIN.right),
    h: Math.max(10, height - MARGIN.top - MARGIN.bottom - (series.length > 1 ? 18 : 0)),
  };
  const band = (horizontal ? plot.h : plot.w) / Math.max(1, categories.length);
  const catLabel = categoryFormatter(categories, catCol);
  const catPos = (i: number) => (horizontal ? plot.y : plot.x) + band * (i + 0.5);
  const valPos = (v: number) =>
    horizontal ? plot.x + ((v - lo) / (hi - lo)) * plot.w : plot.y + plot.h - ((v - lo) / (hi - lo)) * plot.h;

  // Grid and value axis.
  const axis = s("g", { class: "tf-axis" });
  for (const t of ticks) {
    const p = valPos(t);
    axis.append(
      horizontal
        ? s("line", { x1: p, x2: p, y1: plot.y, y2: plot.y + plot.h, class: t === 0 ? "tf-zero" : "tf-grid" })
        : s("line", { x1: plot.x, x2: plot.x + plot.w, y1: p, y2: p, class: t === 0 ? "tf-zero" : "tf-grid" }),
      horizontal
        ? s("text", { x: p, y: plot.y + plot.h + 16, "text-anchor": "middle" }, formatAxis(t, valCol))
        : s("text", { x: plot.x - 6, y: p + 4, "text-anchor": "end" }, formatAxis(t, valCol)),
    );
  }
  // Category axis labels (thinned when crowded).
  const every = Math.max(1, Math.ceil(categories.length / Math.max(1, Math.floor((horizontal ? plot.h : plot.w) / (horizontal ? 14 : 70)))));
  categories.forEach((c, i) => {
    if (i % every !== 0) return;
    const p = catPos(i);
    axis.append(
      horizontal
        ? s("text", { x: plot.x - 6, y: p + 4, "text-anchor": "end" }, catLabel(c))
        : s("text", { x: p, y: plot.y + plot.h + 16, "text-anchor": "middle" }, catLabel(c)),
    );
  });
  svg.append(axis);

  const shadeValues = shadeIdx === null ? [] : data.rows.map((r) => numeric(r[shadeIdx] ?? null) ?? 0);
  const shadeLo = Math.min(...shadeValues, 0);
  const shadeHi = Math.max(...shadeValues, 0);
  const shade = (v: number) => (shadeHi === shadeLo ? 0.85 : 0.35 + (0.65 * (v - shadeLo)) / (shadeHi - shadeLo));
  const seriesClass = (row: Scalar[]) => {
    if (seriesIdx === null) return "tf-c0";
    const i = series.findIndex((v) => key(v) === key(row[seriesIdx] ?? null));
    return `tf-c${i % PALETTE_SIZE}`;
  };
  const tupleOf = (row: Scalar[]): Tuple => {
    const t: Tuple = { [catCol.name]: row[catIdx] ?? null };
    if (seriesIdx !== null && colorc) t[colorc[0].name] = row[seriesIdx] ?? null;
    return t;
  };
  const tooltip = (row: Scalar[]) =>
    data.columns.map((c, i) => `${c.name}: ${formatValue(row[i] ?? null, c)}`).join("\n");
  const highlight = options.highlight?.length ? options.highlight : null;

  const marks = s("g", { class: "tf-marks" });
  if (spec.mark === "bar") {
    const groups = Math.max(1, series.length);
    const thickness = Math.min((band * 0.8) / groups, 36);
    const span = thickness * groups;
    data.rows.forEach((row) => {
      const v = numeric(row[valIdx] ?? null);
      const ci = categories.findIndex((c) => key(c) === key(row[catIdx] ?? null));
      if (v === null || ci < 0) return;
      const si = seriesIdx === null ? 0 : series.findIndex((x) => key(x) === key(row[seriesIdx] ?? null));
      const offset = catPos(ci) - span / 2 + thickness * si;
      const a = valPos(Math.min(0, v));
      const b = valPos(Math.max(0, v));
      const rect = horizontal
        ? s("rect", { x: a, y: offset, width: Math.max(1, b - a), height: Math.max(1, thickness - 1) })
        : s("rect", { x: offset, y: b, width: Math.max(1, thickness - 1), height: Math.max(1, a - b) });
      decorate(rect, row);
      marks.append(rect);
    });
  } else {
    const groups = seriesIdx === null ? [null] : series;
    groups.forEach((sv) => {
      const rows = data.rows.filter((r) => seriesIdx === null || key(r[seriesIdx] ?? null) === key(sv));
      const points = rows
        .map((r) => {
          const v = numeric(r[valIdx] ?? null);
          const ci = categories.findIndex((c) => key(c) === key(r[catIdx] ?? null));
          return v === null || ci < 0 ? null : { row: r, x: horizontal ? valPos(v) : catPos(ci), y: horizontal ? catPos(ci) : valPos(v) };
        })
        .filter((p): p is { row: Scalar[]; x: number; y: number } => p !== null);
      const cls = points[0] ? seriesClass(points[0].row) : "tf-c0";
      if (spec.mark !== "point" && points.length > 1) {
        const d = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("");
        if (spec.mark === "area" && !horizontal) {
          const base = valPos(Math.max(lo, Math.min(0, hi)));
          const first = points[0];
          const last = points.at(-1);
          if (first && last) {
            marks.append(s("path", { d: `${d}L${last.x.toFixed(1)},${base}L${first.x.toFixed(1)},${base}Z`, class: `tf-area ${cls}` }));
          }
        }
        marks.append(s("path", { d, class: `tf-line ${cls}` }));
      }
      for (const p of points) {
        const dot = s("circle", { cx: p.x, cy: p.y, r: spec.mark === "point" ? 4 : 3 });
        decorate(dot, p.row);
        marks.append(dot);
      }
    });
  }
  svg.append(marks);

  if (series.length > 1 && colorc) {
    const legend = s("g", { class: "tf-legend" });
    let x = plot.x;
    const y = height - 8;
    series.forEach((v, i) => {
      const label = formatValue(v, colorc[0]);
      legend.append(s("rect", { x, y: y - 9, width: 10, height: 10, class: `tf-c${i % PALETTE_SIZE}` }));
      legend.append(s("text", { x: x + 14, y }, label));
      x += 24 + label.length * 7;
    });
    svg.append(legend);
  }
  return svg;

  function decorate(el: SVGElement, row: Scalar[]): void {
    el.setAttribute("class", `tf-mark ${seriesClass(row)}`);
    if (shadeIdx !== null) el.setAttribute("fill-opacity", shade(numeric(row[shadeIdx] ?? null) ?? 0).toFixed(2));
    const tuple = tupleOf(row);
    if (highlight && !matches(tuple, highlight)) el.classList.add("tf-dim");
    el.append(s("title", {}, tooltip(row)));
    if (options.onSelect) {
      const select = options.onSelect;
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        select(tuple);
      });
    }
  }
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Axis labels for dates: "Jan 2024" when every value is the first of a month, "2024" for years. */
export function categoryFormatter(values: readonly Scalar[], column: Column): (v: Scalar) => string {
  const temporal = column.type === "date" || column.type === "datetime";
  const dates = values.filter((v): v is string => typeof v === "string");
  const midnight = (v: string) => v.length <= 10 || /[ T]00:00:00(\.0+)?$/.test(v);
  if (!temporal || !dates.length || !dates.every(midnight)) return (v) => formatValue(v, column);
  const firstOfMonth = dates.every((v) => v.slice(8, 10) === "01");
  const firstOfYear = firstOfMonth && dates.every((v) => v.slice(5, 7) === "01") && dates.length > 1;
  if (firstOfYear) return (v) => (typeof v === "string" ? v.slice(0, 4) : formatValue(v, column));
  if (firstOfMonth) {
    return (v) => (typeof v === "string" ? `${MONTHS[Number(v.slice(5, 7)) - 1] ?? "?"} ${v.slice(0, 4)}` : formatValue(v, column));
  }
  return (v) => formatValue(v, column);
}

function renderScatter(
  svg: SVGSVGElement,
  data: ChartData,
  [xCol, xi]: [Column, number],
  [yCol, yi]: [Column, number],
  colorc: [Column, number] | null,
  options: ChartOptions,
): void {
  const { width, height } = options;
  const xs = data.rows.map((r) => numeric(r[xi] ?? null)).filter((v): v is number => v !== null);
  const ys = data.rows.map((r) => numeric(r[yi] ?? null)).filter((v): v is number => v !== null);
  const xt = niceTicks(Math.min(0, ...xs), Math.max(0, ...xs));
  const yt = niceTicks(Math.min(0, ...ys), Math.max(0, ...ys));
  const plot = { x: MARGIN.left + 8, y: MARGIN.top, w: width - MARGIN.left - 8 - MARGIN.right, h: height - MARGIN.top - MARGIN.bottom };
  const [x0, x1] = [xt[0] ?? 0, xt.at(-1) ?? 1];
  const [y0, y1] = [yt[0] ?? 0, yt.at(-1) ?? 1];
  const px = (v: number) => plot.x + ((v - x0) / (x1 - x0)) * plot.w;
  const py = (v: number) => plot.y + plot.h - ((v - y0) / (y1 - y0)) * plot.h;
  const axis = s("g", { class: "tf-axis" });
  for (const t of xt) {
    axis.append(s("line", { x1: px(t), x2: px(t), y1: plot.y, y2: plot.y + plot.h, class: "tf-grid" }));
    axis.append(s("text", { x: px(t), y: plot.y + plot.h + 16, "text-anchor": "middle" }, formatAxis(t, xCol)));
  }
  for (const t of yt) {
    axis.append(s("line", { x1: plot.x, x2: plot.x + plot.w, y1: py(t), y2: py(t), class: "tf-grid" }));
    axis.append(s("text", { x: plot.x - 6, y: py(t) + 4, "text-anchor": "end" }, formatAxis(t, yCol)));
  }
  svg.append(axis);
  const marks = s("g", { class: "tf-marks" });
  for (const row of data.rows) {
    const x = numeric(row[xi] ?? null);
    const y = numeric(row[yi] ?? null);
    if (x === null || y === null) continue;
    const dot = s("circle", { cx: px(x), cy: py(y), r: 4, class: "tf-mark tf-c0" });
    dot.append(s("title", {}, data.columns.map((c, i) => `${c.name}: ${formatValue(row[i] ?? null, c)}`).join("\n")));
    if (colorc) dot.setAttribute("data-color", formatValue(row[colorc[1]] ?? null, colorc[0]));
    marks.append(dot);
  }
  svg.append(marks);
}
