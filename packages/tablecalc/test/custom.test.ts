import { describe, expect, it } from "vitest";

import { applyTableCalcs, evaluateExpression, parseExpression, TableCalcError, type Table } from "../src/index.js";
import { approx, col, ranking, sales } from "./helpers.js";

/** Evaluates a formula along Month (Region partitions). */
const along = (expr: string, table: Table = sales) =>
  col(applyTableCalcs(table, [{ name: "x", type: "custom", expr, addressing: ["Month"] }]), "x");

describe("custom table-calc formulas", () => {
  it("WINDOW_AVG with a frame", () => {
    expect(along("WINDOW_AVG(SUM([Sales]), -2, 0)")).toEqual([10, 15, 20, 5, 5, 10]);
  });

  it("RUNNING_SUM over TOTAL", () => {
    expect(approx(along("RUNNING_SUM(SUM([Sales])) / TOTAL(SUM([Sales]))"))).toEqual(approx([1 / 6, 0.5, 1, 0.25, 0.25, 1]));
  });

  it("LOOKUP, with literal and FIRST()/LAST() offsets", () => {
    expect(along("LOOKUP(SUM([Sales]), -1)")).toEqual([null, 10, 20, null, 5, null]);
    expect(along("LOOKUP(SUM([Sales]), FIRST())")).toEqual([10, 10, 10, 5, 5, 5]);
    expect(along("LOOKUP(SUM([Sales]), LAST())")).toEqual([30, 30, 30, 15, 15, 15]);
    expect(along("LOOKUP(SUM([Sales]))")).toEqual([10, 20, 30, 5, null, 15]);
  });

  it("PREVIOUS_VALUE is recursive and starts from its argument", () => {
    expect(along("PREVIOUS_VALUE(0) + SUM([Sales])")).toEqual([10, 30, 60, 5, null, null]);
    expect(along("PREVIOUS_VALUE(0) + ZN(SUM([Sales]))")).toEqual([10, 30, 60, 5, 5, 20]);
    expect(along("PREVIOUS_VALUE(1) * 2")).toEqual([2, 4, 8, 2, 4, 8]);
  });

  it("PREVIOUS_VALUE that depends on later rows is an error", () => {
    expect(() => along("WINDOW_SUM(PREVIOUS_VALUE(0))")).toThrow(/PREVIOUS_VALUE/);
  });

  it("RANK in a formula, both directions", () => {
    expect(along("RANK(SUM([Sales]))")).toEqual([3, 2, 1, 2, null, 1]);
    expect(along("RANK(SUM([Sales]), 'asc')")).toEqual([1, 2, 3, 1, null, 2]);
    expect(() => along("RANK(SUM([Sales]), 'up')")).toThrow(/direction/);
  });

  it("IF with FIRST()", () => {
    expect(along("IF FIRST()=0 THEN SUM([Sales]) END")).toEqual([10, null, null, 5, null, null]);
    expect(along("IF INDEX() = 1 THEN 'first' ELSEIF LAST() = 0 THEN 'last' ELSE 'middle' END")).toEqual(
      ["first", "middle", "last", "first", "middle", "last"],
    );
  });

  it("IIF, ZN, ISNULL, IFNULL, ABS and ROUND", () => {
    expect(along("IIF(SUM([Sales]) > 15, 'big', 'small')")).toEqual(["small", "big", "big", "small", null, "small"]);
    expect(along("IIF(SUM([Sales]) > 15, 'big', 'small', 'unknown')")[4]).toBe("unknown");
    expect(along("ZN(SUM([Sales]))")).toEqual([10, 20, 30, 5, 0, 15]);
    expect(along("ISNULL(SUM([Sales]))")).toEqual([false, false, false, false, true, false]);
    expect(along("IFNULL(SUM([Sales]), -1)")).toEqual([10, 20, 30, 5, -1, 15]);
    expect(along("ABS(-SUM([Sales]))")).toEqual([10, 20, 30, 5, null, 15]);
    expect(along("ROUND(SUM([Sales]) / 3, 2)")).toEqual([3.33, 6.67, 10, 1.67, null, 5]);
    expect(evaluateExpression(sales, "ROUND(-2.5)")[0]).toBe(-3);
    expect(evaluateExpression(sales, "ROUND(2.345, 2)")[0]).toBe(2.35);
  });

  it("arithmetic, precedence and null propagation", () => {
    const v = (expr: string) => evaluateExpression(sales, expr)[0];
    expect(v("1 + 2 * 3")).toBe(7);
    expect(v("(1 + 2) * 3")).toBe(9);
    expect(v("2 ^ 3 ^ 2")).toBe(512);
    expect(v("-2 ^ 2")).toBe(4);
    expect(v("7 % 4")).toBe(3);
    expect(v("1 / 0")).toBeNull();
    expect(v("1 + NULL")).toBeNull();
    expect(v("'a' + 'b'")).toBe("ab");
    expect(v("NOT (1 > 2) AND 3 >= 3")).toBe(true);
    expect(v("NULL = 1")).toBeNull();
    expect(v("FALSE AND NULL")).toBe(false);
    expect(v("TRUE OR NULL")).toBe(true);
    expect(v("TRUE AND NULL")).toBeNull();
    expect(v("1 <> 2 AND 1 != 2 AND 1 == 1")).toBe(true);
    expect(v("DIV(7, 2) + SQUARE(2) + POWER(2, 3) + SIGN(-4)")).toBe(14);
    expect(v("MIN(3, 4) + MAX(3, 4)")).toBe(7);
    expect(v("CASE 2 WHEN 1 THEN 'one' WHEN 2 THEN 'two' ELSE 'many' END")).toBe("two");
    expect(v("STR(1) + 'x'")).toBe("1x");
    expect(v("1 // a comment\n + /* inline */ 1")).toBe(2);
  });

  it("comparisons drive IF; a null condition takes the ELSE branch", () => {
    expect(along("IF SUM([Sales]) >= 20 THEN 1 ELSE 0 END")).toEqual([0, 1, 1, 0, 0, 0]);
  });

  it("accepts the exact column name, including brackets and parentheses", () => {
    const t: Table = {
      columns: [
        { name: "Month", type: "date", role: "dimension" },
        { name: "Sales (Current)", type: "float", role: "measure" },
        { name: "a]b", type: "float", role: "measure" },
      ],
      rows: [
        ["2024-01-01", 1, 10],
        ["2024-02-01", 2, 20],
      ],
    };
    expect(along("RUNNING_SUM([Sales (Current)])", t)).toEqual([1, 3]);
    expect(along("SUM([a]]b]) - [Sales (Current)]", t)).toEqual([9, 18]);
  });

  it("references earlier calcs by name", () => {
    const out = applyTableCalcs(sales, [
      { name: "Running", type: "running_sum", of: "Sales", addressing: ["Month"] },
      { name: "Share", type: "custom", expr: "[Running] / WINDOW_MAX([Running])", addressing: ["Month"] },
    ]);
    expect(approx(col(out, "Share"))).toEqual(approx([1 / 6, 0.5, 1, 0.25, 0.25, 1]));
  });

  it("nests table calcs", () => {
    expect(along("WINDOW_MAX(RUNNING_SUM(SUM([Sales])))")).toEqual([60, 60, 60, 20, 20, 20]);
    expect(along("RUNNING_SUM(LOOKUP(SUM([Sales]), -1))")).toEqual([null, 10, 30, null, 5, 5]);
    expect(along("WINDOW_SUM(SUM([Sales]), FIRST(), 0)")).toEqual([10, 30, 60, 5, 5, 20]);
    // INDEX() + FIRST() is always 1; INDEX() + LAST() is always SIZE().
    expect(along("INDEX() + FIRST() + SIZE() * 10")).toEqual([31, 31, 31, 31, 31, 31]);
    expect(along("INDEX() + LAST() = SIZE()")).toEqual([true, true, true, true, true, true]);
  });

  it("TOTAL re-aggregates aggregates inside an expression", () => {
    const t: Table = {
      columns: [
        { name: "Region", type: "string", role: "dimension" },
        { name: "Profit", type: "float", role: "measure" },
        { name: "Sales", type: "float", role: "measure" },
      ],
      rows: [
        ["East", 10, 100],
        ["West", 30, 100],
      ],
    };
    // Ratio of totals (40 / 200), not the total of ratios (0.1 + 0.3).
    expect(approx(evaluateExpression(t, "TOTAL(SUM([Profit]) / SUM([Sales]))"))).toEqual([0.2, 0.2]);
    expect(evaluateExpression(t, "TOTAL(MIN([Profit])) + TOTAL(MAX([Profit]))")).toEqual([40, 40]);
    expect(evaluateExpression(t, "TOTAL(AVG([Profit]))")).toEqual([20, 20]);
    expect(evaluateExpression(t, "TOTAL(ATTR([Region]))")).toEqual([null, null]);
    expect(() => evaluateExpression(t, "TOTAL(COUNTD([Profit]))")).toThrow(/cannot be re-aggregated/);
    expect(() => evaluateExpression(t, "TOTAL(INDEX())")).toThrow(/cannot contain/);
  });

  it("window statistics", () => {
    const v = (expr: string) => evaluateExpression(ranking, expr)[0];
    expect(v("WINDOW_VAR(SUM([Value]))")).toBe(11);
    expect(v("WINDOW_VARP(SUM([Value]))")).toBe(8.25);
    expect(v("WINDOW_STDEV(SUM([Value]))")).toBeCloseTo(Math.sqrt(11), 12);
    expect(v("WINDOW_STDEVP(SUM([Value]))")).toBeCloseTo(Math.sqrt(8.25), 12);
    expect(v("WINDOW_COVAR(SUM([Value]), SUM([Other]))")).toBe(4);
    expect(v("WINDOW_COVARP(SUM([Value]), SUM([Other]))")).toBe(3);
    expect(v("WINDOW_CORR(SUM([Value]), SUM([Other]))")).toBeCloseTo(3 / Math.sqrt(8.25 * 1.25), 12);
    expect(v("WINDOW_PERCENTILE(SUM([Value]), 0.75)")).toBe(10.25);
    expect(v("WINDOW_MEDIAN(SUM([Value]))")).toBe(9);
    expect(v("WINDOW_COUNT(SUM([Value]))")).toBe(4);
    expect(v("RANK_MODIFIED(SUM([Value]))")).toBe(4);
    expect(v("RANK_DENSE(SUM([Value]))")).toBe(3);
    expect(v("RANK_UNIQUE(SUM([Value]))")).toBe(4);
    expect(v("RANK_PERCENTILE(SUM([Value]))")).toBe(0);
  });

  it("window functions need both bounds or neither", () => {
    expect(() => along("WINDOW_SUM(SUM([Sales]), -1)")).toThrow(/both a start and an end/);
  });

  it("types the result column", () => {
    const out = applyTableCalcs(sales, [
      { name: "i", type: "custom", expr: "INDEX()" },
      { name: "b", type: "custom", expr: "SUM([Sales]) > 10" },
      { name: "s", type: "custom", expr: "IIF(INDEX() = 1, 'a', 'b')" },
      { name: "r", type: "custom", expr: "RUNNING_SUM(SUM([Sales]))" },
      { name: "f", type: "custom", expr: "SUM([Sales]) * 2" },
    ]);
    expect(out.columns.slice(3)).toEqual([
      { name: "i", type: "integer", role: "measure" },
      { name: "b", type: "boolean", role: "measure" },
      { name: "s", type: "string", role: "measure" },
      { name: "r", type: "float", role: "measure", format: "$#,##0" },
      { name: "f", type: "float", role: "measure" },
    ]);
  });
});

