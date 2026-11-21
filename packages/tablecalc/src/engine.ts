/**
 * `applyTableCalcs`: runs a query's table calculations over its result, in Tableau's order:
 * densify, then each calc in declaration order (a calc can read earlier calcs' columns), then
 * table-calculation filters, which hide rows without changing any calculated value.
 */

import { layoutOf, partitionRows, type Layout } from "./addressing.js";
import { densify } from "./densify.js";
import { bind, PartitionEvaluator, referencedColumns, type Bound } from "./evaluator.js";
import { parseExpression } from "./parser.js";
import { quickCalcExpression } from "./quick.js";
import {
  TableCalcError,
  type AddressingSpec,
  type Column,
  type ColumnType,
  type Scalar,
  type SecondaryCalc,
  type Table,
  type TableCalc,
  type TableCalcOptions,
  type TableCalcType,
} from "./types.js";

const INTEGER_TYPES = new Set<TableCalcType>([
  "rank",
  "rank_dense",
  "rank_modified",
  "rank_unique",
  "index",
  "first",
  "last",
  "size",
  "running_count",
  "window_count",
]);
const PERCENT_TYPES = new Set<TableCalcType>(["percent_difference", "percent_of_total", "rank_percentile"]);
const INTEGER_FUNCTIONS = new Set([
  "INDEX",
  "FIRST",
  "LAST",
  "SIZE",
  "RANK",
  "RANK_DENSE",
  "RANK_MODIFIED",
  "RANK_UNIQUE",
  "RUNNING_COUNT",
  "WINDOW_COUNT",
]);
const VALUE_FUNCTIONS = new Set([
  "RUNNING_SUM",
  "RUNNING_AVG",
  "RUNNING_MIN",
  "RUNNING_MAX",
  "WINDOW_SUM",
  "WINDOW_AVG",
  "WINDOW_MIN",
  "WINDOW_MAX",
  "WINDOW_MEDIAN",
  "WINDOW_PERCENTILE",
  "LOOKUP",
  "TOTAL",
]);

interface Context {
  layout: Layout;
  options: TableCalcOptions;
}

export function applyTableCalcs(table: Table, calcs: readonly TableCalc[], options: TableCalcOptions = {}): Table {
  const { table: dense, inserted } = densify(table, options.densify);
  let current = dense;
  const ctx: Context = { layout: layoutOf(dense, options.across), options };
  for (const calc of calcs) current = applyOne(current, calc, ctx);

  let keep = current.rows.map((_, i) => !(options.dropDensified && inserted[i]));
  for (const filter of options.filters ?? []) {
    const values = evaluate(current, filter.expr, filter, ctx);
    keep = keep.map((k, i) => k && values[i] === true);
  }
  if (keep.every(Boolean)) return current;
  return { columns: current.columns, rows: current.rows.filter((_, i) => keep[i]) };
}

/** Evaluates a Tableau table-calc formula at every row of the table. */
export function evaluateExpression(
  table: Table,
  expr: string,
  spec: AddressingSpec = {},
  options: TableCalcOptions = {},
): Scalar[] {
  return evaluate(table, expr, spec, { layout: layoutOf(table, options.across), options });
}

function evaluate(table: Table, expr: string, spec: AddressingSpec, ctx: Context): Scalar[] {
  return evaluateBound(table, bind(parseExpression(expr), table.columns), spec, ctx);
}

function evaluateBound(table: Table, root: Bound, spec: AddressingSpec, ctx: Context): Scalar[] {
  const out: Scalar[] = new Array<Scalar>(table.rows.length).fill(null);
  for (const partition of partitionRows(table, spec, ctx.layout)) {
    const evaluator = new PartitionEvaluator(root, {
      rows: table.rows,
      columns: table.columns,
      positions: partition.positions,
      aggregations: ctx.options.aggregations ?? {},
    });
    const values = evaluator.evaluateAll();
    partition.positions.forEach((rows, k) => {
      for (const r of rows) out[r] = values[k] ?? null;
    });
  }
  return out;
}

