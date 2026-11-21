import { describe, expect, it, vi } from "vitest";

import { MapError, renderStateMap, US_STATE_TILES } from "../src/index.js";

const data = {
  columns: [
    { name: "State", type: "string" as const, role: "dimension" as const },
    { name: "Sales", type: "float" as const, role: "measure" as const, format: "$#,##0" },
  ],
  rows: [
    ["California", 300],
    ["TX", 100],
    ["Atlantis", 5],
  ],
};

describe("renderStateMap", () => {
  it("draws a tile per state, shades by value and accepts names or postal codes", () => {
    const svg = renderStateMap("State", "Sales", data, { width: 400 });
    expect(svg.querySelectorAll("rect.tf-empty").length + svg.querySelectorAll("rect.tf-mark").length).toBe(
      Object.keys(US_STATE_TILES).length,
    );
    const marks = [...svg.querySelectorAll("rect.tf-mark")];
    expect(marks).toHaveLength(2); // Atlantis is ignored
    const titles = marks.map((m) => m.querySelector("title")?.textContent);
    expect(titles).toEqual(expect.arrayContaining(["California\nSales: $300", "Texas\nSales: $100"]));
    const opacity = (name: string) =>
      Number(marks.find((m) => m.querySelector("title")?.textContent?.startsWith(name))?.getAttribute("fill-opacity"));
    expect(opacity("California")).toBeGreaterThan(opacity("Texas"));
  });

  it("selects a state on click and dims the rest when highlighted", () => {
    const onSelect = vi.fn();
    const svg = renderStateMap("State", "Sales", data, { width: 400, onSelect, highlight: [{ State: "California" }] });
    const marks = [...svg.querySelectorAll("rect.tf-mark")];
    const ca = marks.find((m) => m.querySelector("title")?.textContent?.startsWith("California"));
    const tx = marks.find((m) => m.querySelector("title")?.textContent?.startsWith("Texas"));
    expect(ca?.classList.contains("tf-dim")).toBe(false);
    expect(tx?.classList.contains("tf-dim")).toBe(true);
    tx?.dispatchEvent(new Event("click"));
    expect(onSelect).toHaveBeenCalledWith({ State: "TX" });
  });

  it("explains missing columns", () => {
    expect(() => renderStateMap("Nope", "Sales", data, { width: 400 })).toThrow(MapError);
  });
});
