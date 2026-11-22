/**
 * A small, dependency-free SVG chart renderer for `<tf-chart>`.
 *
 * Phase 1 needs bars, lines, areas and points that render identically in the browser and in
 * headless PDF snapshots. The P1.15 eval compares Vega-Lite and ECharts as the long-term renderer;
 * both can sit behind {@link renderChart}'s signature.
 */

import { keyboard, s } from "./dom.js";
import { formatAxis, formatValue } from "./format.js";
import { EXTRA_MARKS, type ExtraMark, MarkError, renderExtra } from "./marks.js";
import type { Column, Scalar, Tuple } from "./protocol.js";

export type Mark = "bar" | "line" | "area" | "point" | ExtraMark;
export const MARK_NAMES: readonly string[] = ["bar", "line", "area", "point", ...EXTRA_MARKS];

export interface ChartSpec {
  mark: Mark;
  x: string;
  y: string;
  color?: string | undefined;
  title?: string | undefined;
  /** P6.11 extras: gantt length, bullet target, histogram bins. */
  size?: string | undefined;
  target?: string | undefined;
  bins?: number | undefined;
  /** A reference line: average, median, min, max or a number (bar, line, area and point charts). */
  reference?: string | undefined;
  /** A reference band: "quartiles", or two numbers "lo,hi". */
  band?: string | undefined;
  /** A trend line: "linear" (least squares over the categories in order). */
  trend?: string | undefined;
}

/** Reference statistics of a measure. */
export function referenceValue(kind: string, values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  switch (kind) {
    case "average":
      return values.reduce((a, b) => a + b, 0) / values.length;
    case "median":
      return sorted.length % 2 ? (sorted[mid] ?? null) : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2;
    case "min":
      return sorted[0] ?? null;
    case "max":
      return sorted.at(-1) ?? null;
    default: {
      const n = Number(kind);
      return Number.isFinite(n) ? n : null;
    }
  }
}

/** Least-squares line through (i, value): [intercept, slope]. */
export function linearTrend(values: (number | null)[]): [number, number] | null {
  const points = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (points.length < 2) return null;
  const n = points.length;
  const sx = points.reduce((a, [x]) => a + x, 0);
  const sy = points.reduce((a, [, y]) => a + y, 0);
  const sxx = points.reduce((a, [x]) => a + x * x, 0);
  const sxy = points.reduce((a, [x, y]) => a + x * y, 0);
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx || 1);
  return [(sy - slope * sx) / n, slope];
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
  /** Viz in tooltip (P6.11): the pointer entered a mark (with where), or left it (null). */
  onHover?: ((tuple: Tuple | null, at: { x: number; y: number }) => void) | undefined;
}

export class ChartError extends Error {}
export { MarkError };

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

/** The distinct values of a column in first-appearance order: the order charts give series colours. */
export function seriesOf(data: { columns: Column[]; rows: Scalar[][] }, field: string): Scalar[] {
  const index = data.columns.findIndex((c) => c.name === field);
  if (index < 0) throw new ChartError(`the query has no column "${field}"`);
  const seen = new Set<string>();
  const out: Scalar[] = [];
  for (const row of data.rows) {
    const v = row[index] ?? null;
    if (!seen.has(key(v))) {
      seen.add(key(v));
      out.push(v);
    }
  }
  return out;
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
  const { width, height } = options;
  const svg = s("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    // An image when it only shows; a labelled group when its marks are buttons (they stay reachable).
    role: options.onSelect ? "group" : "img",
    "aria-label": spec.title ?? `${spec.y} by ${spec.x}`,
    class: "tf-svg",
  });
  if ((EXTRA_MARKS as readonly string[]).includes(spec.mark)) {
    try {
      return renderExtra({ ...spec, mark: spec.mark as ExtraMark }, data, options, svg);
    } catch (error) {
      if (error instanceof MarkError) throw new ChartError(error.message);
      throw error;
    }
  }
  const xc = col(spec.x);
  const yc = col(spec.y);
  if (!xc || !yc) throw new ChartError("a chart needs x and y");
  const colorc = col(spec.color);

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

  // Reference lines, bands and trend lines (P6.11), over the value axis.
  const overlay = s("g", { class: "tf-overlay" });
  const inRange = (v: number) => v >= lo && v <= hi;
  const rule = (v: number, label: string) => {
    const p = valPos(v);
    overlay.append(
      horizontal
        ? s("line", { x1: p, x2: p, y1: plot.y, y2: plot.y + plot.h, class: "tf-reference" })
        : s("line", { x1: plot.x, x2: plot.x + plot.w, y1: p, y2: p, class: "tf-reference" }),
      horizontal
        ? s("text", { x: p + 4, y: plot.y + 10, class: "tf-reference-label" }, label)
        : s("text", { x: plot.x + plot.w - 4, y: p - 4, "text-anchor": "end", class: "tf-reference-label" }, label),
    );
  };
  if (spec.band) {
    const sorted = [...values].sort((a, b) => a - b);
    const quart = (q: number) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))] ?? 0;
    const parts = spec.band === "quartiles" ? [quart(0.25), quart(0.75)] : spec.band.split(",").map(Number);
    const [b0, b1] = [Math.max(lo, Math.min(...parts)), Math.min(hi, Math.max(...parts))];
    if (parts.length === 2 && parts.every(Number.isFinite) && b1 > b0) {
      const [p0, p1] = [valPos(b0), valPos(b1)];
      overlay.append(
        horizontal
          ? s("rect", { x: Math.min(p0, p1), y: plot.y, width: Math.abs(p1 - p0), height: plot.h, class: "tf-band" })
          : s("rect", { x: plot.x, y: Math.min(p0, p1), width: plot.w, height: Math.abs(p1 - p0), class: "tf-band" }),
      );
    }
  }
  if (spec.reference) {
    const v = referenceValue(spec.reference, values);
    if (v !== null && inRange(v)) rule(v, `${spec.reference === String(v) ? "" : `${spec.reference}: `}${formatAxis(v, valCol)}`);
  }
  if (spec.trend === "linear" && seriesIdx === null && !horizontal) {
    const byCategory = categories.map((c) => {
      const row = data.rows.find((r) => key(r[catIdx] ?? null) === key(c));
      return row ? numeric(row[valIdx] ?? null) : null;
    });
    const fit = linearTrend(byCategory);
    if (fit && categories.length > 1) {
      const [a, b] = fit;
      const last = categories.length - 1;
      const clamp = (v: number) => Math.max(lo, Math.min(hi, v));
      overlay.append(
        s("line", { x1: catPos(0), y1: valPos(clamp(a)), x2: catPos(last), y2: valPos(clamp(a + b * last)), class: "tf-trend" }),
      );
    }
  }
  if (overlay.childNodes.length) svg.append(overlay);

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
    keyboard(el, tooltip(row), options.onSelect ? () => options.onSelect?.(tuple) : null);
    hoverable(el, tuple, options);
  }
}

