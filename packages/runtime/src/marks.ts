/**
 * More chart types for `<tf-chart>` (P6.11): pie, heatmap, histogram, treemap, box plot, Gantt and
 * bullet. Same rules as chart.ts: SVG built with the DOM helpers (values are text, never markup), a
 * tooltip on every mark, and clicks reported as dimension tuples for the dashboard's actions.
 *
 * Encodings by mark:
 * - pie: `x` slices (a dimension), `y` angle (a measure);
 * - heatmap: `x` and `y` dimensions, `color` the measure shading each cell;
 * - histogram: `x` a measure, binned in the browser (`bins`, default 10) and counted;
 * - treemap: `x` the rectangles (a dimension), `y` their area (a measure);
 * - boxplot: `x` the groups (a dimension), `y` the measure whose spread is drawn (one row per value);
 * - gantt: `y` the rows (a dimension), `x` the start (a measure or date), `size` the length;
 * - bullet: `y` the rows (a dimension), `x` the value, `target` the target measure.
 */
import { s } from "./dom.js";
import { formatAxis, formatValue } from "./format.js";
import type { Column, Scalar, Tuple } from "./protocol.js";

export const EXTRA_MARKS = ["pie", "heatmap", "histogram", "treemap", "boxplot", "gantt", "bullet"] as const;
export type ExtraMark = (typeof EXTRA_MARKS)[number];

export interface ExtraSpec {
  mark: ExtraMark;
  x: string;
  y: string;
  color?: string | undefined;
  size?: string | undefined;
  target?: string | undefined;
  bins?: number | undefined;
}

interface Data {
  columns: Column[];
  rows: Scalar[][];
}

interface Options {
  width: number;
  height: number;
  highlight?: Tuple[] | null | undefined;
  onSelect?: ((tuple: Tuple | null) => void) | undefined;
}

export class MarkError extends Error {}

const PAD = { top: 12, right: 16, bottom: 36, left: 56 };

function column(data: Data, name: string | undefined, what: string): [Column, number] {
  const i = name === undefined ? -1 : data.columns.findIndex((c) => c.name === name);
  const col = data.columns[i];
  if (i < 0 || !col) throw new MarkError(`${what}: the query has no column "${name ?? ""}"`);
  return [col, i];
}

function num(v: Scalar | undefined): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v)) {
    const t = Date.parse(v.length <= 10 ? `${v}T00:00:00Z` : v);
    return Number.isFinite(t) ? t : null;
  }
  return null;
}

function tooltip(data: Data, row: Scalar[]): string {
  return data.columns.map((c, i) => `${c.name}: ${formatValue(row[i] ?? null, c)}`).join("\n");
}

function interact(el: SVGElement, tuple: Tuple, text: string, options: Options): SVGElement {
  el.append(s("title", {}, text));
  const highlight = options.highlight?.length ? options.highlight : null;
  if (highlight && !highlight.some((h) => Object.entries(h).every(([k, v]) => !(k in tuple) || tuple[k] === v))) {
    el.classList.add("tf-dim");
  }
  if (options.onSelect) {
    const select = options.onSelect;
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      select(tuple);
    });
  }
  return el;
}

/** Quartiles by linear interpolation (Tableau's default for box plots). */
export function quartiles(values: number[]): { min: number; q1: number; median: number; q3: number; max: number } {
  const v = [...values].sort((a, b) => a - b);
  const at = (p: number) => {
    const pos = (v.length - 1) * p;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return (v[lo] ?? 0) + ((v[hi] ?? 0) - (v[lo] ?? 0)) * (pos - lo);
  };
  return { min: v[0] ?? 0, q1: at(0.25), median: at(0.5), q3: at(0.75), max: v.at(-1) ?? 0 };
}

