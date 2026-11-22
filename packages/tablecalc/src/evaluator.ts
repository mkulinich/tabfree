/**
 * Binds a parsed formula to the result's columns and evaluates it over one partition.
 *
 * - An aggregate such as SUM([Sales]) is the result column named "Sales" (the query already
 *   aggregated it); a bare field such as [Sales (Current)] is the column with exactly that name.
 *   Earlier table calcs are columns too, so they can be referenced by name.
 * - Table-calc functions see the partition as an ordered list of positions (normally one per row)
 *   and evaluate their arguments at other positions: WINDOW_*, RUNNING_*, LOOKUP, PREVIOUS_VALUE,
 *   RANK_*, INDEX, FIRST, LAST, SIZE and TOTAL.
 * - Only the whitelisted functions below exist; anything else is a TableCalcError.
 */

import type { BinaryOp, Expr } from "./parser.js";
import { TableCalcError, type Aggregation, type Column, type Scalar } from "./types.js";
import {
  compareScalars,
  correlation,
  covariance,
  extreme,
  isNumber,
  mean,
  normalize,
  numbers,
  pairs,
  percentile,
  sum,
  variance,
} from "./values.js";

/** Aggregations that name a result column; MIN/MAX with two arguments are the scalar forms. */
const AGGREGATES = new Set([
  "SUM",
  "AVG",
  "MIN",
  "MAX",
  "COUNT",
  "COUNTD",
  "MEDIAN",
  "ATTR",
  "AGG",
  "STDEV",
  "STDEVP",
  "VAR",
  "VARP",
  "PERCENTILE",
]);

/** Table-calc functions: name → [min args, max args]. */
const TABLE_FUNCTIONS: Record<string, [number, number]> = {
  INDEX: [0, 0],
  FIRST: [0, 0],
  LAST: [0, 0],
  SIZE: [0, 0],
  LOOKUP: [1, 2],
  PREVIOUS_VALUE: [1, 1],
  TOTAL: [1, 1],
  RUNNING_SUM: [1, 1],
  RUNNING_AVG: [1, 1],
  RUNNING_MIN: [1, 1],
  RUNNING_MAX: [1, 1],
  RUNNING_COUNT: [1, 1],
  WINDOW_SUM: [1, 3],
  WINDOW_AVG: [1, 3],
  WINDOW_MIN: [1, 3],
  WINDOW_MAX: [1, 3],
  WINDOW_COUNT: [1, 3],
  WINDOW_MEDIAN: [1, 3],
  WINDOW_STDEV: [1, 3],
  WINDOW_STDEVP: [1, 3],
  WINDOW_VAR: [1, 3],
  WINDOW_VARP: [1, 3],
  WINDOW_PERCENTILE: [2, 4],
  WINDOW_CORR: [2, 4],
  WINDOW_COVAR: [2, 4],
  WINDOW_COVARP: [2, 4],
  RANK: [1, 2],
  RANK_DENSE: [1, 2],
  RANK_MODIFIED: [1, 2],
  RANK_UNIQUE: [1, 2],
  RANK_PERCENTILE: [1, 2],
};

/** Row-level functions: name → [min args, max args]. */
const SCALAR_FUNCTIONS: Record<string, [number, number]> = {
  IIF: [3, 4],
  ZN: [1, 1],
  ISNULL: [1, 1],
  IFNULL: [2, 2],
  ABS: [1, 1],
  ROUND: [1, 2],
  FLOOR: [1, 1],
  CEILING: [1, 1],
  SQRT: [1, 1],
  SQUARE: [1, 1],
  POWER: [2, 2],
  EXP: [1, 1],
  LN: [1, 1],
  LOG: [1, 2],
  SIGN: [1, 1],
  DIV: [2, 2],
  INT: [1, 1],
  FLOAT: [1, 1],
  STR: [1, 1],
  LEN: [1, 1],
  UPPER: [1, 1],
  LOWER: [1, 1],
  PI: [0, 0],
  MIN: [2, 2],
  MAX: [2, 2],
};

