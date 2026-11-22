/** P6.11: more chart types, reference and trend lines, parameter and URL actions, navigation. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { linearTrend, referenceValue } from "../src/chart.js";
import { actionUrl } from "../src/host.js";
import { histogram, quartiles, squarify } from "../src/marks.js";
import { parseDashboardMessage } from "../src/protocol.js";
import {
  ChartError,
  mountDashboard,
  renderChart,
  TabfreeClient,
  type ChartData,
  type DashboardHost,
  type FrameWindow,
  type Manifest,
} from "../src/index.js";

const sales: ChartData = {
  columns: [
    { name: "Region", type: "string", role: "dimension" },
    { name: "Sales", type: "float", role: "measure" },
    { name: "Target", type: "float", role: "measure" },
  ],
  rows: [
    ["East", 300, 250],
    ["West", 200, 260],
    ["South", 100, 120],
  ],
};

describe("more chart types", () => {
  it("draws pies, treemaps, heatmaps and bullets with tooltips and clicks", () => {
    const onSelect = vi.fn();
    const pie = renderChart({ mark: "pie", x: "Region", y: "Sales" }, sales, { width: 300, height: 200, onSelect });
    const slices = pie.querySelectorAll("path.tf-mark");
    expect(slices).toHaveLength(3);
    expect(slices[0]?.querySelector("title")?.textContent).toContain("Share: 50.0%");
    slices[1]?.dispatchEvent(new Event("click"));
    expect(onSelect).toHaveBeenCalledWith({ Region: "West" });
    const tree = renderChart({ mark: "treemap", x: "Region", y: "Sales" }, sales, { width: 300, height: 200 });
    expect(tree.querySelectorAll("rect.tf-mark")).toHaveLength(3);
    const bullet = renderChart({ mark: "bullet", x: "Sales", y: "Region", target: "Target" }, sales, { width: 300, height: 200 });
    expect(bullet.querySelectorAll("line.tf-target")).toHaveLength(3);
    const grid: ChartData = {
      columns: [
        { name: "Region", type: "string", role: "dimension" },
        { name: "Segment", type: "string", role: "dimension" },
        { name: "Sales", type: "float", role: "measure" },
      ],
      rows: [
        ["East", "A", 1],
        ["East", "B", 5],
        ["West", "A", 3],
      ],
    };
    const heat = renderChart({ mark: "heatmap", x: "Region", y: "Segment", color: "Sales" }, grid, { width: 300, height: 200 });
    const cells = [...heat.querySelectorAll("rect.tf-mark")];
    expect(cells).toHaveLength(3);
    expect(cells.map((c) => c.getAttribute("fill-opacity"))).toEqual(["0.15", "1.00", "0.57"]);
    expect(() => renderChart({ mark: "pie", x: "Nope", y: "Sales" }, sales, { width: 300, height: 200 })).toThrow(ChartError);
  });

  it("bins histograms, draws box plots from quartiles and Gantt bars over dates", () => {
    expect(histogram([1, 2, 2, 3, 10], 3).map((b) => b[2])).toEqual([4, 0, 1]);
    expect(quartiles([1, 2, 3, 4, 5])).toEqual({ min: 1, q1: 2, median: 3, q3: 4, max: 5 });
    const values: ChartData = {
      columns: [
        { name: "Region", type: "string", role: "dimension" },
        { name: "Sales", type: "float", role: "measure" },
      ],
      rows: [["East", 1], ["East", 5], ["West", 3], ["West", 9]],
    };
    const hist = renderChart({ mark: "histogram", x: "Sales", y: "Sales", bins: 4 }, values, { width: 300, height: 200 });
    expect(hist.querySelectorAll("rect.tf-mark")).toHaveLength(4);
    const box = renderChart({ mark: "boxplot", x: "Region", y: "Sales" }, values, { width: 300, height: 200 });
    expect(box.querySelectorAll("g.tf-mark")).toHaveLength(2);
    expect(box.querySelector("g.tf-mark title")?.textContent).toContain("Median:");
    const tasks: ChartData = {
      columns: [
        { name: "Task", type: "string", role: "dimension" },
        { name: "Start", type: "date", role: "dimension" },
        { name: "Days", type: "integer", role: "measure" },
      ],
      rows: [["Design", "2027-01-01", 5], ["Build", "2027-01-06", 10]],
    };
    const gantt = renderChart({ mark: "gantt", x: "Start", y: "Task", size: "Days" }, tasks, { width: 400, height: 200 });
    const bars = [...gantt.querySelectorAll("rect.tf-mark")].map((b) => Number(b.getAttribute("width")));
    expect(bars).toHaveLength(2);
    expect(bars[1]).toBeGreaterThan(bars[0] ?? 0); // ten days is longer than five
  });

  it("lays treemaps out to fill the box in proportion", () => {
    const boxes = squarify([6, 6, 4, 3, 2, 2, 1], { x: 0, y: 0, w: 600, h: 400 });
    const area = boxes.reduce((a, b) => a + b.w * b.h, 0);
    expect(area).toBeCloseTo(600 * 400, 3);
    expect((boxes[0]?.w ?? 0) * (boxes[0]?.h ?? 0)).toBeCloseTo((6 / 24) * 600 * 400, 3);
  });
});

describe("reference lines, bands and trends", () => {
  it("computes references and fits a line", () => {
    expect(referenceValue("average", [1, 2, 3, 6])).toBe(3);
    expect(referenceValue("median", [1, 2, 3, 6])).toBe(2.5);
    expect(referenceValue("250", [1])).toBe(250);
    expect(referenceValue("bogus", [1])).toBeNull();
    expect(linearTrend([1, 2, null, 4])).toEqual([1, 1]);
  });

  it("draws them over bar and line charts", () => {
    const svg = renderChart(
      { mark: "line", x: "Region", y: "Sales", reference: "average", band: "quartiles", trend: "linear" },
      sales,
      { width: 400, height: 200 },
    );
    expect(svg.querySelector(".tf-reference-label")?.textContent).toBe("average: 200");
    expect(svg.querySelector("rect.tf-band")).not.toBeNull();
    expect(svg.querySelector("line.tf-trend")).not.toBeNull();
  });
});

describe("actions and navigation", () => {
  it("fills URL templates and refuses other schemes", () => {
    expect(actionUrl("https://crm.example/a?region=<Region>", { Region: "East & West" })).toBe(
      "https://crm.example/a?region=East%20%26%20West",
    );
    expect(actionUrl("javascript:alert(<Region>)", { Region: "x" })).toBeNull();
    expect(actionUrl("not a url <Region>", { Region: "x" })).toBeNull();
  });

  it("checks navigation messages", () => {
    expect(parseDashboardMessage({ v: 0, type: "navigate", dashboard: "sales-overview" })).toEqual({
      v: 0,
      type: "navigate",
      dashboard: "sales-overview",
    });
    expect(parseDashboardMessage({ v: 0, type: "navigate", dashboard: "../admin" })).toBeNull();
    expect(parseDashboardMessage({ v: 0, type: "navigate", dashboard: "x", extra: 1 })).toBeNull();
  });
});

const manifest: Manifest = {
  id: "sales",
  title: "Sales",
  parameters: { focus: { type: "string", default: "All" } },
  queries: { by_region: {} },
  actions: [
    { type: "parameter", source: ["by_region"], parameter: "focus", field: "Region" },
    { type: "url", source: ["by_region"], url: "https://crm.example/<Region>" },
  ],
};

async function flush(): Promise<void> {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let host: DashboardHost | null = null;
afterEach(() => {
  host?.destroy();
  host = null;
  vi.restoreAllMocks();
});

it("parameter and URL actions follow a selection; navigation reaches the host", async () => {
  let listener: ((e: MessageEvent) => void) | null = null;
  const add = window.addEventListener.bind(window);
  vi.spyOn(window, "addEventListener").mockImplementation(((type: string, fn: EventListener, opts?: unknown) => {
    if (type === "message") listener = fn as (e: MessageEvent) => void;
    add(type, fn, opts as AddEventListenerOptions);
  }) as typeof window.addEventListener);
  const container = document.createElement("div");
  document.body.append(container);
  const onOpenUrl = vi.fn();
  const onNavigate = vi.fn();
  const onStateChange = vi.fn();
  const query = vi.fn(async () => ({ columns: [], rows: [], truncated: false }));
  host = mountDashboard({ container, manifest, html: "", runtime: "", runQuery: query, runDomain: query, onOpenUrl, onNavigate, onStateChange });
  const hostListener = listener as unknown as (e: MessageEvent) => void;
  const frameListeners = new Set<(e: MessageEvent) => void>();
  const parent = { postMessage: (data: unknown) => hostListener({ data, source: host?.iframe.contentWindow } as MessageEvent) };
  const contentWindow = host.iframe.contentWindow;
  if (!contentWindow) throw new Error("no contentWindow");
  vi.spyOn(contentWindow, "postMessage").mockImplementation(((data: unknown, _o: string, ports?: MessagePort[]) => {
    for (const l of frameListeners) l({ data, source: parent, ports: ports ?? [] } as unknown as MessageEvent);
  }) as typeof contentWindow.postMessage);
  const frame: FrameWindow = {
    parent,
    addEventListener: (_t, l) => frameListeners.add(l),
    removeEventListener: (_t, l) => frameListeners.delete(l),
  };
  const client = await TabfreeClient.connect(frame, 1000);
  client.select("by_region", [{ Region: "West" }]);
  await flush();
  expect(host.state.parameters["focus"]).toBe("West");
  expect(onOpenUrl).toHaveBeenCalledWith("https://crm.example/West", "new_tab");
  client.navigate("other-dashboard");
  await flush();
  expect(onNavigate).toHaveBeenCalledWith("other-dashboard");
});
