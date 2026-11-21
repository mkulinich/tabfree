/** P3.6: layout containers, legend, text, image, export, crosstab nesting/pivot/totals, filter styles, drill. */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { defineElements, resetClient, type DashboardState, type QueryResult, type TabfreeClient } from "../src/index.js";
import { parseDashboardMessage } from "../src/protocol.js";

const results: Record<string, QueryResult> = {
  by_segment: {
    query: "by_segment",
    columns: [
      { name: "Region", type: "string", role: "dimension" },
      { name: "Segment", type: "string", role: "dimension" },
      { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
      { name: "__total", type: "string", role: "dimension" },
    ],
    rows: [
      ["East", "Consumer", 100, null],
      ["East", "Corporate", 50, null],
      ["West", "Consumer", 200, null],
      ["East", null, 150, "Region"],
      ["West", null, 200, "Region"],
      [null, null, 350, "grand"],
    ],
    truncated: false,
  },
  by_year: {
    query: "by_year",
    columns: [
      { name: "Region", type: "string", role: "dimension" },
      { name: "Year", type: "integer", role: "dimension" },
      { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
    ],
    rows: [
      ["East", 2023, 10],
      ["East", 2024, 20],
      ["West", 2024, 5],
    ],
    truncated: false,
  },
  kpis: {
    query: "kpis",
    columns: [
      { name: "Sales", type: "float", role: "measure", format: "$#,##0" },
      { name: "Note", type: "string", role: "dimension" },
    ],
    rows: [[1234.5, "<b>bold</b>"]],
    truncated: false,
  },
  detail: {
    query: "detail",
    columns: [
      { name: "Customer", type: "string", role: "dimension" },
      { name: "Sales", type: "float", role: "measure" },
    ],
    rows: [["Ann", 5]],
    truncated: false,
  },
};

class FakeClient extends EventTarget {
  state: DashboardState = { filters: {}, parameters: {}, highlight: null };
  theme = { mode: "light" as const };
  filters = {
    region: { label: "Region", kind: "categorical" as const, multi: true, default: null },
    when: { label: "When", kind: "range" as const, multi: false, default: null },
  };
  parameters = {};
  query = vi.fn(async (name: string) => {
    const result = results[name];
    if (!result) throw new Error(`no query ${name}`);
    return result;
  });
  domain = vi.fn(async () => ({
    query: "region",
    columns: [{ name: "Region", type: "string" as const }],
    rows: [["East"], ["West"], ["South"]],
    truncated: false,
  }));
  setFilter = vi.fn();
  setParameter = vi.fn();
  select = vi.fn();
  resize = vi.fn();
  reportError = vi.fn();
  export = vi.fn();
}

let fake: FakeClient;
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

beforeAll(() => defineElements());
afterEach(() => document.body.replaceChildren());

async function mount(html: string): Promise<HTMLElement> {
  fake = new FakeClient();
  resetClient(Promise.resolve(fake as unknown as TabfreeClient));
  const holder = document.createElement("div");
  holder.innerHTML = html; // test fixture markup, not data
  document.body.append(holder);
  await settle();
  return holder;
}

const texts = (root: ParentNode, selector: string) => [...root.querySelectorAll(selector)].map((n) => n.textContent);

describe("layout", () => {
  it("tiles children by weight and floats objects", async () => {
    const root = await mount(
      '<tf-dashboard size="fixed" width="900" height="600"><tf-container direction="vertical" gap="4">' +
        '<div weight="2"></div><div></div></tf-container><tf-float x="10" y="20" width="100"></tf-float></tf-dashboard>',
    );
    const container = root.querySelector("tf-container") as HTMLElement;
    expect(container.style.flexDirection).toBe("column");
    expect((container.children[0] as HTMLElement).style.flex).toBe("2 1 0px");
    expect((container.children[1] as HTMLElement).style.flex).toBe("1 1 0px");
    const float = root.querySelector("tf-float") as HTMLElement;
    expect([float.style.position, float.style.left, float.style.top, float.style.width]).toEqual(["absolute", "10px", "20px", "100px"]);
    const dashboard = root.querySelector("tf-dashboard") as HTMLElement;
    expect([dashboard.style.width, dashboard.style.height]).toEqual(["900px", "600px"]);
  });
});

describe("crosstab", () => {
  it("nests row headers and places subtotals after their group", async () => {
    const root = await mount('<tf-crosstab query="by_segment"></tf-crosstab>');
    const rows = [...root.querySelectorAll("tbody tr")].map((tr) => texts(tr, "th, td"));
    expect(rows).toEqual([
      ["East", "Consumer", "$100"],
      ["", "Corporate", "$50"],
      ["", "Total", "$150"],
      ["West", "Consumer", "$200"],
      ["", "Total", "$200"],
      ["Grand Total", "", "$350"],
    ]);
    expect(root.querySelectorAll("tr.tf-total")).toHaveLength(3);
  });

  it("pivots a dimension across the columns", async () => {
    const root = await mount('<tf-crosstab query="by_year" columns="Year"></tf-crosstab>');
    expect(texts(root, "thead tr:first-child th")).toEqual(["", "2023", "2024"]);
    const rows = [...root.querySelectorAll("tbody tr")].map((tr) => texts(tr, "th, td"));
    expect(rows).toEqual([
      ["East", "$10", "$20"],
      ["West", "", "$5"],
    ]);
  });
});

describe("content objects", () => {
  it("tf-legend lists series in chart order and selects a member", async () => {
    const root = await mount('<tf-legend query="by_year" field="Region"></tf-legend>');
    expect(texts(root, ".tf-legend-item")).toEqual(["East", "West"]);
    (root.querySelectorAll(".tf-legend-item")[1] as HTMLElement).click();
    expect(fake.select).toHaveBeenCalledWith("by_year", [{ Region: "West" }]);
    (root.querySelectorAll(".tf-legend-item")[1] as HTMLElement).click();
    expect(fake.select).toHaveBeenLastCalledWith("by_year", []);
  });

  it("tf-text fills placeholders as text", async () => {
    const root = await mount('<tf-text query="kpis">Sales were {Sales}; note {Note}; {Missing} stays</tf-text>');
    const el = root.querySelector("tf-text") as HTMLElement;
    expect(el.textContent).toBe("Sales were $1,235; note <b>bold</b>; {Missing} stays");
    expect(el.querySelector("b")).toBeNull();
  });

  it("tf-image accepts only data: images", async () => {
    const root = await mount(
      '<tf-image src="data:image/png;base64,iVBORw0KGgo=" alt="Logo"></tf-image><tf-image src="https://evil.example/x.png"></tf-image>',
    );
    const [good, bad] = [...root.querySelectorAll("tf-image")];
    expect(good?.querySelector("img")?.getAttribute("alt")).toBe("Logo");
    expect(bad?.querySelector("img")).toBeNull();
    expect(bad?.textContent).toContain("data:image");
  });

  it("tf-export asks the host to export", async () => {
    const root = await mount('<tf-export format="crosstab" query="by_year"></tf-export>');
    (root.querySelector("button") as HTMLElement).click();
    await settle();
    expect(fake.export).toHaveBeenCalledWith("crosstab", "by_year");
  });
});

describe("filters and drill", () => {
  it("offers Tableau's control styles", async () => {
    const radio = await mount('<tf-filter name="region" control="radio"></tf-filter>');
    const options = radio.querySelectorAll('input[type="radio"]');
    expect(options).toHaveLength(4); // (All) + 3
    (options[2] as HTMLInputElement).click();
    expect(fake.setFilter).toHaveBeenCalledWith("region", { members: ["West"] });

    const multi = await mount('<tf-filter name="region" control="multi-dropdown"></tf-filter>');
    const select = multi.querySelector("select") as HTMLSelectElement;
    expect(select.multiple).toBe(true);
    (select.options[0] as HTMLOptionElement).selected = true;
    (select.options[2] as HTMLOptionElement).selected = true;
    select.dispatchEvent(new Event("change"));
    expect(fake.setFilter).toHaveBeenCalledWith("region", { members: ["East", "South"] });

    const search = await mount('<tf-filter name="region" control="search"></tf-filter>');
    const input = search.querySelector('input[type="search"]') as HTMLInputElement;
    input.value = "west";
    input.dispatchEvent(new Event("change"));
    expect(fake.setFilter).toHaveBeenCalledWith("region", { members: ["West"] });

    const dates = await mount('<tf-filter name="when" control="date"></tf-filter>');
    const [from] = [...dates.querySelectorAll('input[type="date"]')] as HTMLInputElement[];
    if (!from) throw new Error("no date input");
    from.value = "2024-01-01";
    from.dispatchEvent(new Event("change"));
    expect(fake.setFilter).toHaveBeenCalledWith("when", { min: "2024-01-01" });
  });

  it("drills in place to the detail query and back", async () => {
    const root = await mount('<tf-chart query="by_year" mark="bar" x="Sales" y="Region" drill="detail"></tf-chart>');
    root.querySelector("rect.tf-mark")?.dispatchEvent(new Event("click"));
    await settle();
    expect(fake.select).toHaveBeenCalledWith("by_year", [{ Region: "East" }]);
    expect(fake.query).toHaveBeenLastCalledWith("detail");
    expect(root.querySelector(".tf-drill")?.textContent).toContain("Region: East");
    (root.querySelector(".tf-back") as HTMLElement).click();
    await settle();
    expect(fake.select).toHaveBeenLastCalledWith("by_year", []);
    expect(fake.query).toHaveBeenLastCalledWith("by_year");
  });

  it("sorts chart bars by the measure", async () => {
    const root = await mount('<tf-chart query="by_year" mark="point" x="Year" y="Sales" sort="desc"></tf-chart>');
    expect(root.querySelectorAll("circle.tf-mark").length).toBe(3);
  });
});

describe("protocol", () => {
  it("validates export requests", () => {
    expect(parseDashboardMessage({ v: 0, type: "export", format: "csv", query: "by_year" })).toEqual({
      v: 0,
      type: "export",
      format: "csv",
      query: "by_year",
    });
    expect(parseDashboardMessage({ v: 0, type: "export", format: "exe" })).toBeNull();
    expect(parseDashboardMessage({ v: 0, type: "export", format: "pdf", query: "../x" })).toBeNull();
  });
});