export type Bound =
  | { k: "lit"; value: Scalar }
  | { k: "col"; index: number; name: string; agg: string | null }
  | { k: "unary"; op: "-" | "NOT"; arg: Bound }
  | { k: "binary"; op: BinaryOp; left: Bound; right: Bound }
  | { k: "if"; branches: [Bound, Bound][]; otherwise: Bound | null }
  | { k: "case"; subject: Bound; whens: [Bound, Bound][]; otherwise: Bound | null }
  | { k: "fn"; name: string; args: Bound[] }
  | { k: "tc"; name: string; args: Bound[]; desc: boolean };

/** Resolves fields to columns and checks every function name and arity. */
export function bind(expr: Expr, columns: readonly Column[]): Bound {
  const index = new Map(columns.map((c, i) => [c.name, i]));
  const column = (name: string, agg: string | null): Bound => {
    const i = index.get(name);
    if (i === undefined) throw new TableCalcError(`unknown column "${name}" in table calculation`);
    return { k: "col", index: i, name, agg };
  };
  const arity = (name: string, args: number, [min, max]: [number, number]): void => {
    if (args < min || args > max) {
      const want = min === max ? `${min}` : `${min} to ${max}`;
      throw new TableCalcError(`${name}() takes ${want} argument${max === 1 ? "" : "s"}, got ${args}`);
    }
  };
  const walk = (e: Expr): Bound => {
    switch (e.kind) {
      case "literal":
        return { k: "lit", value: e.value };
      case "field":
        return column(e.name, null);
      case "unary":
        return { k: "unary", op: e.op, arg: walk(e.arg) };
      case "binary":
        return { k: "binary", op: e.op, left: walk(e.left), right: walk(e.right) };
      case "if":
        return {
          k: "if",
          branches: e.branches.map(([c, v]) => [walk(c), walk(v)]),
          otherwise: e.otherwise ? walk(e.otherwise) : null,
        };
      case "case":
        return {
          k: "case",
          subject: walk(e.subject),
          whens: e.whens.map(([w, v]) => [walk(w), walk(v)]),
          otherwise: e.otherwise ? walk(e.otherwise) : null,
        };
      case "call": {
        const { name, args } = e;
        const scalarMinMax = (name === "MIN" || name === "MAX") && args.length === 2;
        if (AGGREGATES.has(name) && !scalarMinMax) {
          const expected = name === "PERCENTILE" ? 2 : 1;
          arity(name, args.length, [expected, expected]);
          const target = args[0];
          if (target?.kind !== "field") {
            throw new TableCalcError(
              `${name}() over an expression must be computed by the query: add it as a measure and reference the result column`,
            );
          }
          return column(target.name, name.toLowerCase());
        }
        const table = Object.hasOwn(TABLE_FUNCTIONS, name) ? TABLE_FUNCTIONS[name] : undefined;
        if (table) {
          arity(name, args.length, table);
          let desc = false;
          let bound = args;
          if (name.startsWith("RANK")) {
            desc = name !== "RANK_PERCENTILE";
            const dir = args[1];
            if (dir) {
              const value = dir.kind === "literal" && typeof dir.value === "string" ? dir.value.toLowerCase() : null;
              if (value !== "asc" && value !== "desc") throw new TableCalcError(`${name}() direction must be 'asc' or 'desc'`);
              desc = value === "desc";
              bound = args.slice(0, 1);
            }
          }
          if (name.startsWith("WINDOW_")) {
            const valueArgs = TABLE_FUNCTIONS[name]?.[0] ?? 1;
            if (args.length !== valueArgs && args.length !== valueArgs + 2) {
              throw new TableCalcError(`${name}() takes either no window bounds or both a start and an end`);
            }
          }
          return { k: "tc", name, args: bound.map((a) => walk(a)), desc };
        }
        const scalar = Object.hasOwn(SCALAR_FUNCTIONS, name) ? SCALAR_FUNCTIONS[name] : undefined;
        if (scalar) {
          arity(name, args.length, scalar);
          return { k: "fn", name, args: args.map((a) => walk(a)) };
        }
        throw new TableCalcError(`unknown function "${name}" in table calculation`);
      }
    }
  };
  return walk(expr);
}

