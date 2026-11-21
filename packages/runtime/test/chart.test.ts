import { describe, expect, it, vi } from "vitest";

import { categoryFormatter } from "../src/chart.js";
import { ChartError, niceTicks, renderChart, type ChartData } from "../src/index.js";

const byRegion: ChartData = {
  columns: [
    { name: "Region", type: "string", role: "dimension" },
    { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
  ],
  rows: [
    ["East", 300],
    ["West", 200],
    ["South", -50],
  ],
};

describe("renderChart", () => {
  it("draws one bar per row with tooltips, horizontally when the category is on y", () => {
    const svg = renderChart({ mark: "bar", x: "Sales", y: "Region" }, byRegion, { width: 400, height: 200 });
    const bars = svg.querySelectorAll("rect.tf-mark");
    expect(bars).toHaveLength(3);
    expect(bars[0]?.querySelector("title")?.textContent).toBe("Region: East\nSales: $300");
    const labels = [...svg.querySelectorAll(".tf-axis text")].map((t) => t.textContent);
    expect(labels).toEqual(expect.arrayContaining(["East", "West", "South"]));
  });

  it("dims marks outside the highlight and reports clicks", () => {
    const onSelect = vi.fn();
    const svg = renderChart({ mark: "bar", x: "Region", y: "Sales" }, byRegion, {
      width: 400,
      height: 200,
      highlight: [{ Region: "West" }],
      onSelect,
    });
    const bars = [...svg.querySelectorAll("rect.tf-mark")];
    expect(bars.map((b) => b.classList.contains("tf-dim"))).toEqual([true, false, true]);
    bars[1]?.dispatchEvent(new Event("click"));
    expect(onSelect).toHaveBeenCalledWith({ Region: "West" });
  });

  it("draws a line per colour series and a legend", () => {
    const data: ChartData = {
      columns: [
        { name: "Month", type: "datetime", role: "dimension" },
        { name: "Segment", type: "string", role: "dimension" },
        { name: "Sales", type: "float", role: "measure" },
      ],
      rows: [
        ["2024-01-01 00:00:00", "A", 1],
        ["2024-02-01 00:00:00", "A", 2],
        ["2024-01-01 00:00:00", "B", 3],
        ["2024-02-01 00:00:00", "B", 4],
      ],
    };
    const svg = renderChart({ mark: "line", x: "Month", y: "Sales", color: "Segment" }, data, { width: 400, height: 200 });
    expect(svg.querySelectorAll("path.tf-line")).toHaveLength(2);
    expect([...svg.querySelectorAll(".tf-legend text")].map((t) => t.textContent)).toEqual(["A", "B"]);
    expect([...svg.querySelectorAll(".tf-axis text")].map((t) => t.textContent)).toContain("Jan 2024");
  });

  it("draws a scatter when both axes are measures", () => {
    const data: ChartData = {
      columns: [
        { name: "Sales", type: "float", role: "measure" },
        { name: "Profit", type: "float", role: "measure" },
      ],
      rows: [
        [1, 2],
        [3, null],
      ],
    };
    const svg = renderChart({ mark: "point", x: "Sales", y: "Profit" }, data, { width: 300, height: 200 });
    expect(svg.querySelectorAll("circle.tf-mark")).toHaveLength(1);
  });

  it("explains a spec that does not fit the data", () => {
    expect(() => renderChart({ mark: "bar", x: "Nope", y: "Sales" }, byRegion, { width: 1, height: 1 })).toThrow(ChartError);
  });
});

describe("axis helpers", () => {
  it("produces nice ticks that cover the range", () => {
    expect(niceTicks(0, 87)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(-50, 300)).toEqual([-100, 0, 100, 200, 300]);
    expect(niceTicks(5, 5)).toEqual([0, 2, 4, 6, 8, 10]); // a flat series still gets an axis from zero
  });

  it("labels months and years compactly", () => {
    const col = { name: "d", type: "date" as const };
    expect(categoryFormatter(["2024-01-01", "2024-02-01"], col)("2024-02-01")).toBe("Feb 2024");
    expect(categoryFormatter(["2023-01-01", "2024-01-01"], col)("2024-01-01")).toBe("2024");
    expect(categoryFormatter(["2024-01-05"], col)("2024-01-05")).toBe("2024-01-05");
  });
});

describe("quarter labels", () => {
  it("labels a quarterly series as quarters and a monthly one as months", () => {
    const col = { name: "d", type: "datetime" as const };
    const quarters = ["2023-01-01 00:00:00", "2023-04-01 00:00:00", "2023-07-01 00:00:00"];
    expect(categoryFormatter(quarters, col)("2023-04-01 00:00:00")).toBe("2023 Q2");
    const months = ["2023-01-01", "2023-02-01", "2023-03-01", "2023-04-01"];
    expect(categoryFormatter(months, col)("2023-04-01")).toBe("Apr 2023");
  });
});
