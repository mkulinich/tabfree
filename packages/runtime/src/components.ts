/**
 * The `<tf-*>` web components (ADR-002). Claude writes dashboards with these; each one names a
 * query, filter or parameter from tabfree.json and gets data only through the SDK.
 *
 * - `<tf-dashboard>`: root container; applies the theme and reports the frame height.
 * - `<tf-chart query mark x y color>`: bar, line, area or point chart.
 * - `<tf-crosstab query>`: a sortable table.
 * - `<tf-kpi query measure label change>`: one big number, optionally with a ▲/▼ change measure.
 * - `<tf-map query geo color>`: US state tile map; click a state to select it.
 * - `<tf-filter name control>`: dropdown, checklist, relative-date, range or wildcard control.
 * - `<tf-parameter name control>`: slider, dropdown or input.
 */

import { ChartError, renderChart, type Mark } from "./chart.js";
import { h } from "./dom.js";
import { formatValue } from "./format.js";
import { MapError, renderStateMap } from "./map.js";
import type { FilterSpec, FilterValue, Period, Scalar, Tuple } from "./protocol.js";
import { client, QueryError, type QueryResult, type TabfreeClient } from "./sdk.js";

abstract class TfElement extends HTMLElement {
  protected tf: TabfreeClient | null = null;
  #generation = 0;
  readonly #onState = () => void this.update();

