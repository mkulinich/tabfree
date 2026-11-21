import { describe, expect, it } from "vitest";

import { applyTableCalcs, densify, TableCalcError, type Table, type TableCalc } from "../src/index.js";
import { approx, col, sales } from "./helpers.js";

/** East has no February, West has no March. */
const sparse: Table = {
  columns: [
    { name: "Region", type: "string", role: "dimension" },
    { name: "Month", type: "date", role: "dimension" },
    { name: "Sales", type: "float", role: "measure" },
  ],
  rows: [
    ["East", "2024-01-01", 10],
    ["East", "2024-03-01", 30],
    ["West", "2024-01-01", 5],
    ["West", "2024-02-01", 7],
  ],
};

const calcs: TableCalc[] = [
  { name: "Running", type: "running_sum", of: "Sales", addressing: ["Month"] },
  { name: "Prev", type: "lookup", of: "Sales", offset: -1, addressing: ["Month"] },
];

describe("densification", () => {
  it("fills missing addressing members per partition, with null measures, in place", () => {
    const out = applyTableCalcs(sparse, calcs, { densify: ["Month"] });
    expect(out.rows.map((r) => r.slice(0, 3))).toEqual([
      ["East", "2024-01-01", 10],
      ["East", "2024-02-01", null],
      ["East", "2024-03-01", 30],
      ["West", "2024-01-01", 5],
      ["West", "2024-02-01", 7],
      ["West", "2024-03-01", null],
    ]);
    expect(col(out, "Running")).toEqual([10, 10, 40, 5, 12, 12]);
    // LOOKUP(-1) for East March is the empty February, not January.
    expect(col(out, "Prev")).toEqual([null, 10, null, null, 5, 7]);
  });

  it("without densification LOOKUP skips the missing month", () => {
    expect(col(applyTableCalcs(sparse, calcs), "Prev")).toEqual([null, 10, null, 5]);
  });

  it("dropDensified keeps the calc values but hides the inserted rows", () => {
    const out = applyTableCalcs(sparse, calcs, { densify: ["Month"], dropDensified: true });
    expect(out.rows.length).toBe(4);
    expect(col(out, "Running")).toEqual([10, 40, 5, 12]);
    expect(col(out, "Prev")).toEqual([null, null, null, 5]);
  });

  it("densify: true completes the cross product of every dimension", () => {
    const t: Table = { ...sparse, rows: [["East", "2024-01-01", 1], ["West", "2024-02-01", 2]] };
    const { table, inserted } = densify(t, true);
    expect(table.rows).toEqual([
      ["East", "2024-01-01", 1],
      ["East", "2024-02-01", null],
      ["West", "2024-01-01", null],
      ["West", "2024-02-01", 2],
    ]);
    expect(inserted).toEqual([false, true, true, false]);
  });

  it("puts inserted rows before an original row they sort ahead of", () => {
    const t: Table = { ...sparse, rows: [["East", "2024-03-01", 3], ["West", "2024-01-01", 1]] };
    expect(densify(t, ["Month"]).table.rows).toEqual([
      ["East", "2024-01-01", null],
      ["East", "2024-03-01", 3],
      ["West", "2024-01-01", 1],
      ["West", "2024-03-01", null],
    ]);
  });

  it("is a no-op when nothing is missing", () => {
    const out = densify(sales, true);
    expect(out.table.rows).toEqual(sales.rows);
    expect(out.inserted.every((x) => !x)).toBe(true);
  });

  it("rejects unknown columns and measures", () => {
    expect(() => densify(sparse, ["Nope"])).toThrow(/unknown densify column/);
    expect(() => densify(sparse, ["Sales"])).toThrow(/not a dimension/);
  });

  it("refuses an oversized cross product", () => {
    const rows = Array.from({ length: 1001 }, (_, i) => [`r${i}`, `m${i}`, 1]);
    expect(() => densify({ ...sparse, rows }, true)).toThrow(TableCalcError);
  });
});

