/**
 * Table calculations over a query result (ADR-004: the JS engine is the source of truth).
 *
 * Phase 1 covers the common quick table calculations. Addressing follows Tableau: the calculation
 * moves along the "addressing" columns, and every other dimension column partitions the result.
 * `table_down`/`table_across` (and the default) address along all dimensions, in row order.
 */

import type { Column, Scalar } from "./protocol.js";

export interface TableCalc {
  name: string;
  type: string;
  of?: string;
  computeUsing?: string;
  addressing?: string[];
  partitioning?: string[];
  relativeTo?: "previous" | "next" | "first" | "last";
  direction?: "asc" | "desc";
  frame?: { start?: number | "first"; end?: number | "last" };
}

export interface Table {
  columns: Column[];
  rows: Scalar[][];
}

export class TableCalcError extends Error {}

const SUPPORTED = new Set([
  "running_sum",
  "running_avg",
  "running_min",
  "running_max",
  "running_count",
  "difference",
  "percent_difference",
  "percent_of_total",
  "moving_avg",
  "window_sum",
  "window_avg",
  "window_min",
  "window_max",
  "rank",
  "rank_dense",
  "rank_unique",
  "index",
  "first",
  "last",
  "size",
  "total",
]);

export function applyTableCalcs(table: Table, calcs: readonly TableCalc[]): Table {
  let current = table;
  for (const calc of calcs) current = applyOne(current, calc);
  return current;
}

function applyOne(table: Table, calc: TableCalc): Table {
  if (!SUPPORTED.has(calc.type)) throw new TableCalcError(`table calculation "${calc.type}" is not supported yet`);
  const names = table.columns.map((c) => c.name);
  const valueIndex = calc.of === undefined ? -1 : names.indexOf(calc.of);
  if (calc.of !== undefined && valueIndex < 0) throw new TableCalcError(`unknown column "${calc.of}"`);
  const dimensions = table.columns.filter((c) => c.role !== "measure").map((c) => c.name);
  const addressing =
    calc.computeUsing === "specific" ? (calc.addressing ?? []) : calc.computeUsing === "cell" ? [] : dimensions;
  for (const name of addressing) {
    if (!names.includes(name)) throw new TableCalcError(`unknown addressing column "${name}"`);
  }
  const partitionCols = (calc.partitioning ?? dimensions.filter((d) => !addressing.includes(d))).map((n) =>
    names.indexOf(n),
  );

  const partitions = new Map<string, number[]>();
  table.rows.forEach((row, i) => {
    const key = JSON.stringify(partitionCols.map((c) => row[c] ?? null));
    const list = partitions.get(key);
    if (list) list.push(i);
    else partitions.set(key, [i]);
  });

  const out: Scalar[] = new Array<Scalar>(table.rows.length).fill(null);
  for (const indices of partitions.values()) {
    const values = indices.map((i) => (valueIndex < 0 ? null : toNumber(table.rows[i]?.[valueIndex] ?? null)));
    const results = compute(calc, values);
    indices.forEach((rowIndex, k) => {
      out[rowIndex] = results[k] ?? null;
    });
  }
  const source = valueIndex >= 0 ? table.columns[valueIndex] : undefined;
  const type = calc.type.startsWith("rank") || ["index", "first", "last", "size", "running_count"].includes(calc.type);
  const column: Column = {
    name: calc.name,
    type: type ? "integer" : "float",
    role: "measure",
    // Ranks, indexes and counts are plain integers; only value-like results keep the source's format.
    ...(calc.type.startsWith("percent") ? { format: "0.0%" } : !type && source?.format ? { format: source.format } : {}),
  };
  return { columns: [...table.columns, column], rows: table.rows.map((row, i) => [...row, out[i] ?? null]) };
}

function toNumber(value: Scalar): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function compute(calc: TableCalc, values: (number | null)[]): (number | null)[] {
  const n = values.length;
  const present = values.filter((v): v is number => v !== null);
  switch (calc.type) {
    case "running_sum":
    case "running_avg":
    case "running_min":
    case "running_max":
    case "running_count": {
      let sum = 0;
      let count = 0;
      let min = Infinity;
      let max = -Infinity;
      return values.map((v) => {
        if (v !== null) {
          sum += v;
          count += 1;
          min = Math.min(min, v);
          max = Math.max(max, v);
        }
        if (calc.type === "running_count") return count;
        if (count === 0) return null;
        return { running_sum: sum, running_avg: sum / count, running_min: min, running_max: max }[
          calc.type as "running_sum"
        ];
      });
    }
    case "difference":
    case "percent_difference": {
      return values.map((v, i) => {
        const other = values[reference(calc.relativeTo ?? "previous", i, n)];
        if (v === null || other === null || other === undefined) return null;
        if (calc.type === "difference") return v - other;
        return other === 0 ? null : (v - other) / Math.abs(other);
      });
    }
    case "percent_of_total": {
      const total = present.reduce((a, b) => a + b, 0);
      return values.map((v) => (v === null || total === 0 ? null : v / total));
    }
    case "total": {
      const total = present.reduce((a, b) => a + b, 0);
      return values.map(() => (present.length ? total : null));
    }
    case "moving_avg":
    case "window_sum":
    case "window_avg":
    case "window_min":
    case "window_max": {
      const startOpt = calc.frame?.start ?? (calc.type === "moving_avg" ? -2 : "first");
      const endOpt = calc.frame?.end ?? (calc.type === "moving_avg" ? 0 : "last");
      return values.map((_, i) => {
        const lo = startOpt === "first" ? 0 : Math.max(0, i + startOpt);
        const hi = endOpt === "last" ? n - 1 : Math.min(n - 1, i + endOpt);
        const window = values.slice(lo, hi + 1).filter((v): v is number => v !== null);
        if (!window.length) return null;
        const sum = window.reduce((a, b) => a + b, 0);
        if (calc.type === "window_sum") return sum;
        if (calc.type === "window_min") return Math.min(...window);
        if (calc.type === "window_max") return Math.max(...window);
        return sum / window.length;
      });
    }
    case "rank":
    case "rank_dense":
    case "rank_unique": {
      const desc = (calc.direction ?? "desc") === "desc";
      const order = values
        .map((v, i) => ({ v, i }))
        .filter((e): e is { v: number; i: number } => e.v !== null)
        .sort((a, b) => (desc ? b.v - a.v : a.v - b.v) || a.i - b.i);
      const ranks: (number | null)[] = new Array<number | null>(n).fill(null);
      let dense = 0;
      order.forEach((entry, position) => {
        const previous = order[position - 1];
        const tie = previous !== undefined && previous.v === entry.v;
        if (!tie) dense += 1;
        const prevRank = previous ? (ranks[previous.i] ?? 0) : 0;
        ranks[entry.i] =
          calc.type === "rank_unique" ? position + 1 : calc.type === "rank_dense" ? dense : tie ? prevRank : position + 1;
      });
      return ranks;
    }
    case "index":
      return values.map((_, i) => i + 1);
    case "first":
      return values.map((_, i) => -i);
    case "last":
      return values.map((_, i) => n - 1 - i);
    case "size":
      return values.map(() => n);
    default:
      throw new TableCalcError(`table calculation "${calc.type}" is not supported yet`);
  }
}

function reference(relativeTo: string, i: number, n: number): number {
  if (relativeTo === "next") return i + 1;
  if (relativeTo === "first") return 0;
  if (relativeTo === "last") return n - 1;
  return i - 1;
}