/** Equal-width bins over [min, max]; returns [start, end, count]. */
export function histogram(values: number[], bins: number): [number, number, number][] {
  if (!values.length) return [];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const n = Math.max(1, Math.min(100, Math.floor(bins)));
  const width = hi === lo ? 1 : (hi - lo) / n;
  const out: [number, number, number][] = Array.from({ length: n }, (_v, i) => [lo + i * width, lo + (i + 1) * width, 0]);
  for (const value of values) {
    const i = Math.min(n - 1, Math.floor((value - lo) / width));
    const bin = out[i];
    if (bin) bin[2] += 1;
  }
  return out;
}

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Squarified treemap layout (Bruls et al.) of positive values into a rectangle. */
export function squarify(values: number[], box: Rect): Rect[] {
  const total = values.reduce((a, b) => a + Math.max(0, b), 0);
  const out: Rect[] = values.map(() => ({ x: box.x, y: box.y, w: 0, h: 0 }));
  if (total <= 0) return out;
  const order = values.map((v, i) => [Math.max(0, v), i] as const).sort((a, b) => b[0] - a[0]);
  let rect = { ...box };
  let remaining = total;
  let row: (readonly [number, number])[] = [];
  const worst = (items: (readonly [number, number])[], side: number, scale: number) => {
    const areas = items.map(([v]) => v * scale);
    const sum = areas.reduce((a, b) => a + b, 0);
    return Math.max(...areas.map((a) => Math.max((side * side * a) / (sum * sum), (sum * sum) / (side * side * a))));
  };
  const place = (items: (readonly [number, number])[], scale: number) => {
    const sum = items.reduce((a, [v]) => a + v * scale, 0);
    const horizontal = rect.w >= rect.h;
    const thickness = horizontal ? sum / rect.h : sum / rect.w;
    let offset = 0;
    for (const [v, i] of items) {
      const length = (v * scale) / thickness;
      out[i] = horizontal
        ? { x: rect.x, y: rect.y + offset, w: thickness, h: length }
        : { x: rect.x + offset, y: rect.y, w: length, h: thickness };
      offset += length;
    }
    rect = horizontal
      ? { x: rect.x + thickness, y: rect.y, w: rect.w - thickness, h: rect.h }
      : { x: rect.x, y: rect.y + thickness, w: rect.w, h: rect.h - thickness };
  };
  for (const item of order) {
    if (item[0] <= 0) continue;
    const scale = (rect.w * rect.h) / remaining;
    const side = Math.min(rect.w, rect.h);
    if (!row.length || worst([...row, item], side, scale) <= worst(row, side, scale)) {
      row.push(item);
    } else {
      place(row, scale);
      remaining -= row.reduce((a, [v]) => a + v, 0);
      row = [item];
    }
  }
  if (row.length) place(row, (rect.w * rect.h) / remaining);
  return out;
}

function axisTicks(lo: number, hi: number): number[] {
  const span = hi - lo || 1;
  const step = 10 ** Math.floor(Math.log10(span / 4));
  const nice = [1, 2, 5, 10].map((m) => m * step).find((m) => span / m <= 6) ?? step * 10;
  const ticks = [];
  for (let t = Math.floor(lo / nice) * nice; t <= hi + nice * 1e-9; t += nice) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}

