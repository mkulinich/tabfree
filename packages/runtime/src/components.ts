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
 *
 * Layout and content objects (`tf-container`, `tf-float`, `tf-legend`, `tf-text`, `tf-image`,
 * `tf-export`) are in layout.ts.
 */

import { ChartError, MARK_NAMES, renderChart, type Mark } from "./chart.js";
import { h } from "./dom.js";
import { formatValue } from "./format.js";
import { LAYOUT_ELEMENTS } from "./layout.js";
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
      this.#observer = new ResizeObserver(() => {
        this.#device();
        this.tf?.resize(document.documentElement.scrollHeight);
      });
      this.#observer.observe(document.documentElement);
    }
    this.#device();
  }

  /** Device layouts (P6.11): below ``phone-width`` (default 600 px), `phone="stack"` stacks the layout. */
  #device(): void {
    const limit = Number(this.getAttribute("phone-width")) || 600;
    const narrow = this.getAttribute("phone") === "stack" && document.documentElement.clientWidth < limit;
    this.classList.toggle("tf-phone", narrow);
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#observer?.disconnect();
  }

  static observedAttributes = ["size", "width", "height", "phone", "phone-width"];

  protected async render(): Promise<void> {
    if (!this.tf) return;
    document.documentElement.dataset.theme = this.tf.theme.mode;
    // Tableau's dashboard size: "automatic" fills the frame; "fixed" keeps the authored width and height
    // (and scrolls). Floating objects are positioned relative to the dashboard either way.
    this.style.position = "relative";
    if (this.getAttribute("size") === "fixed") {
      const width = Number(this.getAttribute("width")) || 1000;
      const height = Number(this.getAttribute("height")) || 800;
      this.style.width = `${width}px`;
      this.style.height = `${height}px`;
      this.style.overflow = "auto";
    } else {
      this.style.removeProperty("width");
      this.style.removeProperty("height");
    }
    this.tf.resize(document.documentElement.scrollHeight);
  }
}

// ---- tf-chart ----------------------------------------------------------------------------------

const MARKS = MARK_NAMES;

export class TfChart extends TfElement {
  static observedAttributes = [
    "query", "mark", "x", "y", "color", "height", "sort", "drill", "size", "target", "bins", "reference", "band", "trend",
  ];
  #selected: Tuple | null = null;
  /** In-place drill (P3.6): the detail query shown after a mark was clicked. */
  #drilled: { query: string; from: Tuple } | null = null;

  #select(query: string, tuple: Tuple | null): void {
    const same = tuple !== null && this.#selected !== null && sameTuple(tuple, this.#selected);
    this.#selected = same ? null : tuple;
    this.tf?.select(query, this.#selected ? [this.#selected] : []);
    const drill = this.getAttribute("drill");
    if (drill && this.#selected && !this.#drilled) {
      // The manifest's filter action from this query to the drill query narrows it to the mark.
      this.#drilled = { query: drill, from: this.#selected };
      void this.update();
    }
  }

