import { describe, expect, it } from "vitest";

import { applyTableCalcs, TableCalcError, type ComputeUsing, type Table, type TableCalc } from "../src/index.js";
import { col, sales } from "./helpers.js";

/** Category on Rows, Year on Columns. */
const grid: Table = {
  columns: [
    { name: "Category", type: "string", role: "dimension" },
    { name: "Year", type: "integer", role: "dimension" },
    { name: "Sales", type: "float", role: "measure" },
  ],
  rows: [
    ["A", 2023, 1],
    ["A", 2024, 2],
    ["B", 2023, 3],
    ["B", 2024, 4],
  ],
};

const run = (t: Table, calc: Partial<TableCalc>, across?: string[]) =>
  col(
    applyTableCalcs(t, [{ name: "x", type: "running_sum", of: "Sales", ...calc } as TableCalc], across ? { across } : {}),
    "x",
  );

describe("table compute-using", () => {
  const cases: [ComputeUsing, number[]][] = [
    ["table_across", [1, 3, 3, 7]],
    ["table_down", [1, 2, 4, 6]],
    ["table_across_then_down", [1, 3, 6, 10]],
    ["table_down_then_across", [1, 6, 4, 10]],
    ["cell", [1, 2, 3, 4]],
  ];
  it.each(cases)("%s with Year across", (computeUsing, expected) => {
    expect(run(grid, { computeUsing }, ["Year"])).toEqual(expected);
  });

  it("defaults to every dimension, across then down", () => {
    expect(run(grid, {}, ["Year"])).toEqual([1, 3, 6, 10]);
    expect(run(grid, {})).toEqual([1, 3, 6, 10]);
  });

  it("puts every dimension down when nothing is across", () => {
    expect(run(grid, { computeUsing: "table_down" })).toEqual([1, 3, 6, 10]);
    // Table (across) with no dimension across addresses nothing, as in Tableau: each mark alone.
    expect(run(grid, { computeUsing: "table_across" })).toEqual([1, 2, 3, 4]);
  });

  it("explicit partitioning removes fields from the addressing", () => {
    expect(run(grid, { computeUsing: "table_down", partitioning: ["Category"] })).toEqual([1, 3, 3, 7]);
  });

  it("rejects unknown across, addressing and partitioning columns", () => {
    expect(() => run(grid, {}, ["Nope"])).toThrow(/unknown "across" column "Nope"/);
    expect(() => run(grid, { addressing: ["Nope"] })).toThrow(TableCalcError);
    expect(() => run(grid, { addressing: ["Year"], partitioning: ["Nope"] })).toThrow(/unknown partitioning column/);
  });
});

describe("pane compute-using", () => {
  // Category and Sub-Category on Rows, Year on Columns: a pane is one Category.
  const panes: Table = {
    columns: [
      { name: "Category", type: "string", role: "dimension" },
      { name: "Sub", type: "string", role: "dimension" },
      { name: "Year", type: "integer", role: "dimension" },
      { name: "Sales", type: "float", role: "measure" },
    ],
    rows: [
      ["A", "x", 2023, 1],
      ["A", "x", 2024, 2],
      ["A", "y", 2023, 3],
      ["B", "x", 2023, 4],
    ],
  };
  const index = (computeUsing: ComputeUsing) =>
    col(applyTableCalcs(panes, [{ name: "i", type: "index", computeUsing }], { across: ["Year"] }), "i");

  it("addresses the innermost field of each shelf within a pane", () => {
    expect(index("pane_down")).toEqual([1, 1, 2, 1]);
    expect(index("pane_across")).toEqual([1, 2, 1, 1]);
    expect(index("pane_across_then_down")).toEqual([1, 2, 3, 1]);
    expect(index("pane_down_then_across")).toEqual([1, 3, 2, 1]);
    expect(index("table_down")).toEqual([1, 1, 2, 3]);
  });
});

describe("specific dimensions", () => {
  it("orders along the addressing values, not the result order", () => {
    const shuffled = { ...sales, rows: [sales.rows[2], sales.rows[0], sales.rows[1]].filter((r) => r !== undefined) };
    expect(run(shuffled, { addressing: ["Month"] })).toEqual([60, 10, 30]);
  });

  it("orderBy sorts by a measure, keeping nulls last", () => {
    expect(run(sales, { addressing: ["Month"], orderBy: [{ by: "Sales", dir: "desc" }] })).toEqual([60, 50, 30, 20, 20, 15]);
  });

  it("orderBy with a manual member order", () => {
    const order = ["2024-03-01", "2024-01-01"];
    expect(run(sales, { addressing: ["Month"], orderBy: [{ by: "Month", order }] })).toEqual([40, 60, 30, 20, 20, 15]);
  });

  it("several addressing fields order outermost first", () => {
    // Along Month then Region: Jan East, Jan West, Feb East, Feb West, Mar East, Mar West.
    expect(run(sales, { addressing: ["Month", "Region"] })).toEqual([10, 35, 65, 15, 35, 80]);
  });

  it("an empty addressing makes every row its own partition", () => {
    expect(run(sales, { computeUsing: "specific", addressing: [] })).toEqual([10, 20, 30, 5, null, 15]);
  });
});

describe("restarting every and at the level", () => {
  const months: Table = {
    columns: [
      { name: "Year", type: "integer", role: "dimension" },
      { name: "Month", type: "integer", role: "dimension" },
      { name: "Sales", type: "float", role: "measure" },
    ],
    rows: [
      [2023, 1, 10],
      [2023, 2, 20],
      [2024, 1, 30],
      [2024, 2, 40],
    ],
  };

  it("restartEvery partitions by that field and the ones above it", () => {
    expect(run(months, { addressing: ["Year", "Month"] })).toEqual([10, 30, 60, 100]);
    expect(run(months, { addressing: ["Year", "Month"], restartEvery: "Year" })).toEqual([10, 30, 30, 70]);
  });

  it("atLevel steps only when that level changes", () => {
    const out = applyTableCalcs(months, [
      { name: "i", type: "index", addressing: ["Year", "Month"], atLevel: "Year" },
      { name: "n", type: "size", addressing: ["Year", "Month"], atLevel: "Year" },
      { name: "deepest", type: "index", addressing: ["Year", "Month"], atLevel: "Month" },
    ]);
    expect(col(out, "i")).toEqual([1, 1, 2, 2]);
    expect(col(out, "n")).toEqual([2, 2, 2, 2]);
    expect(col(out, "deepest")).toEqual([1, 2, 3, 4]);
  });

  it("rejects levels that are not addressing fields", () => {
    expect(() => run(months, { addressing: ["Month"], atLevel: "Year" })).toThrow(/atLevel "Year"/);
    expect(() => run(months, { addressing: ["Month"], restartEvery: "Year" })).toThrow(/restartEvery "Year"/);
  });
});