  connectedCallback(): void {
    if (!this.hasAttribute("aria-busy")) this.setAttribute("aria-busy", "true");
    client()
      .then((c) => {
        this.tf = c;
        c.addEventListener("state", this.#onState);
        void this.update();
      })
      .catch((error: unknown) => this.showError(error));
  }

  disconnectedCallback(): void {
    this.tf?.removeEventListener("state", this.#onState);
  }

  attributeChangedCallback(): void {
    if (this.tf) void this.update();
  }

  /** Re-render; results from an older update that arrive late are dropped. */
  protected async update(): Promise<void> {
    const generation = ++this.#generation;
    try {
      await this.render(() => generation === this.#generation);
      this.removeAttribute("aria-busy");
    } catch (error) {
      if (generation === this.#generation) this.showError(error);
    }
  }

  protected abstract render(isCurrent: () => boolean): Promise<void>;

  protected showError(error: unknown): void {
    this.removeAttribute("aria-busy");
    const message =
      error instanceof QueryError || error instanceof ChartError || error instanceof Error
        ? error.message
        : "Something went wrong.";
    this.replaceChildren(h("div", { class: "tf-error", role: "alert" }, message));
    this.tf?.reportError(`<${this.localName}>: ${message}`);
  }

  protected title_(): HTMLElement | null {
    const title = this.getAttribute("title");
    return title ? h("div", { class: "tf-title" }, title) : null;
  }
}

function sameTuple(a: Tuple, b: Tuple): boolean {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => a[k] === b[k]);
}

function required(el: HTMLElement, name: string): string {
  const value = el.getAttribute(name);
  if (!value) throw new Error(`missing the "${name}" attribute`);
  return value;
}

/** A tuple from a result row: the dimension columns only (what actions filter on). */
function dimensionTuple(result: QueryResult, row: Scalar[]): Tuple {
  const tuple: Tuple = {};
  result.columns.forEach((c, i) => {
    if (c.role !== "measure") tuple[c.name] = row[i] ?? null;
  });
  return tuple;
}

// ---- tf-dashboard ------------------------------------------------------------------------------

export class TfDashboard extends TfElement {
  #observer: ResizeObserver | null = null;

  override connectedCallback(): void {
    super.connectedCallback();
    if (typeof ResizeObserver !== "undefined") {
      this.#observer = new ResizeObserver(() => this.tf?.resize(document.documentElement.scrollHeight));
      this.#observer.observe(document.documentElement);
    }
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#observer?.disconnect();
  }

  protected async render(): Promise<void> {
    if (!this.tf) return;
    document.documentElement.dataset.theme = this.tf.theme.mode;
    this.tf.resize(document.documentElement.scrollHeight);
  }
}

// ---- tf-chart ----------------------------------------------------------------------------------

const MARKS: readonly string[] = ["bar", "line", "area", "point"];

export class TfChart extends TfElement {
  static observedAttributes = ["query", "mark", "x", "y", "color", "height"];
  #selected: Tuple | null = null;

  #select(query: string, tuple: Tuple | null): void {
    const same = tuple !== null && this.#selected !== null && sameTuple(tuple, this.#selected);
    this.#selected = same ? null : tuple;
    this.tf?.select(query, this.#selected ? [this.#selected] : []);
  }

  protected async render(isCurrent: () => boolean): Promise<void> {
    if (!this.tf) return;
    const query = required(this, "query");
    const mark = this.getAttribute("mark") ?? "bar";
    if (!MARKS.includes(mark)) throw new ChartError(`unknown mark "${mark}"`);
    const result = await this.tf.query(query);
    if (!isCurrent()) return;
    const highlight = this.tf.state.highlight;
    const svg = renderChart(
      {
        mark: mark as Mark,
        x: required(this, "x"),
        y: required(this, "y"),
        color: this.getAttribute("color") ?? undefined,
        title: this.getAttribute("title") ?? undefined,
      },
      result,
      {
        width: Math.max(240, Math.floor(this.clientWidth || 560)),
        height: Number(this.getAttribute("height")) || 300,
        // The dashboard's highlight action wins; otherwise emphasise this chart's own selection.
        highlight: highlight ? highlight.tuples : this.#selected ? [this.#selected] : null,
        onSelect: (tuple) => this.#select(query, tuple),
      },
    );
    svg.addEventListener("click", () => {
      if (this.#selected) this.#select(query, null);
    });
    this.replaceChildren(...[this.title_(), svg].filter((n): n is HTMLElement | SVGSVGElement => n !== null));
    if (result.truncated) this.append(h("div", { class: "tf-note" }, "Showing the first rows only."));
  }
}

// ---- tf-crosstab -------------------------------------------------------------------------------

export class TfCrosstab extends TfElement {
  static observedAttributes = ["query", "max-rows"];
  #sort: { column: number; desc: boolean } | null = null;
  #result: QueryResult | null = null;

  protected async render(isCurrent: () => boolean): Promise<void> {
    if (!this.tf) return;
    const result = await this.tf.query(required(this, "query"));
    if (!isCurrent()) return;
    this.#result = result;
    this.#draw();
  }

  #draw(): void {
    const result = this.#result;
    if (!result) return;
    const query = required(this, "query");
    const limit = Number(this.getAttribute("max-rows")) || 1000;
    const rows = [...result.rows];
    const sort = this.#sort;
    if (sort) {
      rows.sort((a, b) => {
        const x = a[sort.column] ?? null;
        const y = b[sort.column] ?? null;
        const order = x === y ? 0 : x === null ? 1 : y === null ? -1 : x < y ? -1 : 1;
        return sort.desc ? -order : order;
      });
    }
    const head = h(
      "tr",
      {},
      ...result.columns.map((c, i) => {
        const th = h("th", { scope: "col", class: c.role === "measure" ? "tf-num" : null }, c.name);
        if (sort?.column === i) th.setAttribute("aria-sort", sort.desc ? "descending" : "ascending");
        th.addEventListener("click", () => {
          this.#sort = { column: i, desc: sort?.column === i ? !sort.desc : c.role === "measure" };
          this.#draw();
        });
        return th;
      }),
    );
    const body = h("tbody");
    for (const row of rows.slice(0, limit)) {
      const tr = h(
        "tr",
        {},
        ...result.columns.map((c, i) =>
          h("td", { class: c.role === "measure" ? "tf-num" : null }, formatValue(row[i] ?? null, c)),
        ),
      );
      tr.addEventListener("click", () => this.tf?.select(query, [dimensionTuple(result, row)]));
      body.append(tr);
    }
    const table = h("table", { class: "tf-table" }, h("thead", {}, head), body);
    const notes = [];
    if (rows.length > limit) notes.push(h("div", { class: "tf-note" }, `Showing ${limit} of ${rows.length} rows.`));
    if (result.truncated) notes.push(h("div", { class: "tf-note" }, "The query returned the first rows only."));
    this.replaceChildren(...[this.title_(), h("div", { class: "tf-scroll" }, table), ...notes].filter((n) => n !== null));
  }
}

// ---- tf-kpi ------------------------------------------------------------------------------------

export class TfKpi extends TfElement {
  static observedAttributes = ["query", "measure", "label", "change"];

  protected async render(isCurrent: () => boolean): Promise<void> {
    if (!this.tf) return;
    const result = await this.tf.query(required(this, "query"));
    if (!isCurrent()) return;
    const measure = required(this, "measure");
    const i = result.columns.findIndex((c) => c.name === measure);
    const column = result.columns[i];
    if (i < 0 || !column) throw new Error(`the query has no column "${measure}"`);
    const value = result.rows[0]?.[i] ?? null;
    const parts: HTMLElement[] = [
      h("div", { class: "tf-kpi-label" }, this.getAttribute("label") ?? measure),
      h("div", { class: "tf-kpi-value" }, formatValue(value, column)),
    ];
    const changeName = this.getAttribute("change");
    if (changeName) {
      const ci = result.columns.findIndex((c) => c.name === changeName);
      if (ci < 0) throw new Error(`the query has no column "${changeName}"`);
      const change = result.rows[0]?.[ci] ?? null;
      const direction = typeof change !== "number" ? "flat" : change > 0.02 ? "up" : change < -0.02 ? "down" : "flat";
      const arrow = { up: "▲", down: "▼", flat: "■" }[direction];
      const text = typeof change === "number" ? formatValue(change, { type: "float", format: "0.0%" }) : "n/a";
      parts.push(h("div", { class: `tf-kpi-change tf-${direction}` }, `${arrow} ${text}`));
    }
    this.replaceChildren(...parts);
  }
}

// ---- tf-map -----------------------------------------------------------------------------------

export class TfMap extends TfElement {
  static observedAttributes = ["query", "geo", "color"];
  #selected: Tuple | null = null;

  protected async render(isCurrent: () => boolean): Promise<void> {
    if (!this.tf) return;
    const query = required(this, "query");
    const result = await this.tf.query(query);
    if (!isCurrent()) return;
    const highlight = this.tf.state.highlight;
    let svg: SVGSVGElement;
    try {
      svg = renderStateMap(required(this, "geo"), required(this, "color"), result, {
        width: Math.max(240, Math.floor(this.clientWidth || 480) - 24),
        highlight: highlight ? highlight.tuples : this.#selected ? [this.#selected] : null,
        onSelect: (tuple) => {
          const same = tuple !== null && this.#selected !== null && sameTuple(tuple, this.#selected);
          this.#selected = same ? null : tuple;
          this.tf?.select(query, this.#selected ? [this.#selected] : []);
        },
      });
    } catch (error) {
      throw error instanceof MapError ? new Error(error.message) : error;
    }
    this.replaceChildren(...[this.title_(), svg].filter((n): n is HTMLElement | SVGSVGElement => n !== null));
  }
}

// ---- tf-filter ---------------------------------------------------------------------------------

const RELATIVE_CHOICES = [1, 3, 6, 12];

export class TfFilter extends TfElement {
  static observedAttributes = ["name", "control"];

  protected async render(isCurrent: () => boolean): Promise<void> {
    if (!this.tf) return;
    const name = required(this, "name");
    const spec = this.tf.filters[name];
    if (!spec) throw new Error(`no filter named "${name}" in tabfree.json`);
    const current = this.tf.state.filters[name] ?? null;
    const id = `tf-filter-${name}`;
    const label = h("label", { for: id, class: "tf-label" }, spec.label);
    let control: HTMLElement;
    if (spec.kind === "categorical") {
      const domain = await this.tf.domain(name);
      if (!isCurrent()) return;
      const column = domain.columns[0];
      const members = domain.rows.map((r) => r[0] ?? null);
      const selected = current && "members" in current ? current.members : null;
      const mode = this.getAttribute("control") ?? (spec.multi ? "checklist" : "dropdown");
      control =
        mode === "checklist"
          ? this.#checklist(id, name, members, selected, column ? (v: Scalar) => formatValue(v, column) : String)
          : this.#dropdown(id, name, members, selected, column ? (v: Scalar) => formatValue(v, column) : String);
    } else if (spec.kind === "relative_date") {
      control = this.#relative(id, name, spec, current);
    } else if (spec.kind === "range") {
      control = this.#range(id, name, current);
    } else {
      const input = h("input", { id, type: "search", placeholder: "Contains…", class: "tf-input" });
      if (current && "wildcard" in current) input.value = current.wildcard.value;
      let timer: ReturnType<typeof setTimeout> | undefined;
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          const value: FilterValue | null = input.value ? { wildcard: { match: "contains", value: input.value } } : null;
          this.tf?.setFilter(name, value);
        }, 300);
      });
      control = input;
    }
    this.replaceChildren(label, control);
  }

