import { describe, expect, it } from "vitest";

import { applyTableCalcs, formatAxis, formatValue, parseNumberFormat, TableCalcError, type Table } from "../src/index.js";

describe("formatValue", () => {
  it.each([
    [1234.5, "$#,##0", "$1,235"],
    [-1234.5, "$#,##0.00", "-$1,234.50"],
    [0.1234, "0.0%", "12.3%"],
    [1234567, "#,##0", "1,234,567"],
    [12.5, '0.0" kg"', "12.5 kg"],
  ])("formats %s with %s", (value, format, expected) => {
    expect(formatValue(value, { type: "float", format })).toBe(expected);
  });

  it("uses type defaults and labels nulls", () => {
    expect(formatValue(1234, { type: "integer" })).toBe("1,234");
    expect(formatValue(3.14159, { type: "float" })).toBe("3.14");
    expect(formatValue(null, { type: "float" })).toBe("Null");
    expect(formatValue(true, { type: "boolean" })).toBe("True");
    expect(formatValue("2024-03-01 00:00:00", { type: "datetime" })).toBe("2024-03-01");
    expect(formatValue("2024-03-01 13:45:10", { type: "datetime" })).toBe("2024-03-01 13:45:10");
  });

  it("ignores unsupported patterns", () => {
    expect(parseNumberFormat("yyyy-mm-dd")).toBeNull();
    expect(formatValue(2.5, { type: "float", format: "yyyy" })).toBe("2.50");
    expect(formatValue(2.5, { type: "float", format: '"$"#,##0.0" units"' })).toBe("$2.5 units");
    const started = performance.now();
    expect(parseNumberFormat('"'.repeat(50_000) + "#")).not.toBeUndefined();
    expect(parseNumberFormat('"'.repeat(50_000) + ".")).toBeNull();
    expect(performance.now() - started).toBeLessThan(500);
  });

  it("abbreviates axis labels", () => {
    expect(formatAxis(1_500_000, { type: "float", format: "$#,##0" })).toBe("$1.5M");
    expect(formatAxis(20_000, { type: "float" })).toBe("20K");
    expect(formatAxis(0.25, { type: "float", format: "0%" })).toBe("25%");
  });
});

describe("applyTableCalcs", () => {
  const table: Table = {
    columns: [
      { name: "Region", type: "string", role: "dimension" },
      { name: "Month", type: "date", role: "dimension" },
      { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
    ],
    rows: [
      ["East", "2024-01-01", 10],
      ["East", "2024-02-01", 20],
      ["West", "2024-01-01", 5],
      ["West", "2024-02-01", null],
      ["West", "2024-03-01", 15],
    ],
  };
  const column = (t: Table, name: string) => t.rows.map((r) => r[t.columns.findIndex((c) => c.name === name)]);

  it("runs along the addressing and restarts in each partition", () => {
    const out = applyTableCalcs(table, [
      { name: "Running", type: "running_sum", of: "Sales", computeUsing: "specific", addressing: ["Month"] },
    ]);
    expect(column(out, "Running")).toEqual([10, 30, 5, 5, 20]);
    expect(out.columns.at(-1)).toMatchObject({ name: "Running", role: "measure", format: "$#,##0" });
  });

  it("computes differences, shares and ranks", () => {
    const out = applyTableCalcs(table, [
      { name: "Diff", type: "difference", of: "Sales", computeUsing: "specific", addressing: ["Month"] },
      { name: "Share", type: "percent_of_total", of: "Sales", computeUsing: "table_down" },
      { name: "Rank", type: "rank", of: "Sales", computeUsing: "table_down" },
    ]);
    // Tableau's Difference is ZN([Sales]) - LOOKUP(ZN([Sales]), -1): a null month counts as 0.
    expect(column(out, "Diff")).toEqual([null, 10, null, -5, 15]);
    expect(column(out, "Share")).toEqual([0.2, 0.4, 0.1, null, 0.3]);
    expect(column(out, "Rank")).toEqual([3, 1, 4, null, 2]);
    expect(out.columns.find((c) => c.name === "Share")?.format).toBe("0.0%");
  });

  it("re-exports the @tabfree/tablecalc engine: custom formulas, options and filters", () => {
    const out = applyTableCalcs(
      table,
      [{ name: "Prev", type: "custom", expr: "LOOKUP(SUM([Sales]), -1)", computeUsing: "table_across" }],
      { across: ["Month"], filters: [{ expr: "[Region] = 'West'" }] },
    );
    expect(column(out, "Prev")).toEqual([null, 5, null]);
  });

  it("rejects unsupported calculations and unknown columns", () => {
    expect(() => applyTableCalcs(table, [{ name: "x", type: "custom" }])).toThrow(TableCalcError);
    expect(() => applyTableCalcs(table, [{ name: "x", type: "running_sum", of: "Nope" }])).toThrow(/unknown column/);
  });
});