  #back(base: string): void {
    this.#drilled = null;
    this.#selected = null;
    this.tf?.select(base, []);
    void this.update();
  }

  protected async render(isCurrent: () => boolean): Promise<void> {
    if (!this.tf) return;
    const base = required(this, "query");
    const query = this.#drilled?.query ?? base;
    const mark = this.getAttribute("mark") ?? "bar";
    if (!MARKS.includes(mark)) throw new ChartError(`unknown mark "${mark}"`);
    const fetched = await this.tf.query(query);
    if (!isCurrent()) return;
    let x = required(this, "x");
    let y = required(this, "y");
    if (this.#drilled) {
      // The detail query has its own dimension (the next level down): put it on the dimension axis.
      const level = fetched.columns.find((c) => c.role !== "measure" && c.name !== "__total")?.name;
      const swap = (name: string) => (fetched.columns.some((c) => c.name === name) || !level ? name : level);
      x = swap(x);
      y = swap(y);
    }
    const result = sortRows(fetched, this.getAttribute("sort"), [x, y]);
    const highlight = this.tf.state.highlight;
    const svg = renderChart(
      {
        mark: mark as Mark,
        x,
        y,
        color: this.getAttribute("color") ?? undefined,
        title: this.getAttribute("title") ?? undefined,
        size: this.getAttribute("size") ?? undefined,
        target: this.getAttribute("target") ?? undefined,
        bins: this.hasAttribute("bins") ? Number(this.getAttribute("bins")) : undefined,
        reference: this.getAttribute("reference") ?? undefined,
        band: this.getAttribute("band") ?? undefined,
        trend: this.getAttribute("trend") ?? undefined,
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
      if (this.#selected && !this.#drilled) this.#select(query, null);
    });
    const parts: (HTMLElement | SVGSVGElement | null)[] = [this.title_()];
    if (this.#drilled) {
      const back = h("button", { type: "button", class: "tf-button tf-back" }, "◀ Back");
      back.addEventListener("click", () => this.#back(base));
      const where = Object.entries(this.#drilled.from)
        .map(([k, v]) => `${k}: ${v === null ? "Null" : String(v)}`)
        .join(", ");
      parts.push(h("div", { class: "tf-drill" }, back, h("span", { class: "tf-note" }, where)));
    }
    parts.push(svg);
    this.replaceChildren(...parts.filter((n): n is HTMLElement | SVGSVGElement => n !== null));
    if (result.truncated) this.append(h("div", { class: "tf-note" }, "Showing the first rows only."));
  }
}

/** Order rows by a measure for `sort="asc|desc"` (the first measure among the named columns). */
function sortRows(result: QueryResult, sort: string | null, names: string[]): QueryResult {
  if (sort !== "asc" && sort !== "desc") return result;
  const index = result.columns.findIndex((c) => names.includes(c.name) && c.role === "measure");
  if (index < 0) return result;
  const rows = [...result.rows].sort((a, b) => {
    const x = a[index] ?? null;
    const y = b[index] ?? null;
    const order = x === y ? 0 : x === null ? 1 : y === null ? -1 : x < y ? -1 : 1;
    return sort === "desc" ? -order : order;
  });
  return { ...result, rows };
}

// ---- tf-crosstab -------------------------------------------------------------------------------

const TOTAL = "__total";

interface Pivoted {
  rowDims: number[];
  measures: number[];
  /** Pivot members across the columns (empty when there is no `columns` attribute). */
  across: Scalar[];
  rows: { key: Scalar[]; cells: Map<string, Scalar[]>; total: string | null; source: Scalar[] }[];
}

function keyOf(values: Scalar[]): string {
  return JSON.stringify(values);
}

/**
 * Lay the result out as a crosstab: row headers for the dimensions, optionally one dimension pivoted
 * across the columns, and total rows (from the query's `totals`) placed after their group.
 */
function pivot(result: QueryResult, across: string | null): Pivoted {
  const totalIdx = result.columns.findIndex((c) => c.name === TOTAL);
  const acrossIdx = across === null ? -1 : result.columns.findIndex((c) => c.name === across);
  if (across !== null && acrossIdx < 0) throw new Error(`the query has no column "${across}"`);
  const rowDims: number[] = [];
  const measures: number[] = [];
  result.columns.forEach((c, i) => {
    if (i === totalIdx || i === acrossIdx) return;
    (c.role === "measure" ? measures : rowDims).push(i);
  });
  const acrossValues: Scalar[] = [];
  const seenAcross = new Set<string>();
  const detail: Pivoted["rows"] = [];
  const totals: Pivoted["rows"] = [];
  const index = new Map<string, Pivoted["rows"][number]>();
  for (const row of result.rows) {
    const total = totalIdx >= 0 ? ((row[totalIdx] ?? null) as string | null) : null;
    const key = rowDims.map((i) => row[i] ?? null);
    const pivotValue = acrossIdx >= 0 ? (row[acrossIdx] ?? null) : null;
    if (acrossIdx >= 0 && !seenAcross.has(keyOf([pivotValue]))) {
      seenAcross.add(keyOf([pivotValue]));
      acrossValues.push(pivotValue);
    }
    const id = keyOf([total, ...key]);
    let entry = index.get(id);
    if (!entry) {
      entry = { key, cells: new Map(), total, source: row };
      index.set(id, entry);
      (total === null ? detail : totals).push(entry);
    }
    entry.cells.set(keyOf([pivotValue]), measures.map((i) => row[i] ?? null));
  }
  // Each subtotal goes after the last row of its group (deeper subtotals first); the grand total last.
  const ordered = [...detail];
  const names = rowDims.map((i) => result.columns[i]?.name);
  const depthOf = (r: Pivoted["rows"][number]) => names.indexOf(r.total ?? "");
  const subtotals = totals.filter((r) => r.total !== "grand").sort((a, b) => depthOf(b) - depthOf(a));
  for (const t of subtotals) {
    const prefix = keyOf(t.key.slice(0, depthOf(t) + 1));
    let at = -1;
    ordered.forEach((r, i) => {
      if (keyOf(r.key.slice(0, depthOf(t) + 1)) === prefix) at = i;
    });
    ordered.splice(at < 0 ? ordered.length : at + 1, 0, t);
  }
  ordered.push(...totals.filter((r) => r.total === "grand"));
  return { rowDims, measures, across: acrossIdx >= 0 ? acrossValues : [null], rows: ordered };
}

export class TfCrosstab extends TfElement {
  static observedAttributes = ["query", "max-rows", "columns"];
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
    const across = this.getAttribute("columns");
    const hasTotals = result.columns.some((c) => c.name === TOTAL);
    const nested = across !== null || hasTotals;
    let source = result;
    const sort = this.#sort;
    if (sort && !nested) {
      const rows = [...result.rows].sort((a, b) => {
        const x = a[sort.column] ?? null;
        const y = b[sort.column] ?? null;
        const order = x === y ? 0 : x === null ? 1 : y === null ? -1 : x < y ? -1 : 1;
        return sort.desc ? -order : order;
      });
      source = { ...result, rows };
    }
    const table = pivot(source, across);
    const col = (i: number) => source.columns[i] as QueryResult["columns"][number];
    const thead = h("thead");
    if (across !== null) {
      // Two header rows: the pivot members, then the measures under each.
      const acrossCol = source.columns.find((c) => c.name === across);
      const top = h("tr", {}, ...table.rowDims.map(() => h("th", { scope: "col" })));
      for (const v of table.across) {
        top.append(
          h("th", { scope: "colgroup", colspan: table.measures.length, class: "tf-num" }, acrossCol ? formatValue(v, acrossCol) : String(v)),
        );
      }
      thead.append(top);
    }
    const header = h("tr");
    table.rowDims.forEach((i) => header.append(this.#th(col(i).name, i, !nested)));
    for (let a = 0; a < table.across.length; a++) {
      table.measures.forEach((i) => header.append(this.#th(col(i).name, i, !nested, true)));
    }
    thead.append(header);
    const body = h("tbody");
    let previous: Scalar[] = [];
    for (const row of table.rows.slice(0, limit)) {
      const tr = h("tr", { class: row.total ? "tf-total" : null });
      table.rowDims.forEach((i, d) => {
        const value = row.key[d] ?? null;
        let text: string;
        const depth = row.total === null ? -1 : table.rowDims.findIndex((j) => col(j).name === row.total);
        if (row.total === "grand") text = d === 0 ? "Grand Total" : "";
        else if (row.total !== null) text = d === depth + 1 ? "Total" : "";
        // Nested headers: repeat a dimension value only when it changes (Tableau's merged cells).
        else {
          const repeated = nested && previous.length > d && previous.slice(0, d + 1).every((p, j) => p === row.key[j]);
          text = repeated ? "" : formatValue(value, col(i));
        }
        tr.append(h("th", { scope: "row", class: "tf-rowhead" }, text));
      });
      for (const a of table.across) {
        const cells = row.cells.get(keyOf([a]));
        table.measures.forEach((i, m) => tr.append(h("td", { class: "tf-num" }, cells ? formatValue(cells[m] ?? null, col(i)) : "")));
      }
      if (row.total === null) {
        previous = row.key;
        tr.addEventListener("click", () => this.tf?.select(query, [dimensionTuple(source, row.source)]));
      }
      body.append(tr);
    }
    const notes = [];
    if (table.rows.length > limit) notes.push(h("div", { class: "tf-note" }, `Showing ${limit} of ${table.rows.length} rows.`));
    if (result.truncated) notes.push(h("div", { class: "tf-note" }, "The query returned the first rows only."));
    const grid = h("table", { class: "tf-table" }, thead, body);
    this.replaceChildren(...[this.title_(), h("div", { class: "tf-scroll" }, grid), ...notes].filter((n) => n !== null));
  }

  #th(name: string, index: number, sortable: boolean, numeric = false): HTMLElement {
    const th = h("th", { scope: "col", class: numeric ? "tf-num" : null }, name);
    if (!sortable) return th;
    const sort = this.#sort;
    if (sort?.column === index) th.setAttribute("aria-sort", sort.desc ? "descending" : "ascending");
    th.addEventListener("click", () => {
      this.#sort = { column: index, desc: sort?.column === index ? !sort.desc : numeric };
      this.#draw();
    });
    return th;
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
      const show = column ? (v: Scalar) => formatValue(v, column) : String;
      // Tableau's quick-filter styles: single value (dropdown, list, slider), multiple values
      // (list, dropdown) and type-in (search).
      switch (mode) {
        case "checklist":
          control = this.#checklist(id, name, members, selected, show);
          break;
        case "radio":
          control = this.#radio(id, name, members, selected, show);
          break;
        case "multi-dropdown":
          control = this.#multiDropdown(id, name, members, selected, show);
          break;
        case "slider":
          control = this.#memberSlider(id, name, members, selected, show);
          break;
        case "search":
          control = this.#search(id, name, members, selected, show, spec.multi);
          break;
        default:
          control = this.#dropdown(id, name, members, selected, show);
      }
    } else if (spec.kind === "relative_date") {
      control = this.#relative(id, name, spec, current);
    } else if (spec.kind === "range") {
      control = this.#range(id, name, current, this.getAttribute("control") === "date" ? "date" : "number");
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

  #radio(id: string, name: string, members: Scalar[], selected: Scalar[] | null, show: (v: Scalar) => string): HTMLElement {
    const group = h("div", { id, class: "tf-checklist", role: "radiogroup" });
    const choice = (label: string, checked: boolean, value: Scalar[] | null) => {
      const input = h("input", { type: "radio", name: id, checked });
      input.addEventListener("change", () => this.tf?.setFilter(name, value === null ? null : { members: value }));
      group.append(h("label", { class: "tf-check" }, input, label));
    };
    choice("(All)", selected === null, null);
    for (const m of members) choice(show(m), selected?.length === 1 && selected[0] === m, [m]);
    return group;
  }

  #multiDropdown(
    id: string,
    name: string,
    members: Scalar[],
    selected: Scalar[] | null,
    show: (v: Scalar) => string,
  ): HTMLElement {
    const select = h("select", { id, class: "tf-input", multiple: true, size: Math.min(8, Math.max(3, members.length)) });
    members.forEach((m, i) => select.append(h("option", { value: String(i), selected: selected?.includes(m) ?? false }, show(m))));
    select.addEventListener("change", () => {
      const chosen = Array.from(select.selectedOptions).map((o) => members[Number(o.value)] ?? null);
      this.tf?.setFilter(name, chosen.length === 0 || chosen.length === members.length ? null : { members: chosen });
    });
    return select;
  }

  #memberSlider(
    id: string,
    name: string,
    members: Scalar[],
    selected: Scalar[] | null,
    show: (v: Scalar) => string,
  ): HTMLElement {
    // A single-value slider over the members in domain order, with an "All" position at the left.
    const at = selected?.length === 1 ? members.indexOf(selected[0] ?? null) + 1 : 0;
    const slider = h("input", { id, type: "range", min: 0, max: members.length, step: 1, value: String(Math.max(0, at)) });
    const output = h("output", { for: id, class: "tf-output" }, at > 0 ? show(members[at - 1] ?? null) : "(All)");
    slider.addEventListener("input", () => {
      const i = Number(slider.value);
      output.textContent = i > 0 ? show(members[i - 1] ?? null) : "(All)";
    });
    slider.addEventListener("change", () => {
      const i = Number(slider.value);
      this.tf?.setFilter(name, i > 0 ? { members: [members[i - 1] ?? null] } : null);
    });
    return h("div", { class: "tf-slider" }, slider, output);
  }

  #search(
    id: string,
    name: string,
    members: Scalar[],
    selected: Scalar[] | null,
    show: (v: Scalar) => string,
    multi: boolean,
  ): HTMLElement {
    // Type-in: pick members by typing; with a multi-value filter each match is added as a chip.
    const listId = `${id}-members`;
    const datalist = h("datalist", { id: listId }, ...members.map((m) => h("option", { value: show(m) })));
    const input = h("input", { id, type: "search", list: listId, class: "tf-input", placeholder: "Type a value…" });
    const chosen: Scalar[] = [...(selected ?? [])];
    const chips = h("div", { class: "tf-chips" });
    const redraw = () => {
      chips.replaceChildren(
        ...chosen.map((m) => {
          const chip = h("button", { type: "button", class: "tf-chip", "aria-label": `Remove ${show(m)}` }, `${show(m)} ✕`);
          chip.addEventListener("click", () => {
            chosen.splice(chosen.indexOf(m), 1);
            this.tf?.setFilter(name, chosen.length ? { members: [...chosen] } : null);
          });
          return chip;
        }),
      );
    };
    redraw();
    input.addEventListener("change", () => {
      const match = members.find((m) => show(m).toLowerCase() === input.value.trim().toLowerCase());
      if (match === undefined) return;
      if (multi) {
        if (!chosen.includes(match)) chosen.push(match);
      } else {
        chosen.splice(0, chosen.length, match);
      }
      input.value = "";
      this.tf?.setFilter(name, { members: [...chosen] });
    });
    return h("div", { class: "tf-search" }, input, datalist, chips);
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

  #range(id: string, name: string, current: FilterValue | null, type: "number" | "date"): HTMLElement {
    // "Range of values" or "Range of dates" (control="date").
    const min = h("input", { id, type, class: "tf-input", placeholder: "Min", "aria-label": "From" });
    const max = h("input", { type, class: "tf-input", placeholder: "Max", "aria-label": "To" });
    const shown = (v: Scalar | undefined) => (v === undefined || v === null ? "" : String(v).slice(0, type === "date" ? 10 : undefined));
    if (current && "min" in current) min.value = shown(current.min);
    if (current && "max" in current) max.value = shown(current.max);
    const commit = () => {
      const value: { min?: Scalar; max?: Scalar } = {};
      const read = (input: HTMLInputElement) => (type === "date" ? input.value : Number(input.value));
      if (min.value !== "") value.min = read(min);
      if (max.value !== "") value.max = read(max);
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

// ---- dynamic zones, show/hide and navigation (P6.11) ---------------------------------------------------

/**
 * `<tf-when parameter="show_details" equals="true">…</tf-when>`: its children show only while the
 * parameter has that value (Tableau's dynamic zone visibility). Without `equals`, any truthy value.
 */
export class TfWhen extends TfElement {
  static observedAttributes = ["parameter", "equals"];

  protected async render(): Promise<void> {
    if (!this.tf) return;
    const name = required(this, "parameter");
    const value = this.tf.state.parameters[name];
    const equals = this.getAttribute("equals");
    const visible = equals === null ? Boolean(value) && value !== "false" : String(value) === equals;
    this.hidden = !visible;
    this.tf.resize(document.documentElement.scrollHeight);
  }

  protected override showError(error: unknown): void {
    this.hidden = false;
    super.showError(error);
  }
}

/** `<tf-toggle target="element-id" label="Filters">`: a show/hide button for another element. */
export class TfToggle extends HTMLElement {
  connectedCallback(): void {
    const target = this.getAttribute("target") ?? "";
    const label = this.getAttribute("label") ?? "Show/hide";
    const button = h("button", { type: "button", class: "tf-button", "aria-controls": target }, label);
    const sync = () => {
      const el = document.getElementById(target);
      button.setAttribute("aria-expanded", String(Boolean(el && !el.hidden)));
    };
    button.addEventListener("click", () => {
      const el = document.getElementById(target);
      if (!el) return;
      el.hidden = !el.hidden;
      sync();
      void client().then((c) => c.resize(document.documentElement.scrollHeight));
    });
    if (this.getAttribute("start") === "hidden") {
      const el = document.getElementById(target);
      if (el) el.hidden = true;
    }
    this.replaceChildren(button);
    sync();
  }
}

/** `<tf-nav dashboard="other-id" label="Details">`: a navigation button (the host checks access). */
export class TfNav extends HTMLElement {
  connectedCallback(): void {
    const dashboard = this.getAttribute("dashboard") ?? "";
    const button = h("button", { type: "button", class: "tf-button" }, this.getAttribute("label") ?? "Open");
    button.addEventListener("click", () => void client().then((c) => c.navigate(dashboard)));
    this.replaceChildren(button);
  }
}

export const ELEMENTS: Record<string, CustomElementConstructor> = {
  "tf-when": TfWhen,
  "tf-toggle": TfToggle,
  "tf-nav": TfNav,
  "tf-dashboard": TfDashboard,
  "tf-chart": TfChart,
  "tf-crosstab": TfCrosstab,
  "tf-kpi": TfKpi,
  "tf-map": TfMap,
  "tf-filter": TfFilter,
  "tf-parameter": TfParameter,
};

export function defineElements(registry: CustomElementRegistry = customElements): void {
  for (const [name, ctor] of Object.entries({ ...ELEMENTS, ...LAYOUT_ELEMENTS })) {
    if (!registry.get(name)) registry.define(name, ctor);
  }
}