/** Column indices a bound formula reads. */
export function referencedColumns(node: Bound, out: Set<number> = new Set()): Set<number> {
  switch (node.k) {
    case "col":
      out.add(node.index);
      break;
    case "unary":
      referencedColumns(node.arg, out);
      break;
    case "binary":
      referencedColumns(node.left, out);
      referencedColumns(node.right, out);
      break;
    case "if":
      for (const [c, v] of node.branches) {
        referencedColumns(c, out);
        referencedColumns(v, out);
      }
      if (node.otherwise) referencedColumns(node.otherwise, out);
      break;
    case "case":
      referencedColumns(node.subject, out);
      for (const [w, v] of node.whens) {
        referencedColumns(w, out);
        referencedColumns(v, out);
      }
      if (node.otherwise) referencedColumns(node.otherwise, out);
      break;
    case "fn":
    case "tc":
      for (const a of node.args) referencedColumns(a, out);
      break;
    case "lit":
      break;
  }
  return out;
}

const UNSET = Symbol("unset");
type Slot = Scalar | typeof UNSET;

interface RunningState {
  upto: number;
  sum: number;
  count: number;
  min: Scalar;
  max: Scalar;
}

export interface PartitionContext {
  rows: readonly (readonly Scalar[])[];
  columns: readonly Column[];
  /** Row indices for each position, in addressing order. A position's value is its first row's. */
  positions: readonly (readonly number[])[];
  aggregations: Readonly<Record<string, Aggregation>>;
}

/** Evaluates one bound formula at every position of one partition. */
export class PartitionEvaluator {
  private readonly m: number;
  private readonly memo = new Map<Bound, Slot[]>();
  private readonly windows = new Map<Bound, Map<string, Scalar>>();
  private readonly ranks = new Map<Bound, Scalar[]>();
  private readonly running = new Map<Bound, RunningState>();
  private readonly totals = new Map<Bound, Scalar>();
  private readonly rootValues: Slot[];
  private readonly busy: boolean[];

  constructor(
    private readonly root: Bound,
    private readonly ctx: PartitionContext,
  ) {
    this.m = ctx.positions.length;
    this.rootValues = new Array<Slot>(this.m).fill(UNSET);
    this.busy = new Array<boolean>(this.m).fill(false);
  }

  /** The formula's value at every position, computed in order so PREVIOUS_VALUE can recurse. */
  evaluateAll(): Scalar[] {
    const out: Scalar[] = [];
    for (let k = 0; k < this.m; k++) out.push(this.atRoot(k));
    return out;
  }

  private atRoot(k: number): Scalar {
    const known = this.rootValues[k];
    if (known !== UNSET && known !== undefined) return known;
    if (this.busy[k]) throw new TableCalcError("PREVIOUS_VALUE() depends on its own later values");
    this.busy[k] = true;
    const value = this.ev(this.root, k);
    this.busy[k] = false;
    this.rootValues[k] = value;
    return value;
  }

  private columnValue(index: number, k: number): Scalar {
    const row = this.ctx.positions[k]?.[0];
    if (row === undefined) return null;
    return normalize(this.ctx.rows[row]?.[index]);
  }

  private ev(node: Bound, k: number): Scalar {
    switch (node.k) {
      case "lit":
        return node.value;
      case "col":
        return this.columnValue(node.index, k);
      default: {
        let slots = this.memo.get(node);
        if (!slots) {
          slots = new Array<Slot>(this.m).fill(UNSET);
          this.memo.set(node, slots);
        }
        const known = slots[k];
        if (known !== UNSET && known !== undefined) return known;
        const value = normalize(this.compute(node, k));
        slots[k] = value;
        return value;
      }
    }
  }

  private compute(node: Bound, k: number): Scalar {
    switch (node.k) {
      case "lit":
      case "col":
        return this.ev(node, k);
      case "unary":
        return unary(node.op, this.ev(node.arg, k));
      case "binary":
        return this.binary(node, k);
      case "if": {
        for (const [cond, value] of node.branches) if (this.ev(cond, k) === true) return this.ev(value, k);
        return node.otherwise ? this.ev(node.otherwise, k) : null;
      }
      case "case": {
        const subject = this.ev(node.subject, k);
        for (const [when, value] of node.whens) {
          if (equal(subject, this.ev(when, k)) === true) return this.ev(value, k);
        }
        return node.otherwise ? this.ev(node.otherwise, k) : null;
      }
      case "fn":
        return this.scalarFunction(node.name, node.args, (a) => this.ev(a, k));
      case "tc":
        return this.tableFunction(node, k);
    }
  }