describe("formula safety and errors", () => {
  it("rejects unknown functions instead of evaluating them", () => {
    expect(() => along("EVAL('1')")).toThrow(TableCalcError);
    expect(() => along("SCRIPT_REAL('return 1', SUM([Sales]))")).toThrow(/unknown function "SCRIPT_REAL"/);
    expect(() => along("CONSTRUCTOR(1)")).toThrow(/unknown function/);
    expect(() => along("TOSTRING(1)")).toThrow(/unknown function/);
  });

  it("rejects unknown columns, aggregates of expressions and bad arity", () => {
    expect(() => along("SUM([Profit])")).toThrow(/unknown column "Profit"/);
    expect(() => along("[__proto__]")).toThrow(/unknown column/);
    expect(() => along("SUM([Sales] * 2)")).toThrow(/computed by the query/);
    expect(() => along("ZN()")).toThrow(/ZN\(\) takes 1 argument/);
    expect(() => along("LOOKUP()")).toThrow(/LOOKUP\(\) takes 1 to 2 arguments/);
  });

  it("reports syntax errors with a position", () => {
    expect(() => parseExpression("SUM([Sales]")).toThrow(/expected "\)"/);
    expect(() => parseExpression("1 +")).toThrow(/unexpected end of formula/);
    expect(() => parseExpression("'open")).toThrow(/unterminated string/);
    expect(() => parseExpression("[open")).toThrow(/unterminated field/);
    expect(() => parseExpression("1 2")).toThrow(/position 3/);
    expect(() => parseExpression("IF 1 THEN 2")).toThrow(/expected END/);
    expect(() => parseExpression("#2024-01-01#")).toThrow(/date literals/);
    expect(() => parseExpression("[Parameters].[Top N]")).toThrow(/qualified field/);
    expect(() => parseExpression("a; b")).toThrow(TableCalcError);
    expect(() => parseExpression("(".repeat(500) + "1" + ")".repeat(500))).toThrow(/nested too deeply/);
  });

  it("parses into a plain tree", () => {
    expect(parseExpression("SUM([Sales]) * -2")).toEqual({
      kind: "binary",
      op: "*",
      left: { kind: "call", name: "SUM", args: [{ kind: "field", name: "Sales" }] },
      right: { kind: "unary", op: "-", arg: { kind: "literal", value: 2 } },
    });
    expect(parseExpression("'it''s'")).toEqual({ kind: "literal", value: "it's" });
    expect(parseExpression("sum([x])")).toEqual({ kind: "call", name: "SUM", args: [{ kind: "field", name: "x" }] });
  });
});