function withColumn(table: Table, column: Column, values: readonly Scalar[]): Table {
  const at = table.columns.findIndex((c) => c.name === column.name);
  if (at >= 0) {
    return {
      columns: table.columns.map((c, i) => (i === at ? column : c)),
      rows: table.rows.map((row, r) => row.map((v, i) => (i === at ? (values[r] ?? null) : v))),
    };
  }
  return { columns: [...table.columns, column], rows: table.rows.map((row, r) => [...row, values[r] ?? null]) };
}

function sourceColumn(table: Table, of: string | undefined): Column | undefined {
  if (of === undefined) return undefined;
  const column = table.columns.find((c) => c.name === of);
  if (!column) throw new TableCalcError(`unknown column "${of}"`);
  return column;
}

function quickColumn(name: string, calc: TableCalc | SecondaryCalc, source: Column | undefined): Column {
  const isPercentile = calc.type === "percentile" && calc.percentile === undefined;
  if (INTEGER_TYPES.has(calc.type)) return { name, type: "integer", role: "measure" };
  if (PERCENT_TYPES.has(calc.type) || isPercentile) return { name, type: "float", role: "measure", format: "0.0%" };
  const type: ColumnType = calc.type === "lookup" && source ? source.type : "float";
  return { name, type, role: "measure", ...(source?.format ? { format: source.format } : {}) };
}

function customColumn(name: string, root: Bound, values: readonly Scalar[], columns: readonly Column[]): Column {
  if (root.k === "tc" && INTEGER_FUNCTIONS.has(root.name)) return { name, type: "integer", role: "measure" };
  const present = values.filter((v) => v !== null);
  if (present.length && present.every((v) => typeof v === "boolean")) return { name, type: "boolean", role: "measure" };
  if (present.length && present.every((v) => typeof v === "string")) return { name, type: "string", role: "measure" };
  let format: string | undefined;
  if (root.k === "tc" && VALUE_FUNCTIONS.has(root.name)) {
    const refs = [...referencedColumns(root)];
    const only = refs.length === 1 && refs[0] !== undefined ? columns[refs[0]] : undefined;
    format = only?.format;
  }
  return { name, type: "float", role: "measure", ...(format ? { format } : {}) };
}

function addressingOf(spec: AddressingSpec): AddressingSpec {
  const out: AddressingSpec = {};
  if (spec.computeUsing !== undefined) out.computeUsing = spec.computeUsing;
  if (spec.addressing !== undefined) out.addressing = spec.addressing;
  if (spec.partitioning !== undefined) out.partitioning = spec.partitioning;
  if (spec.orderBy !== undefined) out.orderBy = spec.orderBy;
  if (spec.atLevel !== undefined) out.atLevel = spec.atLevel;
  if (spec.restartEvery !== undefined) out.restartEvery = spec.restartEvery;
  return out;
}

function applyOne(table: Table, calc: TableCalc, ctx: Context): Table {
  let column: Column;
  let values: Scalar[];
  if (calc.type === "custom") {
    if (!calc.expr) throw new TableCalcError(`custom table calculation "${calc.name}" needs "expr"`);
    const root = bind(parseExpression(calc.expr), table.columns);
    values = evaluateBound(table, root, calc, ctx);
    column = customColumn(calc.name, root, values, table.columns);
  } else {
    const source = sourceColumn(table, calc.of);
    values = evaluate(table, quickCalcExpression(calc), calc, ctx);
    column = quickColumn(calc.name, calc, source);
  }
  if (!calc.then) return withColumn(table, column, values);

  // Secondary calc: computed over the primary's values. Without its own compute-using it
  // inherits the primary's addressing, as Tableau's dialog does.
  const then = calc.then;
  if ((then.type as string) === "custom") throw new TableCalcError("a secondary calculation cannot be custom");
  const primaryName = `\u0000primary:${calc.name}`;
  const primary = withColumn(table, { ...column, name: primaryName }, values);
  const ownAddressing = then.computeUsing !== undefined || then.addressing !== undefined || then.partitioning !== undefined;
  const spec = ownAddressing ? addressingOf(then) : addressingOf(calc);
  const secondary = evaluate(primary, quickCalcExpression({ ...then, of: primaryName }), spec, ctx);
  const result = quickColumn(calc.name, then, { ...column });
  return withColumn(table, result, secondary);
}