  private binary(node: Extract<Bound, { k: "binary" }>, k: number): Scalar {
    if (node.op === "AND" || node.op === "OR") {
      const left = this.ev(node.left, k);
      const short = node.op === "AND" ? false : true;
      if (left === short) return short;
      const right = this.ev(node.right, k);
      if (right === short) return short;
      if (left === null || right === null) return null;
      return !short;
    }
    return binaryOp(node.op, this.ev(node.left, k), this.ev(node.right, k));
  }

  private scalarFunction(name: string, args: readonly Bound[], at: (b: Bound) => Scalar): Scalar {
    const arg = (i: number): Scalar => {
      const a = args[i];
      return a ? at(a) : null;
    };
    const num = (i: number): number | null => {
      const v = arg(i);
      return isNumber(v) ? v : null;
    };
    const math = (fn: (x: number) => number): Scalar => {
      const x = num(0);
      return x === null ? null : fn(x);
    };
    switch (name) {
      case "IIF": {
        const test = arg(0);
        if (test === true) return arg(1);
        if (test === false) return arg(2);
        return args.length > 3 ? arg(3) : null;
      }
      case "ZN": {
        const v = arg(0);
        return v === null ? 0 : v;
      }
      case "ISNULL":
        return arg(0) === null;
      case "IFNULL": {
        const v = arg(0);
        return v === null ? arg(1) : v;
      }
      case "ABS":
        return math(Math.abs);
      case "ROUND": {
        const x = num(0);
        const digits = args.length > 1 ? num(1) : 0;
        if (x === null || digits === null) return null;
        const f = 10 ** Math.trunc(digits);
        return (Math.sign(x) * Math.round(Math.abs(x) * f + 1e-9)) / f;
      }
      case "FLOOR":
        return math(Math.floor);
      case "CEILING":
        return math(Math.ceil);
      case "SQRT":
        return math((x) => (x < 0 ? NaN : Math.sqrt(x)));
      case "SQUARE":
        return math((x) => x * x);
      case "POWER": {
        const x = num(0);
        const y = num(1);
        return x === null || y === null ? null : x ** y;
      }
      case "EXP":
        return math(Math.exp);
      case "LN":
        return math((x) => (x <= 0 ? NaN : Math.log(x)));
      case "LOG": {
        const x = num(0);
        const base = args.length > 1 ? num(1) : 10;
        if (x === null || base === null || x <= 0 || base <= 0 || base === 1) return null;
        return Math.log(x) / Math.log(base);
      }
      case "SIGN":
        return math(Math.sign);
      case "DIV": {
        const x = num(0);
        const y = num(1);
        return x === null || y === null || y === 0 ? null : Math.trunc(x / y);
      }
      case "INT": {
        const v = arg(0);
        if (typeof v === "string") return normalize(Math.trunc(Number(v)));
        if (typeof v === "boolean") return v ? 1 : 0;
        return v === null ? null : Math.trunc(v);
      }
      case "FLOAT": {
        const v = arg(0);
        if (typeof v === "string") return v.trim() === "" ? null : normalize(Number(v));
        if (typeof v === "boolean") return v ? 1 : 0;
        return v;
      }
      case "STR": {
        const v = arg(0);
        return v === null ? null : String(v);
      }
      case "LEN": {
        const v = arg(0);
        return typeof v === "string" ? v.length : null;
      }
      case "UPPER": {
        const v = arg(0);
        return typeof v === "string" ? v.toUpperCase() : null;
      }
      case "LOWER": {
        const v = arg(0);
        return typeof v === "string" ? v.toLowerCase() : null;
      }
      case "PI":
        return Math.PI;
      case "MIN":
      case "MAX": {
        const a = arg(0);
        const b = arg(1);
        if (a === null || b === null) return null;
        const c = compareScalars(a, b);
        return name === "MIN" ? (c <= 0 ? a : b) : c >= 0 ? a : b;
      }
      default:
        throw new TableCalcError(`unknown function "${name}" in table calculation`);
    }
  }

