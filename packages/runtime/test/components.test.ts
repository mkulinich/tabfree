import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { defineElements, resetClient, type DashboardState, type QueryResult, type TabfreeClient } from "../src/index.js";

const results: Record<string, QueryResult> = {
  by_region: {
    query: "by_region",
    columns: [
      { name: "Region", type: "string", role: "dimension" },
      { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
    ],
    rows: [
      ["East", 300],
      ["<img src=x onerror=alert(1)>", 200],
    ],
    truncated: false,
  },
  kpis: {
    query: "kpis",
    columns: [
      { name: "Profit Ratio", type: "float", role: "measure", format: "0.0%" },
      { name: "Change", type: "float", role: "measure" },
    ],
    rows: [[0.1234, -0.25]],
    truncated: false,
  },
};

class FakeClient extends EventTarget {
  state: DashboardState = { filters: {}, parameters: { top_n: 10 }, highlight: null };
  theme = { mode: "light" as const };
  filters = { region: { label: "Region", kind: "categorical" as const, multi: false, default: null } };
  parameters = { top_n: { label: "Top N", type: "integer" as const, default: 10, min: 1, max: 50, step: 1 } };
  query = vi.fn(async (name: string) => {
    const result = results[name];
    if (!result) throw new Error(`no query ${name}`);
    return result;
  });
  domain = vi.fn(async () => ({ query: "region", columns: [{ name: "Region", type: "string" as const }], rows: [["East"], ["West"]], truncated: false }));
  setFilter = vi.fn();
  setParameter = vi.fn();
  select = vi.fn();
  resize = vi.fn();
  reportError = vi.fn();
}

let fake: FakeClient;
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

beforeAll(() => defineElements());
afterEach(() => {
  document.body.replaceChildren();
});

async function mount(html: string): Promise<HTMLElement> {
  fake = new FakeClient();
  resetClient(Promise.resolve(fake as unknown as TabfreeClient));
  const holder = document.createElement("div");
  holder.innerHTML = html; // test fixture markup, not data
  document.body.append(holder);
  await settle();
  return holder;
}

describe("<tf-*> components", () => {
  it("tf-chart renders bars and selects on click", async () => {
    const root = await mount('<tf-chart query="by_region" mark="bar" x="Sales" y="Region"></tf-chart>');
    const bars = root.querySelectorAll("rect.tf-mark");
    expect(bars).toHaveLength(2);
    bars[0]?.dispatchEvent(new Event("click"));
    expect(fake.select).toHaveBeenCalledWith("by_region", [{ Region: "East" }]);
    bars[0]?.dispatchEvent(new Event("click")); // clicking again clears the selection
    expect(fake.select).toHaveBeenLastCalledWith("by_region", []);
  });

  it("renders data as text, never as markup", async () => {
    const root = await mount('<tf-crosstab query="by_region"></tf-crosstab>');
    expect(root.querySelector("img")).toBeNull();
    const cells = [...root.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells).toEqual(["East", "$300", "<img src=x onerror=alert(1)>", "$200"]);
  });

  it("tf-crosstab sorts on header click and selects rows by dimensions", async () => {
    const root = await mount('<tf-crosstab query="by_region"></tf-crosstab>');
    const header = root.querySelectorAll("th")[1];
    header?.dispatchEvent(new Event("click"));
    expect(root.querySelector("th[aria-sort]")?.getAttribute("aria-sort")).toBe("descending");
    root.querySelector("tbody tr")?.dispatchEvent(new Event("click"));
    expect(fake.select).toHaveBeenCalledWith("by_region", [{ Region: "East" }]);
  });

  it("tf-kpi formats its measure and shows a change", async () => {
    const root = await mount('<tf-kpi query="kpis" measure="Profit Ratio" change="Change"></tf-kpi>');
    expect(root.querySelector(".tf-kpi-value")?.textContent).toBe("12.3%");
    const change = root.querySelector(".tf-kpi-change");
    expect(change?.textContent).toBe("▼ -25.0%");
    expect(change?.classList.contains("tf-down")).toBe(true);
  });

  it("tf-filter offers the domain and sets the filter", async () => {
    const root = await mount('<tf-filter name="region"></tf-filter>');
    const select = root.querySelector("select");
    expect([...(select?.options ?? [])].map((o) => o.textContent)).toEqual(["(All)", "East", "West"]);
    if (!select) throw new Error("no select");
    select.value = "1";
    select.dispatchEvent(new Event("change"));
    expect(fake.setFilter).toHaveBeenCalledWith("region", { members: ["West"] });
    select.value = "";
    select.dispatchEvent(new Event("change"));
    expect(fake.setFilter).toHaveBeenLastCalledWith("region", null);
  });

  it("tf-parameter renders a slider within the allowed range", async () => {
    const root = await mount('<tf-parameter name="top_n"></tf-parameter>');
    const slider = root.querySelector<HTMLInputElement>('input[type="range"]');
    expect(slider?.min).toBe("1");
    expect(slider?.max).toBe("50");
    if (!slider) throw new Error("no slider");
    slider.value = "25";
    slider.dispatchEvent(new Event("change"));
    expect(fake.setParameter).toHaveBeenCalledWith("top_n", 25);
  });

  it("shows a readable error for a bad reference and reports it to the host", async () => {
    const root = await mount('<tf-chart query="by_region" x="Nope" y="Region"></tf-chart>');
    expect(root.querySelector(".tf-error")?.textContent).toBe('the query has no column "Nope"');
    expect(fake.reportError).toHaveBeenCalled();
  });

  it("re-renders when the dashboard state changes", async () => {
    await mount('<tf-kpi query="kpis" measure="Profit Ratio"></tf-kpi>');
    expect(fake.query).toHaveBeenCalledTimes(1);
    fake.dispatchEvent(new CustomEvent("state", { detail: fake.state }));
    await settle();
    expect(fake.query).toHaveBeenCalledTimes(2);
  });
});
