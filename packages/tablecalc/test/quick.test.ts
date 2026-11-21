import { describe, expect, it } from "vitest";

import { applyTableCalcs, quickCalcExpression, TableCalcError, type TableCalc } from "../src/index.js";
import { approx, col, ranking, sales } from "./helpers.js";

/** Runs one calc along Month (so Region partitions) and returns its column. */
const byMonth = (calc: Omit<TableCalc, "name" | "computeUsing" | "addressing">) =>
  col(applyTableCalcs(sales, [{ name: "x", computeUsing: "specific", addressing: ["Month"], ...calc }]), "x");

describe("running calcs", () => {
  it("accumulate along the addressing, carry over nulls and restart per partition", () => {
    expect(byMonth({ type: "running_sum", of: "Sales" })).toEqual([10, 30, 60, 5, 5, 20]);
    expect(byMonth({ type: "running_avg", of: "Sales" })).toEqual([10, 15, 20, 5, 5, 10]);
    expect(byMonth({ type: "running_min", of: "Sales" })).toEqual([10, 10, 10, 5, 5, 5]);
    expect(byMonth({ type: "running_max", of: "Sales" })).toEqual([10, 20, 30, 5, 5, 15]);
    expect(byMonth({ type: "running_count", of: "Sales" })).toEqual([1, 2, 3, 1, 1, 2]);
  });

  it("is null before the first value", () => {
    const t = { ...sales, rows: [["West", "2024-01-01", null], ["West", "2024-02-01", 4]] };
    const out = applyTableCalcs(t, [{ name: "x", type: "running_sum", of: "Sales", addressing: ["Month"] }]);
    expect(col(out, "x")).toEqual([null, 4]);
  });
});

describe("window calcs", () => {
  it("default to the whole partition", () => {
    expect(byMonth({ type: "window_sum", of: "Sales" })).toEqual([60, 60, 60, 20, 20, 20]);
    expect(byMonth({ type: "window_avg", of: "Sales" })).toEqual([20, 20, 20, 10, 10, 10]);
    expect(byMonth({ type: "window_min", of: "Sales" })).toEqual([10, 10, 10, 5, 5, 5]);
    expect(byMonth({ type: "window_max", of: "Sales" })).toEqual([30, 30, 30, 15, 15, 15]);
    expect(byMonth({ type: "window_count", of: "Sales" })).toEqual([3, 3, 3, 2, 2, 2]);
    expect(byMonth({ type: "window_median", of: "Sales" })).toEqual([20, 20, 20, 10, 10, 10]);
  });

  it("honour a frame, clipped to the partition", () => {
    expect(byMonth({ type: "window_sum", of: "Sales", frame: { start: -1, end: 0 } })).toEqual([10, 30, 50, 5, 5, 15]);
    expect(byMonth({ type: "window_sum", of: "Sales", frame: { start: 0, end: 1 } })).toEqual([30, 50, 30, 5, 15, 15]);
    expect(byMonth({ type: "window_sum", of: "Sales", frame: { start: "first", end: 0 } })).toEqual([10, 30, 60, 5, 5, 20]);
    expect(byMonth({ type: "window_max", of: "Sales", frame: { start: 1, end: "last" } })).toEqual([30, 30, null, 15, 15, null]);
  });

  it("moving_avg defaults to the two previous values and the current one", () => {
    expect(byMonth({ type: "moving_avg", of: "Sales" })).toEqual([10, 15, 20, 5, 5, 10]);
    expect(byMonth({ type: "moving_avg", of: "Sales", frame: { start: -1, end: 1 } })).toEqual([15, 20, 25, 5, 10, 15]);
  });

  it("nullIfIncomplete blanks rows whose frame runs off the partition", () => {
    expect(byMonth({ type: "moving_avg", of: "Sales", nullIfIncomplete: true })).toEqual([null, null, 20, null, null, 10]);
    expect(byMonth({ type: "window_sum", of: "Sales", frame: { start: -1, end: 1 }, nullIfIncomplete: true })).toEqual(
      [null, 60, null, null, 20, null],
    );
  });

  it("percentile with a fraction is WINDOW_PERCENTILE (linear interpolation)", () => {
    const out = applyTableCalcs(ranking, [
      { name: "p50", type: "percentile", of: "Value", percentile: 0.5 },
      { name: "p25", type: "percentile", of: "Value", percentile: 0.25 },
    ]);
    expect(col(out, "p50")).toEqual([9, 9, 9, 9, 9]);
    expect(col(out, "p25")).toEqual([8.25, 8.25, 8.25, 8.25, 8.25]);
  });
});