  private offset(node: Bound | undefined, k: number): number | null {
    if (!node) return null;
    const v = this.ev(node, k);
    return isNumber(v) ? Math.trunc(v) : null;
  }

  private tableFunction(node: Extract<Bound, { k: "tc" }>, k: number): Scalar {
    const m = this.m;
    const [a0, a1] = node.args;
    switch (node.name) {
      case "INDEX":
        return k + 1;
      case "FIRST":
        return -k;
      case "LAST":
        return m - 1 - k;
      case "SIZE":
        return m;
      case "LOOKUP": {
        const off = a1 ? this.offset(a1, k) : 0;
        if (off === null || !a0) return null;
        const target = k + off;
        return target >= 0 && target < m ? this.ev(a0, target) : null;
      }
      case "PREVIOUS_VALUE":
        if (!a0) return null;
        return k === 0 ? this.ev(a0, 0) : this.atRoot(k - 1);
      case "TOTAL":
        return a0 ? this.total(a0) : null;
      case "RUNNING_SUM":
      case "RUNNING_AVG":
      case "RUNNING_MIN":
      case "RUNNING_MAX":
      case "RUNNING_COUNT":
        return this.runningAt(node, k);
      case "RANK":
      case "RANK_DENSE":
      case "RANK_MODIFIED":
      case "RANK_UNIQUE":
      case "RANK_PERCENTILE":
        return this.rankAt(node, k);
      default:
        return this.windowAt(node, k);
    }
  }

  private runningAt(node: Extract<Bound, { k: "tc" }>, k: number): Scalar {
    const arg = node.args[0];
    if (!arg) return null;
    let state = this.running.get(node);
    if (!state) {
      state = { upto: -1, sum: 0, count: 0, min: null, max: null };
      this.running.set(node, state);
    }
    let slots = this.memo.get(node);
    if (!slots) {
      slots = new Array<Slot>(this.m).fill(UNSET);
      this.memo.set(node, slots);
    }
    while (state.upto < k) {
      const j = ++state.upto;
      const v = this.ev(arg, j);
      if (v !== null) {
        if (isNumber(v)) state.sum += v;
        state.count += 1;
        state.min = extreme([state.min, v], "min");
        state.max = extreme([state.max, v], "max");
      }
      let out: Scalar;
      switch (node.name) {
        case "RUNNING_SUM":
          out = state.count ? state.sum : null;
          break;
        case "RUNNING_AVG":
          out = state.count ? state.sum / state.count : null;
          break;
        case "RUNNING_MIN":
          out = state.min;
          break;
        case "RUNNING_MAX":
          out = state.max;
          break;
        default:
          out = state.count;
      }
      slots[j] = out;
    }
    const v = slots[k];
    return v === UNSET || v === undefined ? null : v;
  }

  private rankAt(node: Extract<Bound, { k: "tc" }>, k: number): Scalar {
    let ranks = this.ranks.get(node);
    if (!ranks) {
      const arg = node.args[0];
      const values: Scalar[] = [];
      for (let j = 0; j < this.m; j++) values.push(arg ? this.ev(arg, j) : null);
      ranks = rank(values, node.name, node.desc);
      this.ranks.set(node, ranks);
    }
    return ranks[k] ?? null;
  }

