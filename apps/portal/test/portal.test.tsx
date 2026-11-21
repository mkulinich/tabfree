import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, get, label, type RuleJson, send } from "../src/api.js";
import { RulesGrid } from "../src/components/permissions.js";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("api", () => {
  it("sends the CSRF header and surfaces the API's detail", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: "no dashboard 'x'" }), { status: 404 }));
    vi.stubGlobal("fetch", fetch);
    await expect(send("PUT", "/api/favorites/dashboard/x")).rejects.toEqual(new ApiError(404, "no dashboard 'x'"));
    const init = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(init.headers).toMatchObject({ "x-tabfree-request": "1" });
  });

  it("drops empty query parameters", async () => {
    const fetch = vi.fn(async () => new Response("[]"));
    vi.stubGlobal("fetch", fetch);
    await get("/api/content", { q: "sales", project: undefined, kind: "" });
    expect((fetch.mock.calls[0] as unknown as [string])[0]).toBe("/api/content?q=sales");
  });

  it("labels capabilities with Tableau's names", () => {
    expect(label("download_summary_data")).toBe("Download Summary Data");
    expect(label("unknown_thing")).toBe("unknown_thing");
  });
});

describe("permission rules grid", () => {
  it("cycles a cell through allowed, denied and unspecified, and adds grantees", () => {
    let rules: RuleJson[] = [{ user: null, group: "Managers", capabilities: {} }];
    const onChange = (next: RuleJson[]) => {
      rules = next;
      view.rerender(<RulesGrid rules={rules} capabilities={["view", "filter"]} onChange={onChange} readOnly={false} />);
    };
    const view = render(<RulesGrid rules={rules} capabilities={["view", "filter"]} onChange={onChange} readOnly={false} />);
    const cell = () => screen.getByRole("button", { name: /Managers: View/ });
    fireEvent.click(cell());
    expect(rules[0]?.capabilities).toEqual({ view: "allow" });
    fireEvent.click(cell());
    expect(rules[0]?.capabilities).toEqual({ view: "deny" });
    fireEvent.click(cell());
    expect(rules[0]?.capabilities).toEqual({});
    fireEvent.change(screen.getByLabelText("User or group name"), { target: { value: "Executives" } });
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    expect(rules.map((r) => r.group)).toEqual(["Managers", "Executives"]);
  });

  it("is read-only for locked content", () => {
    render(
      <RulesGrid
        rules={[{ user: "alice", group: null, capabilities: { view: "allow" } }]}
        capabilities={["view"]}
        onChange={() => undefined}
        readOnly
      />,
    );
    expect(screen.getByRole("button", { name: /alice: View Allowed/ })).toHaveProperty("disabled", true);
    expect(screen.queryByRole("button", { name: "Add rule" })).toBeNull();
  });
});

describe("edit with Claude", () => {
  it("shows the person's messages, Claude's replies and tool steps", async () => {
    const { ChatLog } = await import("../src/pages/draft.js");
    render(
      <ChatLog
        events={[
          { type: "user", text: "Make it stacked" },
          { type: "tool", text: "Editing the draft: 1 change(s) to index.html" },
          { type: "done", text: "Stacked the region chart." },
        ]}
      />,
    );
    expect(screen.getByText("Make it stacked").textContent).toBe("You: Make it stacked");
    expect(screen.getByText(/Editing the draft/).className).toBe("chat-tool");
    expect(screen.getByText("Stacked the region chart.").textContent).toBe(
      "Claude: Stacked the region chart.",
    );
  });
});
