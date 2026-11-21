/**
 * Densification (Tableau's domain completion). For the chosen dimensions, every combination of the
 * values seen anywhere in the result is filled in within each combination of the other dimensions
 * that occurs in the result. Inserted rows carry null measures, so RUNNING_SUM carries its total
 * through them and LOOKUP(-1) finds an empty previous month instead of skipping it.
 *
 * Inserted rows are placed next to their partition's rows: before the first original row they sort
 * ahead of (by the densified dimensions' values), or after the partition's last original row.
 */

import { TableCalcError, type Scalar, type Table } from "./types.js";
import { compareScalars, normalize, tupleKey } from "./values.js";

/** Upper bound on the rows densification may produce, so a wide cross product fails loudly. */
export const MAX_DENSIFIED_ROWS = 1_000_000;

export interface Densified {
  table: Table;
  /** True for rows the densification inserted. */
  inserted: boolean[];
}

export function densify(table: Table, which: boolean | readonly string[] | undefined): Densified {
  const none = { table, inserted: table.rows.map(() => false) };
  if (!which || (Array.isArray(which) && which.length === 0)) return none;
  const names = table.columns.map((c) => c.name);
  const dimensions = table.columns.filter((c) => c.role !== "measure").map((c) => c.name);
  const target = which === true ? dimensions : [...(which as readonly string[])];
  for (const name of target) {
    if (!names.includes(name)) throw new TableCalcError(`unknown densify column "${name}"`);
    if (!dimensions.includes(name)) throw new TableCalcError(`densify column "${name}" is not a dimension`);
  }
  const targetCols = target.map((n) => names.indexOf(n));
  const otherCols = dimensions.filter((d) => !target.includes(d)).map((n) => names.indexOf(n));

  const domains = targetCols.map((c) => {
    const seen = new Map<string, Scalar>();
    for (const row of table.rows) {
      const v = normalize(row[c]);
      seen.set(tupleKey([v]), v);
    }
    return [...seen.values()].sort(compareScalars);
  });

  interface Part {
    sample: readonly Scalar[];
    present: Set<string>;
    last: number;
  }
  const parts = new Map<string, Part>();
  const partOf = (row: readonly Scalar[]): string => tupleKey(otherCols.map((c) => normalize(row[c])));
  table.rows.forEach((row, i) => {
    const key = partOf(row);
    const part = parts.get(key) ?? { sample: row, present: new Set<string>(), last: i };
    part.present.add(tupleKey(targetCols.map((c) => normalize(row[c]))));
    part.last = i;
    parts.set(key, part);
  });

  const combos = domains.reduce((n, d) => n * d.length, 1);
  if (combos * parts.size > MAX_DENSIFIED_ROWS) {
    throw new TableCalcError(`densification would produce more than ${MAX_DENSIFIED_ROWS} rows`);
  }

  const measureCols = new Set(table.columns.flatMap((c, i) => (c.role === "measure" ? [i] : [])));
  const pending = new Map<string, Scalar[][]>();
  for (const [key, part] of parts) {
    const rows: Scalar[][] = [];
    const tuple: Scalar[] = [];
    const walk = (depth: number): void => {
      if (depth === domains.length) {
        if (part.present.has(tupleKey(tuple))) return;
        const row = table.columns.map((_, c) => (measureCols.has(c) ? null : normalize(part.sample[c])));
        targetCols.forEach((c, j) => {
          row[c] = tuple[j] ?? null;
        });
        rows.push(row);
        return;
      }
      for (const v of domains[depth] ?? []) {
        tuple[depth] = v;
        walk(depth + 1);
      }
    };
    walk(0);
    pending.set(key, rows);
  }

  const before = (a: readonly Scalar[], b: readonly Scalar[]): boolean => {
    for (const c of targetCols) {
      const cmp = compareScalars(normalize(a[c]), normalize(b[c]));
      if (cmp !== 0) return cmp < 0;
    }
    return false;
  };
  const rows: Scalar[][] = [];
  const inserted: boolean[] = [];
  const emit = (row: Scalar[], isNew: boolean): void => {
    rows.push(row);
    inserted.push(isNew);
  };
  table.rows.forEach((row, i) => {
    const key = partOf(row);
    const queue = pending.get(key) ?? [];
    while (queue.length && before(queue[0] ?? [], row)) emit(queue.shift() ?? [], true);
    emit([...row], false);
    if (parts.get(key)?.last === i) while (queue.length) emit(queue.shift() ?? [], true);
  });
  return { table: { columns: table.columns, rows }, inserted };
}