  private windowAt(node: Extract<Bound, { k: "tc" }>, k: number): Scalar {
    const valueArgs = TABLE_FUNCTIONS[node.name]?.[0] ?? 1;
    const bounds = node.args.slice(valueArgs);
    let lo = 0;
    let hi = this.m - 1;
    if (bounds.length === 2) {
      const s = this.offset(bounds[0], k);
      const e = this.offset(bounds[1], k);
      if (s === null || e === null) return null;
      lo = Math.max(0, k + s);
      hi = Math.min(this.m - 1, k + e);
    }
    let cache = this.windows.get(node);
    if (!cache) {
      cache = new Map();
      this.windows.set(node, cache);
    }
    // WINDOW_PERCENTILE's fraction is evaluated at the current row (it is normally a constant).
    const fraction = node.name === "WINDOW_PERCENTILE" && node.args[1] ? this.ev(node.args[1], k) : null;
    const key = `${lo}:${hi}:${String(fraction)}`;
    if (cache.has(key)) return cache.get(key) ?? null;
    const collect = (arg: Bound | undefined): Scalar[] => {
      const out: Scalar[] = [];
      if (!arg) return out;
      for (let j = lo; j <= hi; j++) out.push(this.ev(arg, j));
      return out;
    };
    const values = collect(node.args[0]);
    let result: Scalar;
    switch (node.name) {
      case "WINDOW_SUM":
        result = sum(numbers(values));
        break;
      case "WINDOW_AVG":
        result = mean(numbers(values));
        break;
      case "WINDOW_MIN":
        result = extreme(values, "min");
        break;
      case "WINDOW_MAX":
        result = extreme(values, "max");
        break;
      case "WINDOW_COUNT":
        result = values.filter((v) => v !== null).length;
        break;
      case "WINDOW_MEDIAN":
        result = percentile(numbers(values), 0.5);
        break;
      case "WINDOW_STDEV":
      case "WINDOW_STDEVP": {
        const v = variance(numbers(values), node.name === "WINDOW_STDEVP");
        result = v === null ? null : Math.sqrt(v);
        break;
      }
      case "WINDOW_VAR":
      case "WINDOW_VARP":
        result = variance(numbers(values), node.name === "WINDOW_VARP");
        break;
      case "WINDOW_PERCENTILE":
        result = isNumber(fraction) ? percentile(numbers(values), fraction) : null;
        break;
      case "WINDOW_CORR":
      case "WINDOW_COVAR":
      case "WINDOW_COVARP": {
        const [xs, ys] = pairs(values, collect(node.args[1]));
        result =
          node.name === "WINDOW_CORR" ? correlation(xs, ys) : covariance(xs, ys, node.name === "WINDOW_COVARP");
        break;
      }
      default:
        throw new TableCalcError(`unknown function "${node.name}" in table calculation`);
    }
    result = normalize(result);
    cache.set(key, result);
    return result;
  }

  /**
   * TOTAL(expr): the expression evaluated over the whole partition, with each aggregate
   * re-aggregated from the result's values. SUM and COUNT add up, MIN and MAX take the extreme,
   * so TOTAL(SUM([a]) / SUM([b])) is SUM over the partition of a divided by that of b, as in Tableau.
   * AVG is the mean of the partition's averages (exact only for equal-sized groups); COUNTD,
   * MEDIAN, STDEV, VAR and PERCENTILE cannot be re-aggregated and throw.
   */
  private total(node: Bound): Scalar {
    if (this.totals.has(node)) return this.totals.get(node) ?? null;
    const value = normalize(this.totalOf(node));
    this.totals.set(node, value);
    return value;
  }

  private totalOf(node: Bound): Scalar {
    switch (node.k) {
      case "lit":
        return node.value;
      case "col": {
        const values: Scalar[] = [];
        for (let j = 0; j < this.m; j++) values.push(this.columnValue(node.index, j));
        const column = this.ctx.columns[node.index];
        // A bare field or AGG([x]) says nothing about how the query aggregated it.
        const declared = node.agg === null || node.agg === "agg" ? undefined : node.agg;
        const agg = declared ?? this.ctx.aggregations[node.name] ?? (column?.role === "measure" ? "sum" : "attr");
        return reaggregate(agg, values, node.name);
      }
      case "unary":
        return unary(node.op, this.totalOf(node.arg));
      case "binary": {
        if (node.op === "AND" || node.op === "OR") {
          const l = this.totalOf(node.left);
          const r = this.totalOf(node.right);
          const short = node.op === "OR";
          if (l === short || r === short) return short;
          return l === null || r === null ? null : !short;
        }
        return binaryOp(node.op, this.totalOf(node.left), this.totalOf(node.right));
      }
      case "if": {
        for (const [c, v] of node.branches) if (this.totalOf(c) === true) return this.totalOf(v);
        return node.otherwise ? this.totalOf(node.otherwise) : null;
      }
      case "case": {
        const s = this.totalOf(node.subject);
        for (const [w, v] of node.whens) if (equal(s, this.totalOf(w)) === true) return this.totalOf(v);
        return node.otherwise ? this.totalOf(node.otherwise) : null;
      }
      case "fn":
        return this.scalarFunction(node.name, node.args, (a) => this.totalOf(a));
      case "tc":
        throw new TableCalcError(`TOTAL() cannot contain the table calculation ${node.name}()`);
    }
  }
}

