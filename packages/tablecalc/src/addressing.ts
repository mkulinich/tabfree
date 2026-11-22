/**
 * Addressing and partitioning over a flat result.
 *
 * There is no visual layout yet, so the engine models one: every dimension column is on the Rows
 * shelf ("down") in column order, except the ones `options.across` puts on the Columns shelf. A
 * pane is the area left when the innermost dimension of each shelf is removed. Compute Using then
 * maps to addressing fields (outermost first; everything else partitions):
 *
 * | computeUsing | addressing |
 * |---|---|
 * | table_across | every across dimension |
 * | table_down | every down dimension |
 * | table_across_then_down | down dimensions, then across ones (moves across a row, then to the next row) |
 * | table_down_then_across | across dimensions, then down ones |
 * | pane_across / pane_down | the innermost across / down dimension |
 * | pane_across_then_down | innermost down, then innermost across |
 * | pane_down_then_across | innermost across, then innermost down |
 * | cell | none: every row is its own partition |
 * | specific | `addressing`, as given |
 * | (unset) | `specific` when `addressing` is given, else every dimension (table across then down) |
 *
 * `partitioning`, when given, moves those fields out of the addressing. "Restarting every" moves the
 * named addressing field and those above it to partitioning. Along the addressing, rows are ordered by
 * `orderBy` (Tableau's custom sort), then by the addressing fields' values (ascending, nulls last),
 * then by their position in the result. "At the level" makes rows that share the addressing fields
 * down to that level one position, so the calculation steps only when that level changes.
 */

import { TableCalcError, type AddressingSpec, type Scalar, type Table } from "./types.js";
import { compareScalars, normalize, tupleKey } from "./values.js";

export interface Layout {
  dimensions: string[];
  across: string[];
  down: string[];
}

export function layoutOf(table: Table, across: readonly string[] = []): Layout {
  const names = table.columns.map((c) => c.name);
  for (const name of across) {
    if (!names.includes(name)) throw new TableCalcError(`unknown "across" column "${name}"`);
  }
  const dimensions = table.columns.filter((c) => c.role !== "measure").map((c) => c.name);
  const acrossDims = across.filter((n) => dimensions.includes(n));
  return { dimensions, across: acrossDims, down: dimensions.filter((d) => !acrossDims.includes(d)) };
}

export interface Partition {
  /** Row indices of each position, in addressing order. */
  positions: number[][];
}

function innermost(list: readonly string[]): string[] {
  const last = list.at(-1);
  return last === undefined ? [] : [last];
}

export function addressingFields(spec: AddressingSpec, layout: Layout): string[] {
  const { across, down } = layout;
  const mode = spec.computeUsing ?? (spec.addressing ? "specific" : undefined);
  switch (mode) {
    case undefined:
      return [...down, ...across];
    case "table_across":
      return [...across];
    case "table_down":
      return [...down];
    case "table_across_then_down":
      return [...down, ...across];
    case "table_down_then_across":
      return [...across, ...down];
    case "pane_across":
      return innermost(across);
    case "pane_down":
      return innermost(down);
    case "pane_across_then_down":
      return [...innermost(down), ...innermost(across)];
    case "pane_down_then_across":
      return [...innermost(across), ...innermost(down)];
    case "cell":
      return [];
    case "specific":
      return [...(spec.addressing ?? [])];
    default:
      throw new TableCalcError(`unknown computeUsing "${String(mode)}"`);
  }
}

/** Splits the table's rows into partitions and orders each along its addressing. */
export function partitionRows(table: Table, spec: AddressingSpec, layout: Layout): Partition[] {
  const names = table.columns.map((c) => c.name);
  const indexOf = (name: string, what: string): number => {
    const i = names.indexOf(name);
    if (i < 0) throw new TableCalcError(`unknown ${what} column "${name}"`);
    return i;
  };
  let addressing = addressingFields(spec, layout);
  addressing.forEach((n) => indexOf(n, "addressing"));
  const explicit = spec.partitioning ?? [];
  explicit.forEach((n) => indexOf(n, "partitioning"));
  addressing = addressing.filter((a) => !explicit.includes(a));
  const partitioning = [...explicit, ...layout.dimensions.filter((d) => !explicit.includes(d) && !addressing.includes(d))];

  if (spec.restartEvery !== undefined) {
    const at = addressing.indexOf(spec.restartEvery);
    if (at < 0) throw new TableCalcError(`restartEvery "${spec.restartEvery}" is not an addressing field`);
    partitioning.push(...addressing.slice(0, at + 1));
    addressing = addressing.slice(at + 1);
  }
  let level = addressing.length;
  if (spec.atLevel !== undefined) {
    const at = addressing.indexOf(spec.atLevel);
    if (at < 0) throw new TableCalcError(`atLevel "${spec.atLevel}" is not an addressing field`);
    level = at + 1;
  }

  const partCols = partitioning.map((n) => indexOf(n, "partitioning"));
  const addrCols = addressing.map((n) => indexOf(n, "addressing"));
  const sorts = (spec.orderBy ?? []).map((s) => ({
    col: indexOf(s.by, "orderBy"),
    sign: s.dir === "desc" ? -1 : 1,
    manual: s.order ? new Map(s.order.map((v, i) => [tupleKey([v]), i])) : null,
  }));

  const value = (row: number, col: number): Scalar => normalize(table.rows[row]?.[col]);
  const groups = new Map<string, number[]>();
  table.rows.forEach((row, i) => {
    const key = tupleKey(partCols.map((c) => normalize(row[c])));
    const list = groups.get(key);
    if (list) list.push(i);
    else groups.set(key, [i]);
  });

  const compare = (a: number, b: number): number => {
    for (const s of sorts) {
      const va = value(a, s.col);
      const vb = value(b, s.col);
      if (s.manual) {
        const ia = s.manual.get(tupleKey([va]));
        const ib = s.manual.get(tupleKey([vb]));
        if (ia !== undefined || ib !== undefined) {
          if (ia === undefined) return 1;
          if (ib === undefined) return -1;
          if (ia !== ib) return ia - ib;
          continue;
        }
      }
      const c = compareScalars(va, vb);
      // Nulls stay last in both directions.
      if (c !== 0) return va === null || vb === null ? c : c * s.sign;
    }
    for (const col of addrCols) {
      const c = compareScalars(value(a, col), value(b, col));
      if (c !== 0) return c;
    }
    return a - b;
  };

  const levelCols = addrCols.slice(0, level);
  const out: Partition[] = [];
  for (const rows of groups.values()) {
    rows.sort(compare);
    if (level === addrCols.length) {
      out.push({ positions: rows.map((r) => [r]) });
      continue;
    }
    const byLevel = new Map<string, number[]>();
    for (const r of rows) {
      const key = tupleKey(levelCols.map((c) => value(r, c)));
      const list = byLevel.get(key);
      if (list) list.push(r);
      else byLevel.set(key, [r]);
    }
    out.push({ positions: [...byLevel.values()] });
  }
  return out;
}