export function renderExtra(spec: ExtraSpec, data: Data, options: Options, svg: SVGSVGElement): SVGSVGElement {
  const { width, height } = options;
  const plot = { x: PAD.left, y: PAD.top, w: Math.max(10, width - PAD.left - PAD.right), h: Math.max(10, height - PAD.top - PAD.bottom) };
  const marks = s("g", { class: "tf-marks" });
  const axis = s("g", { class: "tf-axis" });
  switch (spec.mark) {
    case "pie": {
      const [dimCol, di] = column(data, spec.x, "pie slices (x)");
      const [, vi] = column(data, spec.y, "pie angle (y)");
      const rows = data.rows.filter((r) => (num(r[vi]) ?? 0) > 0);
      const total = rows.reduce((a, r) => a + (num(r[vi]) ?? 0), 0);
      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.max(10, Math.min(width, height) / 2 - 16);
      let angle = -Math.PI / 2;
      rows.forEach((row, i) => {
        const share = (num(row[vi]) ?? 0) / total;
        const end = angle + share * Math.PI * 2;
        const large = end - angle > Math.PI ? 1 : 0;
        const [x0, y0, x1, y1] = [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle), cx + radius * Math.cos(end), cy + radius * Math.sin(end)];
        const d = share >= 0.9999
          ? `M${cx - radius},${cy}a${radius},${radius} 0 1,0 ${2 * radius},0a${radius},${radius} 0 1,0 ${-2 * radius},0`
          : `M${cx},${cy}L${x0.toFixed(1)},${y0.toFixed(1)}A${radius},${radius} 0 ${large},1 ${x1.toFixed(1)},${y1.toFixed(1)}Z`;
        const tuple = { [dimCol.name]: row[di] ?? null };
        marks.append(interact(s("path", { d, class: `tf-mark tf-c${i % 8}` }), tuple, `${tooltip(data, row)}\nShare: ${(share * 100).toFixed(1)}%`, options));
        angle = end;
      });
      break;
    }
    case "heatmap": {
      const [xCol, xi] = column(data, spec.x, "heatmap columns (x)");
      const [yCol, yi] = column(data, spec.y, "heatmap rows (y)");
      const [, ci] = column(data, spec.color, "heatmap colour (color)");
      const xs = [...new Set(data.rows.map((r) => String(r[xi] ?? "")))];
      const ys = [...new Set(data.rows.map((r) => String(r[yi] ?? "")))];
      const values = data.rows.map((r) => num(r[ci]) ?? 0);
      const [lo, hi] = [Math.min(...values), Math.max(...values)];
      const cw = plot.w / Math.max(1, xs.length);
      const ch = plot.h / Math.max(1, ys.length);
      data.rows.forEach((row) => {
        const v = num(row[ci]);
        const x = xs.indexOf(String(row[xi] ?? ""));
        const y = ys.indexOf(String(row[yi] ?? ""));
        const opacity = v === null ? 0.05 : hi === lo ? 0.85 : 0.15 + (0.85 * (v - lo)) / (hi - lo);
        const rect = s("rect", { x: plot.x + x * cw, y: plot.y + y * ch, width: Math.max(1, cw - 1), height: Math.max(1, ch - 1), class: "tf-mark tf-c0", "fill-opacity": opacity.toFixed(2) });
        marks.append(interact(rect, { [xCol.name]: row[xi] ?? null, [yCol.name]: row[yi] ?? null }, tooltip(data, row), options));
      });
      xs.forEach((v, i) => axis.append(s("text", { x: plot.x + (i + 0.5) * cw, y: plot.y + plot.h + 16, "text-anchor": "middle" }, v)));
      ys.forEach((v, i) => axis.append(s("text", { x: plot.x - 6, y: plot.y + (i + 0.5) * ch + 4, "text-anchor": "end" }, v)));
      break;
    }
    case "histogram": {
      const [valCol, vi] = column(data, spec.x, "histogram values (x)");
      const values = data.rows.map((r) => num(r[vi])).filter((v): v is number => v !== null);
      const bins = histogram(values, spec.bins ?? 10);
      const top = Math.max(1, ...bins.map((b) => b[2]));
      const bw = plot.w / Math.max(1, bins.length);
      bins.forEach(([start, end, count], i) => {
        const h = (count / top) * plot.h;
        const rect = s("rect", { x: plot.x + i * bw, y: plot.y + plot.h - h, width: Math.max(1, bw - 1), height: h, class: "tf-mark tf-c0" });
        rect.append(s("title", {}, `${formatAxis(start, valCol)} – ${formatAxis(end, valCol)}: ${count}`));
        marks.append(rect);
        if (i % Math.max(1, Math.ceil(bins.length / 6)) === 0) {
          axis.append(s("text", { x: plot.x + i * bw, y: plot.y + plot.h + 16, "text-anchor": "middle" }, formatAxis(start, valCol)));
        }
      });
      axis.append(s("text", { x: plot.x - 6, y: plot.y + 10, "text-anchor": "end" }, String(top)));
      break;
    }
    case "treemap": {
      const [dimCol, di] = column(data, spec.x, "treemap rectangles (x)");
      const [, vi] = column(data, spec.y, "treemap size (y)");
      const boxes = squarify(data.rows.map((r) => num(r[vi]) ?? 0), { x: 0, y: 0, w: width, h: height });
      data.rows.forEach((row, i) => {
        const b = boxes[i];
        if (!b || b.w < 1 || b.h < 1) return;
        const tuple = { [dimCol.name]: row[di] ?? null };
        const g = s("g", {});
        g.append(s("rect", { x: b.x, y: b.y, width: Math.max(1, b.w - 1), height: Math.max(1, b.h - 1), class: `tf-mark tf-c${i % 8}` }));
        if (b.w > 40 && b.h > 16) g.append(s("text", { x: b.x + 4, y: b.y + 14, class: "tf-label" }, formatValue(row[di] ?? null, dimCol)));
        marks.append(interact(g, tuple, tooltip(data, row), options));
      });
      break;
    }
    case "boxplot": {
      const [dimCol, di] = column(data, spec.x, "box plot groups (x)");
      const [valCol, vi] = column(data, spec.y, "box plot values (y)");
      const groups = new Map<string, { label: Scalar; values: number[] }>();
      for (const row of data.rows) {
        const v = num(row[vi]);
        if (v === null) continue;
        const k = String(row[di] ?? "");
        const group = groups.get(k) ?? { label: row[di] ?? null, values: [] };
        group.values.push(v);
        groups.set(k, group);
      }
      const all = [...groups.values()].flatMap((g) => g.values);
      const ticks = axisTicks(Math.min(...all), Math.max(...all));
      const [lo, hi] = [ticks[0] ?? 0, ticks.at(-1) ?? 1];
      const py = (v: number) => plot.y + plot.h - ((v - lo) / (hi - lo || 1)) * plot.h;
      const band = plot.w / Math.max(1, groups.size);
      [...groups.values()].forEach((group, i) => {
        const q = quartiles(group.values);
        const cx = plot.x + band * (i + 0.5);
        const half = Math.min(24, band * 0.3);
        const g = s("g", { class: "tf-mark tf-c0" });
        g.append(s("line", { x1: cx, x2: cx, y1: py(q.min), y2: py(q.max), class: "tf-whisker" }));
        g.append(s("rect", { x: cx - half, y: py(q.q3), width: half * 2, height: Math.max(1, py(q.q1) - py(q.q3)) }));
        g.append(s("line", { x1: cx - half, x2: cx + half, y1: py(q.median), y2: py(q.median), class: "tf-median" }));
        const text = `${formatValue(group.label, dimCol)}\nMedian: ${formatAxis(q.median, valCol)}\nQuartiles: ${formatAxis(q.q1, valCol)} – ${formatAxis(q.q3, valCol)}\nRange: ${formatAxis(q.min, valCol)} – ${formatAxis(q.max, valCol)}`;
        marks.append(interact(g, { [dimCol.name]: group.label }, text, options));
        axis.append(s("text", { x: cx, y: plot.y + plot.h + 16, "text-anchor": "middle" }, formatValue(group.label, dimCol)));
      });
      for (const t of ticks) axis.append(s("text", { x: plot.x - 6, y: py(t) + 4, "text-anchor": "end" }, formatAxis(t, valCol)));
      break;
    }
    case "gantt":
    case "bullet": {
      const [dimCol, di] = column(data, spec.y, `${spec.mark} rows (y)`);
      const [valCol, vi] = column(data, spec.x, `${spec.mark} ${spec.mark === "gantt" ? "start" : "value"} (x)`);
      const [, si] = column(data, spec.mark === "gantt" ? spec.size : spec.target, spec.mark === "gantt" ? "gantt length (size)" : "bullet target (target)");
      const isDate = valCol.type === "date" || valCol.type === "datetime";
      const day = 86_400_000;
      const ends = data.rows.flatMap((r) => {
        const start = num(r[vi]);
        const other = num(r[si]);
        if (start === null) return [];
        if (spec.mark === "gantt") return [start, start + (other ?? 0) * (isDate ? day : 1)];
        return other === null ? [start, 0] : [start, other, 0];
      });
      const ticks = axisTicks(Math.min(...ends), Math.max(...ends));
      const [lo, hi] = [ticks[0] ?? 0, ticks.at(-1) ?? 1];
      const px = (v: number) => plot.x + ((v - lo) / (hi - lo || 1)) * plot.w;
      const band = plot.h / Math.max(1, data.rows.length);
      data.rows.forEach((row, i) => {
        const start = num(row[vi]);
        const other = num(row[si]);
        if (start === null) return;
        const y = plot.y + band * i;
        const tuple = { [dimCol.name]: row[di] ?? null };
        if (spec.mark === "gantt") {
          const end = start + (other ?? 0) * (isDate ? day : 1);
          const bar = s("rect", { x: px(start), y: y + band * 0.2, width: Math.max(2, px(end) - px(start)), height: band * 0.6, class: "tf-mark tf-c0" });
          marks.append(interact(bar, tuple, tooltip(data, row), options));
        } else {
          const g = s("g", {});
          g.append(s("rect", { x: px(Math.min(0, start)), y: y + band * 0.3, width: Math.max(1, Math.abs(px(start) - px(0))), height: band * 0.4, class: "tf-mark tf-c0" }));
          if (other !== null) g.append(s("line", { x1: px(other), x2: px(other), y1: y + band * 0.15, y2: y + band * 0.85, class: "tf-target" }));
          marks.append(interact(g, tuple, tooltip(data, row), options));
        }
        axis.append(s("text", { x: plot.x - 6, y: y + band / 2 + 4, "text-anchor": "end" }, formatValue(row[di] ?? null, dimCol)));
      });
      for (const t of ticks) {
        const label = isDate ? new Date(t).toISOString().slice(0, 10) : formatAxis(t, valCol);
        axis.append(s("line", { x1: px(t), x2: px(t), y1: plot.y, y2: plot.y + plot.h, class: "tf-grid" }));
        axis.append(s("text", { x: px(t), y: plot.y + plot.h + 16, "text-anchor": "middle" }, label));
      }
      break;
    }
  }
  svg.append(axis, marks);
  return svg;
}