function reaggregate(agg: string, values: readonly Scalar[], name: string): Scalar {
  switch (agg) {
    case "sum":
    case "count":
      return sum(numbers(values));
    case "min":
      return extreme(values, "min");
    case "max":
      return extreme(values, "max");
    case "avg":
      return mean(numbers(values));
    case "attr": {
      const present = values.filter((v) => v !== null);
      const first = present[0] ?? null;
      return present.every((v) => v === first) ? first : null;
    }
    default:
      throw new TableCalcError(
        `TOTAL(${agg.toUpperCase()}([${name}])) cannot be re-aggregated from the result; compute it in the query`,
      );
  }
}

function unary(op: "-" | "NOT", v: Scalar): Scalar {
  if (op === "-") return isNumber(v) ? -v : null;
  return typeof v === "boolean" ? !v : null;
}

function equal(a: Scalar, b: Scalar): boolean | null {
  if (a === null || b === null) return null;
  return a === b;
}

function binaryOp(op: BinaryOp, a: Scalar, b: Scalar): Scalar {
  if (a === null || b === null) return null;
  switch (op) {
    case "+":
      if (typeof a === "string" && typeof b === "string") return a + b;
      return isNumber(a) && isNumber(b) ? a + b : null;
    case "-":
    case "*":
    case "/":
    case "%":
    case "^": {
      if (!isNumber(a) || !isNumber(b)) return null;
      if (op === "-") return a - b;
      if (op === "*") return a * b;
      if (op === "^") return a ** b;
      if (b === 0) return null;
      return op === "/" ? a / b : a % b;
    }
    case "=":
      return a === b;
    case "!=":
      return a !== b;
    default: {
      if (typeof a !== typeof b) return null;
      const c = compareScalars(a, b);
      if (op === "<") return c < 0;
      if (op === "<=") return c <= 0;
      if (op === ">") return c > 0;
      return c >= 0;
    }
  }
}

/**
 * Tableau's ranking functions over one partition. Nulls get no rank and are not counted.
 * For (6, 9, 9, 14) descending: RANK (4, 2, 2, 1), RANK_DENSE (3, 2, 2, 1), RANK_MODIFIED
 * (4, 3, 3, 1), RANK_UNIQUE (4, 2, 3, 1) with ties in partition order; RANK_PERCENTILE
 * (ascending by default) is (modified rank - 1) / (n - 1): (0, 0.67, 0.67, 1).
 */
export function rank(values: readonly Scalar[], fn: string, desc: boolean): Scalar[] {
  const entries = values
    .map((v, i) => ({ v, i }))
    .filter((e): e is { v: Exclude<Scalar, null>; i: number } => e.v !== null)
    .sort((a, b) => (desc ? compareScalars(b.v, a.v) : compareScalars(a.v, b.v)) || a.i - b.i);
  const out: Scalar[] = new Array<Scalar>(values.length).fill(null);
  const n = entries.length;
  let dense = 0;
  let start = 0;
  while (start < n) {
    let end = start;
    const first = entries[start];
    if (!first) break;
    while (end + 1 < n && compareScalars(entries[end + 1]?.v ?? null, first.v) === 0) end++;
    dense += 1;
    for (let p = start; p <= end; p++) {
      const e = entries[p];
      if (!e) continue;
      switch (fn) {
        case "RANK":
          out[e.i] = start + 1;
          break;
        case "RANK_DENSE":
          out[e.i] = dense;
          break;
        case "RANK_MODIFIED":
          out[e.i] = end + 1;
          break;
        case "RANK_UNIQUE":
          out[e.i] = p + 1;
          break;
        default:
          out[e.i] = n === 1 ? 1 : end / (n - 1);
      }
    }
    start = end + 1;
  }
  return out;
}