  #dropdown(
    id: string,
    name: string,
    members: Scalar[],
    selected: Scalar[] | null,
    show: (v: Scalar) => string,
  ): HTMLElement {
    const select = h("select", { id, class: "tf-input" }, h("option", { value: "" }, "(All)"));
    members.forEach((m, i) => {
      select.append(h("option", { value: String(i), selected: selected?.length === 1 && selected[0] === m }, show(m)));
    });
    select.addEventListener("change", () => {
      const member = select.value === "" ? undefined : members[Number(select.value)];
      this.tf?.setFilter(name, member === undefined ? null : { members: [member] });
    });
    return select;
  }

  #checklist(
    id: string,
    name: string,
    members: Scalar[],
    selected: Scalar[] | null,
    show: (v: Scalar) => string,
  ): HTMLElement {
    const list = h("div", { id, class: "tf-checklist", role: "group" });
    const boxes = members.map((m) => {
      const box = h("input", { type: "checkbox", checked: selected === null || selected.includes(m) });
      list.append(h("label", { class: "tf-check" }, box, show(m)));
      return box;
    });
    const commit = () => {
      const chosen = members.filter((_, i) => boxes[i]?.checked);
      this.tf?.setFilter(name, chosen.length === members.length ? null : { members: chosen });
    };
    for (const box of boxes) box.addEventListener("change", commit);
    return list;
  }

  #relative(id: string, name: string, spec: FilterSpec, current: FilterValue | null): HTMLElement {
    const period: Period =
      (spec.default && "relative" in spec.default ? spec.default.relative.period : undefined) ?? "month";
    const select = h("select", { id, class: "tf-input" }, h("option", { value: "" }, "All dates"));
    const active = current && "relative" in current ? current.relative : null;
    for (const n of RELATIVE_CHOICES) {
      const text = n === 1 ? `This ${period}` : `Last ${n} ${period}s`;
      select.append(h("option", { value: String(n), selected: active?.from === -(n - 1) && (active.to ?? 0) === 0 }, text));
    }
    select.addEventListener("change", () => {
      const n = Number(select.value);
      this.tf?.setFilter(name, select.value === "" ? null : { relative: { period, from: -(n - 1), to: 0 } });
    });
    return select;
  }

  #range(id: string, name: string, current: FilterValue | null): HTMLElement {
    const min = h("input", { id, type: "number", class: "tf-input", placeholder: "Min", "aria-label": "Minimum" });
    const max = h("input", { type: "number", class: "tf-input", placeholder: "Max", "aria-label": "Maximum" });
    if (current && "min" in current && typeof current.min === "number") min.value = String(current.min);
    if (current && "max" in current && typeof current.max === "number") max.value = String(current.max);
    const commit = () => {
      const value: { min?: number; max?: number } = {};
      if (min.value !== "") value.min = Number(min.value);
      if (max.value !== "") value.max = Number(max.value);
      this.tf?.setFilter(name, Object.keys(value).length ? value : null);
    };
    min.addEventListener("change", commit);
    max.addEventListener("change", commit);
    return h("div", { class: "tf-range" }, min, max);
  }
}

