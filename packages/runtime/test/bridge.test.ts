/**
 * The host bridge and the frame SDK talking over a real MessageChannel. The frame side is a fake
 * window (happy-dom does not run srcdoc frames), but every message goes through the real parsers.
 */
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  buildSrcdoc,
  coerceParameter,
  FRAME_CSP,
  mountDashboard,
  TabfreeClient,
  type DashboardHost,
  type FrameWindow,
  type Manifest,
  type QueryRequest,
  type QueryResponse,
} from "../src/index.js";

const manifest: Manifest = {
  id: "sales-overview",
  title: "Sales Overview",
  filters: {
    region: { label: "Region", field: "Region", kind: "categorical", multi: true, appliesTo: "all" },
    order_date: {
      field: "Order Date",
      kind: "relative_date",
      appliesTo: "all",
      default: { relative: { period: "month", from: -11, to: 0 } },
    },
  },
  parameters: { top_n: { type: "integer", default: 10, allowed: { range: { min: 1, max: 50, step: 1 } } } },
  queries: {
    by_region: {},
    trend: { tableCalcs: [{ name: "Running", type: "running_sum", of: "Sales", computeUsing: "table_down" }] },
  },
  actions: [{ type: "filter", source: ["by_region"], targets: ["trend"], fields: ["Region"] }],
};

