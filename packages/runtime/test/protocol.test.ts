import { describe, expect, it } from "vitest";

import {
  LIMITS,
  isFilterValue,
  parseDashboardMessage,
  parseHostMessage,
  type DashboardState,
} from "../src/index.js";

const state: DashboardState = {
  filters: { region: { members: ["East", "West"] }, order_date: { relative: { period: "month", from: -11, to: 0 } } },
  parameters: { top_n: 10 },
  highlight: null,
};

describe("parseDashboardMessage", () => {
  it.each([
    { v: 0, type: "hello" },
    { v: 0, type: "query", id: "q-1", query: "by_region" },
    { v: 0, type: "setFilter", filter: "region", value: { members: ["East"] } },
    { v: 0, type: "setFilter", filter: "region", value: null },
    { v: 0, type: "setParameter", parameter: "top_n", value: 20 },
    { v: 0, type: "select", source: "by_region", tuples: [{ Region: "East" }] },
    { v: 0, type: "resize", height: 640 },
    { v: 0, type: "error", message: "chart failed" },
  ])("accepts $type", (message) => {
    expect(parseDashboardMessage(message)).toEqual(message);
  });

  it.each([
    ["non-objects", "hello"],
    ["null", null],
    ["arrays", [0, "hello"]],
    ["another protocol version", { v: 1, type: "hello" }],
    ["unknown types", { v: 0, type: "sql", text: "SELECT 1" }],
    ["unexpected keys", { v: 0, type: "query", id: "q", query: "by_region", sql: "SELECT 1" }],
    ["invalid query names", { v: 0, type: "query", id: "q", query: "by region; drop" }],
    ["empty ids", { v: 0, type: "query", id: "", query: "by_region" }],
    ["non-finite parameter values", { v: 0, type: "setParameter", parameter: "top_n", value: Number.NaN }],
    ["object parameter values", { v: 0, type: "setParameter", parameter: "top_n", value: { n: 1 } }],
    ["negative heights", { v: 0, type: "resize", height: -1 }],
    ["huge heights", { v: 0, type: "resize", height: LIMITS.maxHeight + 1 }],
    ["nested tuple values", { v: 0, type: "select", source: "by_region", tuples: [{ Region: { x: 1 } }] }],
  ])("rejects %s", (_label, message) => {
    expect(parseDashboardMessage(message)).toBeNull();
  });

  it("rejects prototype-pollution keys", () => {
    expect(parseDashboardMessage(JSON.parse('{"v":0,"type":"hello","__proto__":{"admin":true}}'))).toBeNull();
    const tuples = JSON.parse('[{"__proto__":"x"}]') as unknown;
    expect(parseDashboardMessage({ v: 0, type: "select", source: "by_region", tuples })).toBeNull();
  });

  it("rejects class instances and null-prototype objects", () => {
    class Hello {
      v = 0;
      type = "hello";
    }
    expect(parseDashboardMessage(new Hello())).toBeNull();
    expect(parseDashboardMessage(Object.assign(Object.create(null) as object, { v: 0, type: "hello" }))).toBeNull();
  });

  it("caps oversized selections and error text", () => {
    const tuples = Array.from({ length: LIMITS.tuples + 1 }, (_, i) => ({ Row: i }));
    expect(parseDashboardMessage({ v: 0, type: "select", source: "by_region", tuples })).toBeNull();
    const parsed = parseDashboardMessage({ v: 0, type: "error", message: "x".repeat(5000) });
    expect(parsed?.type === "error" && parsed.message.length).toBe(LIMITS.messageLength);
  });
});

describe("isFilterValue", () => {
  it.each([
    { members: ["East", 1, true, null] },
    { min: 0 },
    { min: "2026-01-01", max: "2026-12-31" },
    { relative: { period: "month", from: -11, to: 0 } },
    { relative: { period: "day", anchor: "2026-11-20" } },
    { wildcard: { match: "contains", value: "corp", caseSensitive: false } },
  ])("accepts %j", (value) => {
    expect(isFilterValue(value)).toBe(true);
  });

  it.each([
    {},
    { members: ["East"], min: 1 },
    { members: [{ nested: true }] },
    { relative: { period: "fortnight" } },
    { relative: { period: "month", from: 1.5 } },
    { relative: { period: "month", anchor: "next tuesday" } },
    { wildcard: { match: "regex", value: ".*" } },
    { between: [1, 2] },
  ])("rejects %j", (value) => {
    expect(isFilterValue(value)).toBe(false);
  });
});

describe("parseHostMessage", () => {
  it("accepts connect, init, result, error and state messages", () => {
    const messages = [
      { v: 0, type: "connect" },
      { v: 0, type: "init", dashboard: { id: "sales-overview", title: "Sales Overview" }, state, theme: { mode: "dark" } },
      {
        v: 0,
        type: "result",
        id: "q-1",
        query: "by_region",
        columns: [
          { name: "Region", type: "string" },
          { name: "Sales", type: "float" },
        ],
        rows: [
          ["East", 678781.24],
          ["West", null],
        ],
        truncated: false,
      },
      { v: 0, type: "error", id: "q-1", code: "forbidden", message: "You cannot view this data." },
      { v: 0, type: "error", code: "rate_limited", message: "Slow down." },
      { v: 0, type: "state", state: { ...state, highlight: { source: "by_region", tuples: [{ Region: "East" }] } } },
    ];
    for (const message of messages) {
      expect(parseHostMessage(message)).toEqual(message);
    }
  });

  it("rejects ragged rows, unknown column types and unknown error codes", () => {
    const columns = [{ name: "Region", type: "string" }];
    expect(
      parseHostMessage({ v: 0, type: "result", id: "q", query: "by_region", columns, rows: [["East", 1]], truncated: false }),
    ).toBeNull();
    expect(
      parseHostMessage({
        v: 0,
        type: "result",
        id: "q",
        query: "by_region",
        columns: [{ name: "Region", type: "blob" }],
        rows: [],
        truncated: false,
      }),
    ).toBeNull();
    expect(parseHostMessage({ v: 0, type: "error", code: "teapot", message: "?" })).toBeNull();
  });

  it("rejects malformed state and dashboard ids", () => {
    const badState = { filters: { "bad name": { members: [] } }, parameters: {}, highlight: null };
    expect(parseHostMessage({ v: 0, type: "state", state: badState })).toBeNull();
    expect(
      parseHostMessage({ v: 0, type: "init", dashboard: { id: "Bad Id", title: "x" }, state, theme: { mode: "light" } }),
    ).toBeNull();
  });
});