// ---- tf-parameter ------------------------------------------------------------------------------

export class TfParameter extends TfElement {
  static observedAttributes = ["name", "control"];

  protected async render(): Promise<void> {
    if (!this.tf) return;
    const name = required(this, "name");
    const spec = this.tf.parameters[name];
    if (!spec) throw new Error(`no parameter named "${name}" in tabfree.json`);
    const value = this.tf.state.parameters[name] ?? spec.default;
    const id = `tf-param-${name}`;
    const label = h("label", { for: id, class: "tf-label" }, spec.label);
    let control: HTMLElement;
    if (spec.list) {
      const list = spec.list;
      const select = h("select", { id, class: "tf-input" });
      list.forEach((v, i) => select.append(h("option", { value: String(i), selected: v === value }, String(v))));
      select.addEventListener("change", () => this.tf?.setParameter(name, list[Number(select.value)] ?? null));
      control = select;
    } else if (spec.min !== undefined && spec.max !== undefined) {
      const output = h("output", { for: id, class: "tf-output" }, String(value));
      const slider = h("input", {
        id,
        type: "range",
        min: spec.min,
        max: spec.max,
        step: spec.step ?? 1,
        value: String(value),
      });
      slider.addEventListener("input", () => (output.textContent = slider.value));
      slider.addEventListener("change", () => this.tf?.setParameter(name, Number(slider.value)));
      control = h("div", { class: "tf-slider" }, slider, output);
    } else {
      const numeric = spec.type === "integer" || spec.type === "float";
      const input = h("input", { id, class: "tf-input", type: numeric ? "number" : "text", value: String(value ?? "") });
      input.addEventListener("change", () => this.tf?.setParameter(name, numeric ? Number(input.value) : input.value));
      control = input;
    }
    this.replaceChildren(label, control);
  }
}

export const ELEMENTS: Record<string, CustomElementConstructor> = {
  "tf-dashboard": TfDashboard,
  "tf-chart": TfChart,
  "tf-crosstab": TfCrosstab,
  "tf-kpi": TfKpi,
  "tf-map": TfMap,
  "tf-filter": TfFilter,
  "tf-parameter": TfParameter,
};

export function defineElements(registry: CustomElementRegistry = customElements): void {
  for (const [name, ctor] of Object.entries(ELEMENTS)) {
    if (!registry.get(name)) registry.define(name, ctor);
  }
}