describe("difference calcs", () => {
  it("are ZN(x) - LOOKUP(ZN(x), offset) like Tableau's quick calc", () => {
    expect(byMonth({ type: "difference", of: "Sales" })).toEqual([null, 10, 10, null, -5, 15]);
    expect(byMonth({ type: "difference", of: "Sales", relativeTo: "next" })).toEqual([-10, -10, null, 5, -15, null]);
    expect(byMonth({ type: "difference", of: "Sales", relativeTo: "first" })).toEqual([0, 10, 20, 0, -5, 10]);
    expect(byMonth({ type: "difference", of: "Sales", relativeTo: "last" })).toEqual([-20, -10, 0, -10, -15, 0]);
  });

  it("percent_difference divides by the absolute reference and is null against zero", () => {
    expect(byMonth({ type: "percent_difference", of: "Sales" })).toEqual([null, 1, 0.5, null, -1, null]);
    expect(approx(byMonth({ type: "percent_difference", of: "Sales", relativeTo: "first" }))).toEqual(
      [0, 1, 2, 0, -1, 2],
    );
  });
});

describe("totals and shares", () => {
  it("percent_of_total and total use the partition", () => {
    expect(approx(byMonth({ type: "percent_of_total", of: "Sales" }))).toEqual(
      approx([10 / 60, 20 / 60, 30 / 60, 0.25, null, 0.75]),
    );
    expect(byMonth({ type: "total", of: "Sales" })).toEqual([60, 60, 60, 20, 20, 20]);
  });

  it("TOTAL re-aggregates by the declared aggregation", () => {
    const out = applyTableCalcs(sales, [{ name: "x", type: "total", of: "Sales", addressing: ["Month"] }], {
      aggregations: { Sales: "max" },
    });
    expect(col(out, "x")).toEqual([30, 30, 30, 15, 15, 15]);
  });
});

describe("ranks", () => {
  const ranks = (calc: Omit<TableCalc, "name" | "of">) => col(applyTableCalcs(ranking, [{ name: "r", of: "Value", ...calc }]), "r");

  it("follow Tableau's tie rules for (6, 9, 9, 14), descending by default, nulls unranked", () => {
    expect(ranks({ type: "rank" })).toEqual([4, 2, 2, 1, null]);
    expect(ranks({ type: "rank_dense" })).toEqual([3, 2, 2, 1, null]);
    expect(ranks({ type: "rank_modified" })).toEqual([4, 3, 3, 1, null]);
    expect(ranks({ type: "rank_unique" })).toEqual([4, 2, 3, 1, null]);
  });

  it("rank ascending", () => {
    expect(ranks({ type: "rank", direction: "asc" })).toEqual([1, 2, 2, 4, null]);
    expect(ranks({ type: "rank_dense", direction: "asc" })).toEqual([1, 2, 2, 3, null]);
    expect(ranks({ type: "rank_modified", direction: "asc" })).toEqual([1, 3, 3, 4, null]);
    expect(ranks({ type: "rank_unique", direction: "asc" })).toEqual([1, 2, 3, 4, null]);
  });

  it("rank_percentile and percentile are ascending (modified rank - 1) / (n - 1)", () => {
    expect(approx(ranks({ type: "rank_percentile" }))).toEqual(approx([0, 2 / 3, 2 / 3, 1, null]));
    expect(approx(ranks({ type: "percentile" }))).toEqual(approx([0, 2 / 3, 2 / 3, 1, null]));
    expect(approx(ranks({ type: "rank_percentile", direction: "desc" }))).toEqual(approx([1, 2 / 3, 2 / 3, 0, null]));
  });

  it("rank_unique breaks ties by the addressing order (orderBy can change it)", () => {
    expect(ranks({ type: "rank_unique", orderBy: [{ by: "Item", dir: "desc" }] })).toEqual([4, 3, 2, 1, null]);
  });

  it("rank within partitions", () => {
    const out = applyTableCalcs(sales, [{ name: "r", type: "rank", of: "Sales", addressing: ["Region"] }]);
    // Jan: East 10 > West 5; Feb: West is null; Mar: East 30 > West 15.
    expect(col(out, "r")).toEqual([1, 1, 1, 2, null, 2]);
  });
});