describe("secondary calculations", () => {
  it("apply to the primary result and inherit its addressing", () => {
    const out = applyTableCalcs(sales, [
      {
        name: "Share of running",
        type: "running_sum",
        of: "Sales",
        addressing: ["Month"],
        then: { type: "percent_of_total" },
      },
    ]);
    // Running: East 10, 30, 60 (total 100); West 5, 5, 20 (total 30).
    expect(approx(col(out, "Share of running"))).toEqual(approx([0.1, 0.3, 0.6, 5 / 30, 5 / 30, 20 / 30]));
    expect(out.columns.at(-1)).toEqual({ name: "Share of running", type: "float", role: "measure", format: "0.0%" });
    expect(out.columns.length).toBe(4);
  });

  it("can address differently from the primary", () => {
    const out = applyTableCalcs(sales, [
      {
        name: "Rank of running",
        type: "running_sum",
        of: "Sales",
        addressing: ["Month"],
        then: { type: "rank", computeUsing: "specific", addressing: ["Region"] },
      },
    ]);
    // Per month, East's running total beats West's.
    expect(col(out, "Rank of running")).toEqual([1, 1, 1, 2, 2, 2]);
    expect(out.columns.at(-1)?.type).toBe("integer");
  });

  it("difference of a running total, and a secondary on a custom primary", () => {
    const out = applyTableCalcs(sales, [
      { name: "d", type: "running_sum", of: "Sales", addressing: ["Month"], then: { type: "difference" } },
      { name: "c", type: "custom", expr: "SUM([Sales]) * 2", addressing: ["Month"], then: { type: "running_sum" } },
    ]);
    expect(col(out, "d")).toEqual([null, 20, 30, null, 0, 15]);
    expect(col(out, "c")).toEqual([20, 60, 120, 10, 10, 40]);
  });

  it("rejects a custom secondary", () => {
    const bad = { name: "x", type: "index", then: { type: "custom" } } as unknown as TableCalc;
    expect(() => applyTableCalcs(sales, [bad])).toThrow(/secondary calculation cannot be custom/);
  });
});

describe("table-calculation filters", () => {
  it("hide rows after the calcs, leaving the calc values as computed over every row", () => {
    const out = applyTableCalcs(sales, [calcs[0] as TableCalc], { filters: [{ expr: "[Month] >= '2024-02-01'" }] });
    expect(out.rows.map((r) => r[1])).toEqual(["2024-02-01", "2024-03-01", "2024-02-01", "2024-03-01"]);
    // East March still includes January's 10.
    expect(col(out, "Running")).toEqual([30, 60, 5, 20]);
  });

  it("the LOOKUP(..., 0) trick: a table-calc filter on a dimension", () => {
    const out = applyTableCalcs(
      sales,
      [{ name: "Prev", type: "lookup", of: "Sales", offset: -1, addressing: ["Month"] }],
      { filters: [{ expr: "LOOKUP(MIN([Month]), 0) = '2024-03-01'", addressing: ["Month"] }] },
    );
    expect(col(out, "Prev")).toEqual([20, null]);
  });

  it("filter on a calc column, and a top-N filter by INDEX", () => {
    const run = [{ name: "Running", type: "running_sum", of: "Sales", addressing: ["Month"] }] satisfies TableCalc[];
    expect(col(applyTableCalcs(sales, run, { filters: [{ expr: "[Running] > 10" }] }), "Running")).toEqual([30, 60, 20]);
    const top = applyTableCalcs(sales, run, {
      filters: [{ expr: "INDEX() <= 1", addressing: ["Month"], orderBy: [{ by: "Sales", dir: "desc" }] }],
    });
    expect(top.rows.map((r) => r.slice(0, 2))).toEqual([
      ["East", "2024-03-01"],
      ["West", "2024-03-01"],
    ]);
  });

  it("several filters combine with AND; a null result hides the row", () => {
    const out = applyTableCalcs(sales, [], { filters: [{ expr: "SUM([Sales]) > 5" }, { expr: "[Region] = 'East'" }] });
    expect(col(out, "Sales")).toEqual([10, 20, 30]);
    expect(applyTableCalcs(sales, [], { filters: [{ expr: "SUM([Sales]) >= 0" }] }).rows.length).toBe(5);
  });

  it("run after densification and before dropping densified rows", () => {
    const out = applyTableCalcs(sparse, calcs, {
      densify: ["Month"],
      dropDensified: true,
      filters: [{ expr: "[Region] = 'East'" }],
    });
    expect(col(out, "Running")).toEqual([10, 40]);
  });
});