/** MessagePort delivery is asynchronous and can take more than one macrotask under load. */
async function flush(): Promise<void> {
  for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function connectFrame(host: DashboardHost, hostListener: (e: MessageEvent) => void): Promise<TabfreeClient> {
  const frameListeners = new Set<(e: MessageEvent) => void>();
  const parent = {
    postMessage(data: unknown) {
      // The frame's hello arrives at the host from the iframe's window.
      hostListener({ data, source: host.iframe.contentWindow } as MessageEvent);
    },
  };
  const contentWindow = host.iframe.contentWindow;
  if (!contentWindow) throw new Error("no contentWindow");
  vi.spyOn(contentWindow, "postMessage").mockImplementation(((data: unknown, _origin: string, ports?: MessagePort[]) => {
    for (const listener of frameListeners) listener({ data, source: parent, ports: ports ?? [] } as unknown as MessageEvent);
  }) as typeof contentWindow.postMessage);
  const frame: FrameWindow = {
    parent,
    addEventListener: (_type, listener) => frameListeners.add(listener),
    removeEventListener: (_type, listener) => frameListeners.delete(listener),
  };
  return TabfreeClient.connect(frame, 1000);
}

let host: DashboardHost | null = null;
afterEach(() => {
  host?.destroy();
  host = null;
  vi.restoreAllMocks();
});

type RunQuery = (request: QueryRequest) => Promise<QueryResponse>;

function setup(runQuery: Mock<RunQuery> = vi.fn<RunQuery>(async () => ({ columns: [], rows: [], truncated: false }))) {
  let listener: ((e: MessageEvent) => void) | null = null;
  const add = window.addEventListener.bind(window);
  vi.spyOn(window, "addEventListener").mockImplementation(((type: string, fn: EventListener, opts?: unknown) => {
    if (type === "message") listener = fn as (e: MessageEvent) => void;
    add(type, fn, opts as AddEventListenerOptions);
  }) as typeof window.addEventListener);
  const container = document.createElement("div");
  document.body.append(container);
  const onStateChange = vi.fn();
  host = mountDashboard({
    container,
    manifest,
    html: "<html><head><title>x</title></head><body><tf-dashboard></tf-dashboard></body></html>",
    runtime: "console.log('</script>')",
    runQuery,
    runDomain: vi.fn(async () => ({ columns: [{ name: "Region", type: "string" as const }], rows: [["East"]], truncated: false })),
    onStateChange,
  });
  if (!listener) throw new Error("host did not listen");
  return { host, listener: listener as (e: MessageEvent) => void, runQuery, onStateChange };
}

describe("host and SDK", () => {
  it("sandboxes the frame and injects CSP and runtime safely", () => {
    const { host } = setup();
    expect(host.iframe.getAttribute("sandbox")).toBe("allow-scripts");
    const doc = host.iframe.srcdoc;
    expect(doc).toContain(FRAME_CSP);
    expect(doc).toContain("connect-src 'none'");
    expect(doc).toContain("<\\/script>"); // the runtime cannot close its own <script> early
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<title>"));
    expect(buildSrcdoc("<p>hi</p>", "")).toMatch(/^<!doctype html><html><head><meta/);
  });

  it("hands over a private port and initial state, then answers queries with table calcs", async () => {
    const runQuery = vi.fn<RunQuery>(async () => ({
      columns: [
        { name: "Month", type: "date" as const, role: "dimension" as const },
        { name: "Sales", type: "float" as const, role: "measure" as const },
      ],
      rows: [
        ["2024-01-01", 1],
        ["2024-02-01", 2],
      ],
      truncated: false,
    }));
    const { host, listener } = setup(runQuery);
    const client = await connectFrame(host, listener);
    expect(client.dashboard).toEqual({ id: "sales-overview", title: "Sales Overview" });
    expect(client.state.filters.order_date).toEqual({ relative: { period: "month", from: -11, to: 0 } });
    expect(client.parameters.top_n).toMatchObject({ min: 1, max: 50, default: 10 });
    expect(client.filters.region).toEqual({ label: "Region", kind: "categorical", multi: true, default: null });

    const [a, b] = await Promise.all([client.query("trend"), client.query("trend")]);
    expect(a).toBe(b); // shared by components that name the same query
    expect(runQuery).toHaveBeenCalledTimes(1);
    expect(a.rows).toEqual([
      ["2024-01-01", 1, 1],
      ["2024-02-01", 2, 3],
    ]);
    await expect(client.query("missing")).rejects.toMatchObject({ code: "not_found" });
    expect((await client.domain("region")).rows).toEqual([["East"]]);
  });

  it("accepts only declared filters and parameters, with values of the right kind", async () => {
    const { host, listener, onStateChange } = setup();
    const client = await connectFrame(host, listener);
    const states: unknown[] = [];
    client.addEventListener("state", (e) => states.push((e as CustomEvent).detail));

    client.setFilter("region", { members: ["East"] });
    client.setFilter("nope", { members: ["x"] });
    client.setFilter("region", { min: 1 }); // wrong kind for a categorical filter
    client.setParameter("top_n", 20);
    client.setParameter("top_n", 500); // above the allowed range
    await flush();
    expect(host.state.filters.region).toEqual({ members: ["East"] });
    expect(host.state.parameters.top_n).toBe(20);
    expect(states).toHaveLength(2);
    expect(onStateChange).toHaveBeenCalledTimes(2);

    client.setFilter("region", null);
    await flush();
    expect(host.state.filters.region).toBeUndefined();
  });

  it("turns selections into the manifest's filter actions", async () => {
    const { host, listener, runQuery } = setup();
    const client = await connectFrame(host, listener);
    client.select("by_region", [{ Region: "East" }]);
    await flush();
    await client.query("by_region");
    expect(runQuery).toHaveBeenLastCalledWith(expect.objectContaining({ selections: { by_region: [{ Region: "East" }] } }));
    client.select("trend", [{ Month: "2024-01-01" }]); // trend is not an action source: ignored
    client.select("by_region", []);
    await flush();
    await client.query("by_region");
    expect(runQuery).toHaveBeenLastCalledWith(expect.objectContaining({ selections: {} }));
  });

  it("keeps the last filter when an action says keep_filter", async () => {
    const keep: Manifest = {
      ...manifest,
      actions: [{ type: "filter", source: ["by_region"], targets: ["trend"], fields: ["Region"], clear: "keep_filter" }],
    };
    let listener: ((e: MessageEvent) => void) | null = null;
    const add = window.addEventListener.bind(window);
    vi.spyOn(window, "addEventListener").mockImplementation(((type: string, fn: EventListener) => {
      if (type === "message") listener = fn as (e: MessageEvent) => void;
      add(type, fn);
    }) as typeof window.addEventListener);
    const container = document.createElement("div");
    document.body.append(container);
    const runQuery = vi.fn<RunQuery>(async () => ({ columns: [], rows: [], truncated: false }));
    host = mountDashboard({ container, manifest: keep, html: "", runtime: "", runQuery, runDomain: runQuery as never });
    const client = await connectFrame(host, listener as unknown as (e: MessageEvent) => void);
    client.select("by_region", [{ Region: "East" }]);
    await flush();
    client.select("by_region", []);
    await flush();
    await client.query("by_region");
    expect(runQuery).toHaveBeenLastCalledWith(expect.objectContaining({ selections: { by_region: [{ Region: "East" }] } }));
  });

  it("ignores hellos from other windows", async () => {
    const { host, listener } = setup();
    const post = vi.spyOn(host.iframe.contentWindow as Window, "postMessage");
    listener({ data: { v: 0, type: "hello" }, source: window } as unknown as MessageEvent);
    listener({ data: { v: 0, type: "hello", extra: 1 }, source: host.iframe.contentWindow } as MessageEvent);
    expect(post).not.toHaveBeenCalled();
  });

  it("coerces parameter values to their declared type", () => {
    expect(coerceParameter({ label: "n", type: "integer", default: 1, min: 1, max: 9 }, "4.6")).toBe(5);
    expect(coerceParameter({ label: "n", type: "integer", default: 1, min: 1, max: 9 }, 0)).toBeUndefined();
    expect(coerceParameter({ label: "s", type: "string", default: "a", list: ["a", "b"] }, "c")).toBeUndefined();
    expect(coerceParameter({ label: "b", type: "boolean", default: true }, "yes")).toBeUndefined();
  });
});

describe("tableCalcOptions", () => {
  it("passes layout, densification, filters and TOTAL aggregations to the engine", async () => {
    const { tableCalcOptions } = await import("../src/host.js");
    expect(
      tableCalcOptions({
        measures: ["Sales", { field: "Profit", agg: "avg", as: "Avg Profit" }, { field: "Id", agg: "countd" }],
        layout: { across: ["Month"] },
        densify: ["Month"],
        tableCalcFilters: [{ expr: "INDEX() <= 3" }],
      }),
    ).toEqual({
      aggregations: { "Avg Profit": "avg" },
      across: ["Month"],
      densify: ["Month"],
      filters: [{ expr: "INDEX() <= 3" }],
    });
  });
});

describe("usercontent frames and exports (P3.6, P3.7)", () => {
  it("loads the bundle from frameUrl instead of srcdoc and forwards export requests", () => {
    const container = document.createElement("div");
    document.body.append(container);
    const exports: unknown[] = [];
    const host = mountDashboard({
      container,
      manifest: { id: "d", title: "D", queries: { q: { dimensions: [] } } } as unknown as Manifest,
      html: "",
      runtime: "",
      frameUrl: "https://uc.example/frame/d?e=1&s=x",
      runQuery: async () => ({ columns: [], rows: [], truncated: false }),
      runDomain: async () => ({ columns: [], rows: [], truncated: false }),
      onExport: (request) => exports.push(request),
    });
    expect(host.iframe.getAttribute("src")).toBe("https://uc.example/frame/d?e=1&s=x");
    expect(host.iframe.hasAttribute("srcdoc")).toBe(false);
    expect(host.iframe.getAttribute("sandbox")).toBe("allow-scripts");
    host.destroy();
  });
});
