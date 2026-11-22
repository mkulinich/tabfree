/** P7.4: keyboard access, text alternatives and localised numbers and dates. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { dataTable } from "../src/components.js";
import { formatValue, setLocale } from "../src/format.js";
import { renderChart, type ChartData } from "../src/index.js";

const data: ChartData = {
  columns: [
    { name: "Region", type: "string", role: "dimension" },
    { name: "Sales", type: "float", role: "measure", format: "#,##0.00" },
  ],
  rows: [
    ["East", 1234.5],
    ["West", -0.001],
  ],
};

afterEach(() => setLocale("en-US"));

describe("keyboard and screen readers", () => {
  it("makes selectable marks focusable buttons that Enter and Space select", () => {
    const onSelect = vi.fn();
    const svg = renderChart({ mark: "bar", x: "Region", y: "Sales" }, data, { width: 300, height: 200, onSelect });
    const bar = svg.querySelector("rect.tf-mark");
    expect(bar?.getAttribute("tabindex")).toBe("0");
    expect(bar?.getAttribute("role")).toBe("button");
    expect(bar?.getAttribute("aria-label")).toBe("Region: East, Sales: 1,234.50");
    bar?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    bar?.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    expect(onSelect).toHaveBeenCalledTimes(2);
    const still = renderChart({ mark: "pie", x: "Region", y: "Sales" }, data, { width: 300, height: 200 });
    expect(still.querySelector("path.tf-mark")?.getAttribute("role")).toBe("img");
    expect(still.querySelector("path.tf-mark")?.hasAttribute("tabindex")).toBe(false);
  });

  it("offers the data as a table", () => {
    const table = dataTable({ ...data, truncated: false, query: "q" }, "Sales by region");
    expect(table.className).toBe("tf-sr-only");
    expect(table.querySelector("caption")?.textContent).toBe("Sales by region");
    expect([...table.querySelectorAll("th")].map((th) => th.getAttribute("scope"))).toEqual(["col", "col"]);
    expect(table.querySelectorAll("tr")).toHaveLength(3);
  });
});

describe("localisation", () => {
  it("formats numbers for the locale and never shows minus zero", () => {
    const money = { type: "float", format: "#,##0.00" } as const;
    expect(formatValue(1234.5, money)).toBe("1,234.50");
    expect(formatValue(-0.001, money)).toBe("0.00");
    setLocale("de-DE");
    expect(formatValue(1234.5, money)).toBe("1.234,50");
    expect(formatValue(0.25, { type: "float", format: "0.0%" })).toBe("25,0%");
    setLocale("not a locale!!");
    expect(formatValue(1234.5, money)).toMatch(/1.234.50/);
  });

  it("keeps ISO dates unless the dashboard asks for local ones", () => {
    expect(formatValue("2024-03-01", { type: "date" })).toBe("2024-03-01");
    setLocale("en-GB", "long");
    expect(formatValue("2024-03-01", { type: "date" })).toBe("1 March 2024");
    setLocale("en-US", "short");
    expect(formatValue("2024-03-01 14:30:00", { type: "datetime" })).toBe("3/1/24, 2:30 PM");
  });
});

describe("axe-core", () => {
  it("finds no WCAG 2.2 A/AA violations in charts and their data tables", async () => {
    const axe = (await import("axe-core")).default;
    document.body.innerHTML = "";
    const main = document.createElement("main");
    main.lang = "en";
    for (const mark of ["bar", "line", "pie", "treemap"] as const) {
      const figure = document.createElement("div");
      figure.append(renderChart({ mark, x: mark === "bar" || mark === "line" ? "Region" : "Region", y: "Sales" }, data, { width: 300, height: 200, onSelect: () => undefined }));
      figure.append(dataTable({ ...data, truncated: false, query: "q" }, `Sales by region (${mark})`));
      main.append(figure);
    }
    document.body.append(main);
    const result = await axe.run(main, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  });
});
