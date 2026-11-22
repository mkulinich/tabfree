/**
 * Quick table calculations as Tableau formulas. Each declarative `tableCalc` entry becomes the
 * formula Tableau itself writes for that quick calc, so quick and custom calcs share one evaluator:
 *
 * | type | formula |
 * |---|---|
 * | running_sum (avg, min, max, count) | `RUNNING_SUM([x])` |
 * | window_sum (avg, min, max, count, median) | `WINDOW_SUM([x])`, or `WINDOW_SUM([x], start, end)` with a frame |
 * | moving_avg | `WINDOW_AVG([x], -2, 0)` (frame default: the two previous values and the current one) |
 * | difference | `ZN([x]) - LOOKUP(ZN([x]), -1)` (relativeTo next: 1, first: FIRST(), last: LAST()) |
 * | percent_difference | `(ZN([x]) - LOOKUP(ZN([x]), -1)) / ABS(LOOKUP(ZN([x]), -1))` |
 * | percent_of_total | `[x] / TOTAL([x])` |
 * | percentile | `RANK_PERCENTILE([x], 'asc')`; with `percentile`: `WINDOW_PERCENTILE([x], p[, start, end])` |
 * | rank, rank_dense, rank_modified, rank_unique | `RANK([x], 'desc')` (direction default desc) |
 * | rank_percentile | `RANK_PERCENTILE([x], 'asc')` (direction default asc) |
 * | lookup | `LOOKUP([x], offset)`; relativeTo first/last: `LOOKUP([x], FIRST() + offset)` |
 * | index, first, last, size | `INDEX()`, `FIRST()`, `LAST()`, `SIZE()` |
 * | total | `TOTAL([x])` |
 *
 * `nullIfIncomplete` wraps a framed calc in `IIF(FIRST() <= start AND LAST() >= end, ..., NULL)`.
 */

import { TableCalcError, type Frame, type RelativeTo, type TableCalcType } from "./types.js";

export interface QuickCalc {
  type: TableCalcType;
  of?: string;
  frame?: Frame;
  relativeTo?: RelativeTo;
  offset?: number;
  percentile?: number;
  direction?: "asc" | "desc";
  nullIfIncomplete?: boolean;
}

/** A field reference for a result column name: `[name]` with `]` doubled. */
export function fieldRef(name: string): string {
  return `[${name.replaceAll("]", "]]")}]`;
}

const WINDOW: Partial<Record<TableCalcType, string>> = {
  window_sum: "WINDOW_SUM",
  window_avg: "WINDOW_AVG",
  window_min: "WINDOW_MIN",
  window_max: "WINDOW_MAX",
  window_count: "WINDOW_COUNT",
  window_median: "WINDOW_MEDIAN",
  moving_avg: "WINDOW_AVG",
};

const RUNNING: Partial<Record<TableCalcType, string>> = {
  running_sum: "RUNNING_SUM",
  running_avg: "RUNNING_AVG",
  running_min: "RUNNING_MIN",
  running_max: "RUNNING_MAX",
  running_count: "RUNNING_COUNT",
};

const RANKS: Partial<Record<TableCalcType, string>> = {
  rank: "RANK",
  rank_dense: "RANK_DENSE",
  rank_modified: "RANK_MODIFIED",
  rank_unique: "RANK_UNIQUE",
};

function num(n: number): string {
  if (!Number.isFinite(n)) throw new TableCalcError(`invalid number ${n} in table calculation`);
  return String(n);
}

/** The Tableau formula for a quick table calculation. */
export function quickCalcExpression(calc: QuickCalc): string {
  const { type } = calc;
  if (type === "custom") throw new TableCalcError('a "custom" table calculation needs "expr"');
  if (type === "index") return "INDEX()";
  if (type === "first") return "FIRST()";
  if (type === "last") return "LAST()";
  if (type === "size") return "SIZE()";
  if (calc.of === undefined) throw new TableCalcError(`table calculation "${type}" needs "of"`);
  const f = fieldRef(calc.of);

  const frameArgs = (defaults?: { start: number; end: number }): { args: string; guard: string[] } => {
    const frame = calc.frame ?? defaults;
    if (!frame) return { args: "", guard: [] };
    const start = frame.start ?? "first";
    const end = frame.end ?? "last";
    const guard: string[] = [];
    if (typeof start === "number") guard.push(`FIRST() <= ${num(start)}`);
    if (typeof end === "number") guard.push(`LAST() >= ${num(end)}`);
    const s = start === "first" ? "FIRST()" : num(start);
    const e = end === "last" ? "LAST()" : num(end);
    return { args: `, ${s}, ${e}`, guard };
  };
  const guarded = (expr: string, guard: string[]): string =>
    calc.nullIfIncomplete && guard.length ? `IIF(${guard.join(" AND ")}, ${expr}, NULL)` : expr;
  const relative = (): string => {
    switch (calc.relativeTo ?? "previous") {
      case "next":
        return "1";
      case "first":
        return "FIRST()";
      case "last":
        return "LAST()";
      default:
        return "-1";
    }
  };

  const running = RUNNING[type];
  if (running) return `${running}(${f})`;
  const window = WINDOW[type];
  if (window) {
    const { args, guard } = frameArgs(type === "moving_avg" ? { start: -2, end: 0 } : undefined);
    return guarded(`${window}(${f}${args})`, guard);
  }
  const rank = RANKS[type];
  if (rank) return `${rank}(${f}, '${calc.direction ?? "desc"}')`;
  switch (type) {
    case "difference":
      return `ZN(${f}) - LOOKUP(ZN(${f}), ${relative()})`;
    case "percent_difference": {
      const other = `LOOKUP(ZN(${f}), ${relative()})`;
      return `(ZN(${f}) - ${other}) / ABS(${other})`;
    }
    case "percent_of_total":
      return `${f} / TOTAL(${f})`;
    case "total":
      return `TOTAL(${f})`;
    case "percentile": {
      if (calc.percentile === undefined) return `RANK_PERCENTILE(${f}, '${calc.direction ?? "asc"}')`;
      const { args, guard } = frameArgs();
      return guarded(`WINDOW_PERCENTILE(${f}, ${num(calc.percentile)}${args})`, guard);
    }
    case "rank_percentile":
      return `RANK_PERCENTILE(${f}, '${calc.direction ?? "asc"}')`;
    case "lookup": {
      const offset = calc.offset ?? 0;
      if (calc.relativeTo === "first" || calc.relativeTo === "last") {
        const base = calc.relativeTo === "first" ? "FIRST()" : "LAST()";
        return `LOOKUP(${f}, ${base} + ${num(offset)})`;
      }
      if (calc.offset === undefined && calc.relativeTo) return `LOOKUP(${f}, ${relative()})`;
      return `LOOKUP(${f}, ${num(offset)})`;
    }
    default:
      throw new TableCalcError(`table calculation "${String(type)}" is not supported`);
  }
}