function hoverable(el: SVGElement, tuple: Tuple, options: ChartOptions): void {
  const hover = options.onHover;
  if (!hover) return;
  el.addEventListener("mouseenter", (event) => hover(tuple, { x: (event as MouseEvent).clientX, y: (event as MouseEvent).clientY }));
  el.addEventListener("mouseleave", () => hover(null, { x: 0, y: 0 }));
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
  const quarterStarts = firstOfMonth && dates.every((v) => ["01", "04", "07", "10"].includes(v.slice(5, 7)));
  const spaced = new Set(dates.map((v) => v.slice(0, 7))).size === dates.length && dates.length > 1;
  // Quarter starts that are never adjacent months: a quarterly series (a monthly one has Feb, Mar…).
  if (quarterStarts && spaced && !dates.some((v, i) => i > 0 && monthGap(dates[i - 1] ?? v, v) === 1)) {
    return (v) =>
      typeof v === "string" ? `${v.slice(0, 4)} Q${Math.floor((Number(v.slice(5, 7)) - 1) / 3) + 1}` : formatValue(v, column);
  }
  if (firstOfMonth) {
    return (v) => (typeof v === "string" ? `${MONTHS[Number(v.slice(5, 7)) - 1] ?? "?"} ${v.slice(0, 4)}` : formatValue(v, column));
  }
  return (v) => formatValue(v, column);
}

function monthGap(a: string, b: string): number {
  const index = (v: string) => Number(v.slice(0, 4)) * 12 + Number(v.slice(5, 7));
  return Math.abs(index(b) - index(a));
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
  // A dimension on color gives one colour per series (P6.11), with a legend; a measure shades.
  const series = colorc && !isMeasure(colorc[0]) ? seriesOf(data, colorc[0].name) : [];
  const shadeValues = colorc && isMeasure(colorc[0]) ? data.rows.map((r) => numeric(r[colorc[1]] ?? null) ?? 0) : [];
  const [shadeLo, shadeHi] = [Math.min(0, ...shadeValues), Math.max(0, ...shadeValues)];
  const highlight = options.highlight?.length ? options.highlight : null;
  const dims = data.columns.map((c, i) => [c, i] as const).filter(([c]) => !isMeasure(c));
  const marks = s("g", { class: "tf-marks" });
  for (const row of data.rows) {
    const x = numeric(row[xi] ?? null);
    const y = numeric(row[yi] ?? null);
    if (x === null || y === null) continue;
    const si = colorc && series.length ? series.findIndex((v) => key(v) === key(row[colorc[1]] ?? null)) : 0;
    const dot = s("circle", { cx: px(x), cy: py(y), r: 4, class: `tf-mark tf-c${Math.max(0, si) % PALETTE_SIZE}` });
    if (shadeValues.length && colorc) {
      const v = numeric(row[colorc[1]] ?? null) ?? 0;
      dot.setAttribute("fill-opacity", (shadeHi === shadeLo ? 0.85 : 0.3 + (0.7 * (v - shadeLo)) / (shadeHi - shadeLo)).toFixed(2));
    }
    dot.append(s("title", {}, data.columns.map((c, i) => `${c.name}: ${formatValue(row[i] ?? null, c)}`).join("\n")));
    const tuple: Tuple = Object.fromEntries(dims.map(([c, i]) => [c.name, row[i] ?? null]));
    if (highlight && !matches(tuple, highlight)) dot.classList.add("tf-dim");
    if (options.onSelect && dims.length) {
      const select = options.onSelect;
      dot.addEventListener("click", (event) => {
        event.stopPropagation();
        select(tuple);
      });
    }
    keyboard(dot, data.columns.map((c, i) => `${c.name}: ${formatValue(row[i] ?? null, c)}`).join(", "), options.onSelect && dims.length ? () => options.onSelect?.(tuple) : null);
    if (dims.length) hoverable(dot, tuple, options);
    marks.append(dot);
  }
  svg.append(marks);
  if (series.length > 1 && colorc) {
    const legend = s("g", { class: "tf-legend" });
    let lx = plot.x;
    series.forEach((v, i) => {
      const label = formatValue(v, colorc[0]);
      legend.append(s("rect", { x: lx, y: height - 17, width: 10, height: 10, class: `tf-c${i % PALETTE_SIZE}` }));
      legend.append(s("text", { x: lx + 14, y: height - 8 }, label));
      lx += 24 + label.length * 7;
    });
    svg.append(legend);
  }
}
