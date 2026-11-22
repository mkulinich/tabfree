import type { Scalar, Table } from "../src/index.js";

export const col = (t: Table, name: string): Scalar[] => {
  const i = t.columns.findIndex((c) => c.name === name);
  if (i < 0) throw new Error(`no column ${name}`);
  return t.rows.map((r) => r[i] ?? null);
};

/** Rounds numbers to 6 places so float sums compare exactly. */
export const approx = (values: readonly Scalar[]): Scalar[] =>
  values.map((v) => (typeof v === "number" ? Math.round(v * 1e6) / 1e6 : v));

/** Region × Month × Sales: East has every month, West has a null February. */
export const sales: Table = {
  columns: [
    { name: "Region", type: "string", role: "dimension" },
    { name: "Month", type: "date", role: "dimension" },
    { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
  ],
  rows: [
    ["East", "2024-01-01", 10],
    ["East", "2024-02-01", 20],
    ["East", "2024-03-01", 30],
    ["West", "2024-01-01", 5],
    ["West", "2024-02-01", null],
    ["West", "2024-03-01", 15],
  ],
};

/** Tableau's documented ranking example (6, 9, 9, 14), plus a null and a second measure. */
export const ranking: Table = {
  columns: [
    { name: "Item", type: "string", role: "dimension" },
    { name: "Value", type: "integer", role: "measure" },
    { name: "Other", type: "integer", role: "measure" },
  ],
  rows: [
    ["a", 6, 1],
    ["b", 9, 2],
    ["c", 9, 3],
    ["d", 14, 4],
    ["e", null, 5],
  ],
};