describe("position calcs", () => {
  it("lookup, index, first, last and size", () => {
    expect(byMonth({ type: "lookup", of: "Sales", offset: -1 })).toEqual([null, 10, 20, null, 5, null]);
    expect(byMonth({ type: "lookup", of: "Sales", offset: 2 })).toEqual([30, null, null, 15, null, null]);
    expect(byMonth({ type: "lookup", of: "Sales", relativeTo: "first" })).toEqual([10, 10, 10, 5, 5, 5]);
    expect(byMonth({ type: "lookup", of: "Sales", relativeTo: "last", offset: -1 })).toEqual([20, 20, 20, null, null, null]);
    expect(byMonth({ type: "lookup", of: "Sales", relativeTo: "previous" })).toEqual([null, 10, 20, null, 5, null]);
    expect(byMonth({ type: "index" })).toEqual([1, 2, 3, 1, 2, 3]);
    expect(byMonth({ type: "first" })).toEqual([0, -1, -2, 0, -1, -2]);
    expect(byMonth({ type: "last" })).toEqual([2, 1, 0, 2, 1, 0]);
    expect(byMonth({ type: "size" })).toEqual([3, 3, 3, 3, 3, 3]);
  });
});

describe("result columns", () => {
  it("type and format the new columns", () => {
    const out = applyTableCalcs(sales, [
      { name: "run", type: "running_sum", of: "Sales" },
      { name: "share", type: "percent_of_total", of: "Sales" },
      { name: "rank", type: "rank", of: "Sales" },
      { name: "prev", type: "lookup", of: "Sales", offset: -1 },
    ]);
    expect(out.columns.slice(3)).toEqual([
      { name: "run", type: "float", role: "measure", format: "$#,##0" },
      { name: "share", type: "float", role: "measure", format: "0.0%" },
      { name: "rank", type: "integer", role: "measure" },
      { name: "prev", type: "float", role: "measure", format: "$#,##0" },
    ]);
  });

  it("replaces an existing column of the same name (e.g. a SQL push-down value)", () => {
    const first = applyTableCalcs(sales, [{ name: "x", type: "index" }]);
    const again = applyTableCalcs(first, [{ name: "x", type: "size" }]);
    expect(again.columns.map((c) => c.name)).toEqual(["Region", "Month", "Sales", "x"]);
    expect(col(again, "x")).toEqual([6, 6, 6, 6, 6, 6]);
  });

  it("does not modify its input", () => {
    const before = JSON.stringify(sales);
    applyTableCalcs(sales, [{ name: "x", type: "running_sum", of: "Sales" }], { densify: true });
    expect(JSON.stringify(sales)).toBe(before);
  });
});

describe("quickCalcExpression", () => {
  it("writes the formula Tableau uses", () => {
    expect(quickCalcExpression({ type: "running_sum", of: "Sales" })).toBe("RUNNING_SUM([Sales])");
    expect(quickCalcExpression({ type: "difference", of: "Sales" })).toBe("ZN([Sales]) - LOOKUP(ZN([Sales]), -1)");
    expect(quickCalcExpression({ type: "percent_of_total", of: "a]b" })).toBe("[a]]b] / TOTAL([a]]b])");
    expect(quickCalcExpression({ type: "moving_avg", of: "S", nullIfIncomplete: true })).toBe(
      "IIF(FIRST() <= -2 AND LAST() >= 0, WINDOW_AVG([S], -2, 0), NULL)",
    );
    expect(quickCalcExpression({ type: "rank_dense", of: "S", direction: "asc" })).toBe("RANK_DENSE([S], 'asc')");
  });

  it("rejects calcs without their measure, and custom without an expression", () => {
    expect(() => quickCalcExpression({ type: "running_sum" })).toThrow(/needs "of"/);
    expect(() => applyTableCalcs(sales, [{ name: "x", type: "custom" }])).toThrow(TableCalcError);
    expect(() => applyTableCalcs(sales, [{ name: "x", type: "running_sum", of: "Nope" }])).toThrow(/unknown column "Nope"/);
    expect(() =>
      applyTableCalcs(sales, [{ name: "x", type: "bogus" as unknown as TableCalc["type"], of: "Sales" }]),
    ).toThrow(/not supported/);
  });
});
