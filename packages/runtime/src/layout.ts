/**
 * Layout and content components (P3.6), Tableau's dashboard objects:
 *
 * - `<tf-container direction="horizontal|vertical">`: a tiled container; children share the space by
 *   their `weight` attribute (default 1), like Tableau's horizontal and vertical layout containers.
 * - `<tf-float x y width height>`: a floating object, positioned in px within `<tf-dashboard>`.
 * - `<tf-legend query field>`: the colour legend of a chart on the same query; clicking a member
 *   selects it (the manifest's highlight or filter actions then apply).
 * - `<tf-text>`: a text object. With `query`, `{Column}` placeholders in its text are filled from the
 *   first row (formatted, always as text).
 * - `<tf-image src alt fit>`: an image object; `src` must be a `data:image/...` URL (the frame has no
 *   network).
 * - `<tf-export format query label>`: a download button; the host checks the viewer's capabilities.
 *
 * None of these use innerHTML: authored text stays as authored, data arrives as text nodes.
 */

import { PALETTE_SIZE, seriesOf } from "./chart.js";
import { h } from "./dom.js";
import { formatValue } from "./format.js";
import type { ExportFormat, Scalar } from "./protocol.js";
import { client, type TabfreeClient } from "./sdk.js";

function number(el: Element, name: string): number | null {
  const raw = el.getAttribute(name);
  if (raw === null || raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

// ---- layout (no data) -----------------------------------------------------------------------------

export class TfContainer extends HTMLElement {
  static observedAttributes = ["direction", "gap"];

  connectedCallback(): void {
    this.#apply();
  }

  attributeChangedCallback(): void {
    this.#apply();
  }

  #apply(): void {
    const vertical = this.getAttribute("direction") === "vertical";
    this.style.display = "flex";
    this.style.flexDirection = vertical ? "column" : "row";
    this.style.gap = `${number(this, "gap") ?? 12}px`;
    this.style.minWidth = "0";
    for (const child of Array.from(this.children)) {
      if (!(child instanceof HTMLElement)) continue;
      const weight = number(child, "weight") ?? 1;
      child.style.flex = `${Math.max(0, weight)} 1 0`;
      child.style.minWidth = "0";
    }
  }
}

export class TfFloat extends HTMLElement {
  static observedAttributes = ["x", "y", "width", "height"];

  connectedCallback(): void {
    this.#apply();
  }

  attributeChangedCallback(): void {
    this.#apply();
  }

  #apply(): void {
    this.style.position = "absolute";
    this.style.left = `${number(this, "x") ?? 0}px`;
    this.style.top = `${number(this, "y") ?? 0}px`;
    const width = number(this, "width");
    const height = number(this, "height");
    if (width !== null) this.style.width = `${width}px`;
    if (height !== null) this.style.height = `${height}px`;
    this.style.zIndex = "1";
  }
}

// ---- data-bound objects -----------------------------------------------------------------------------

abstract class Bound extends HTMLElement {
  protected tf: TabfreeClient | null = null;
  #generation = 0;
  readonly #onState = () => void this.refresh();

  connectedCallback(): void {
    client()
      .then((c) => {
        this.tf = c;
        c.addEventListener("state", this.#onState);
        void this.refresh();
      })
      .catch(() => undefined);
  }

  disconnectedCallback(): void {
    this.tf?.removeEventListener("state", this.#onState);
  }

  attributeChangedCallback(): void {
    if (this.tf) void this.refresh();
  }

  protected async refresh(): Promise<void> {
    const generation = ++this.#generation;
    try {
      await this.draw(() => generation === this.#generation);
    } catch (error) {
      if (generation !== this.#generation) return;
      const message = error instanceof Error ? error.message : "Something went wrong.";
      this.replaceChildren(h("div", { class: "tf-error", role: "alert" }, message));
      this.tf?.reportError(`<${this.localName}>: ${message}`);
    }
  }

  protected abstract draw(isCurrent: () => boolean): Promise<void>;
}

export class TfLegend extends Bound {
  static observedAttributes = ["query", "field", "title"];
  #selected: Scalar | undefined;

  protected async draw(isCurrent: () => boolean): Promise<void> {
    const tf = this.tf;
    if (!tf) return;
    const query = this.getAttribute("query");
    const field = this.getAttribute("field");
    if (!query || !field) throw new Error('<tf-legend> needs "query" and "field"');
    const result = await tf.query(query);
    if (!isCurrent()) return;
    const column = result.columns.find((c) => c.name === field);
    if (!column) throw new Error(`the query has no column "${field}"`);
    const highlighted = tf.state.highlight?.tuples.map((t) => t[field]) ?? null;
    const list = h("ul", { class: "tf-legend-list", role: "list" });
    seriesOf(result, field).forEach((value, i) => {
      const dim = highlighted !== null && !highlighted.includes(value);
      const button = h(
        "button",
        { type: "button", class: `tf-legend-item${dim ? " tf-dim" : ""}`, "aria-pressed": this.#selected === value },
        h("span", { class: `tf-swatch tf-bg-c${i % PALETTE_SIZE}`, "aria-hidden": "true" }),
        formatValue(value, column),
      );
      button.addEventListener("click", () => {
        this.#selected = this.#selected === value ? undefined : value;
        tf.select(query, this.#selected === undefined ? [] : [{ [field]: value }]);
      });
      list.append(h("li", {}, button));
    });
    const title = this.getAttribute("title") ?? field;
    this.replaceChildren(h("div", { class: "tf-title" }, title), list);
  }
}

const PLACEHOLDER = /\{([^{}]{1,256})\}/g;

export class TfText extends Bound {
  static observedAttributes = ["query"];
  #template: string | null = null;

  override connectedCallback(): void {
    // The authored text is the template; it is read once, before any data replaces it.
    this.#template ??= this.textContent ?? "";
    if (this.getAttribute("query")) super.connectedCallback();
  }

  protected async draw(isCurrent: () => boolean): Promise<void> {
    const query = this.getAttribute("query");
    if (!this.tf || !query || this.#template === null) return;
    const result = await this.tf.query(query);
    if (!isCurrent()) return;
    const row = result.rows[0] ?? [];
    const text = this.#template.replace(PLACEHOLDER, (whole, name: string) => {
      const i = result.columns.findIndex((c) => c.name === name.trim());
      const column = result.columns[i];
      return i < 0 || !column ? whole : formatValue(row[i] ?? null, column);
    });
    this.textContent = text;
  }
}

const DATA_IMAGE = /^data:image\/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$/;

export class TfImage extends HTMLElement {
  static observedAttributes = ["src", "alt", "fit"];

  connectedCallback(): void {
    this.#draw();
  }

  attributeChangedCallback(): void {
    this.#draw();
  }

  #draw(): void {
    const src = this.getAttribute("src") ?? "";
    if (!DATA_IMAGE.test(src)) {
      this.replaceChildren(h("div", { class: "tf-error", role: "alert" }, "Images must be data:image/... URLs."));
      return;
    }
    const fit = this.getAttribute("fit");
    const img = h("img", { src, alt: this.getAttribute("alt") ?? "", class: "tf-image" });
    img.style.objectFit = fit === "fill" || fit === "cover" ? fit : "contain";
    this.replaceChildren(img);
  }
}

const EXPORT_LABELS: Record<ExportFormat, string> = {
  csv: "Download data",
  crosstab: "Download crosstab",
  png: "Download image",
  pdf: "Download PDF",
};

export class TfExport extends HTMLElement {
  static observedAttributes = ["format", "query", "label"];

  connectedCallback(): void {
    this.#draw();
  }

  attributeChangedCallback(): void {
    this.#draw();
  }

  #draw(): void {
    const raw = this.getAttribute("format") ?? "csv";
    const format: ExportFormat = raw === "crosstab" || raw === "png" || raw === "pdf" ? raw : "csv";
    const query = this.getAttribute("query") ?? undefined;
    const button = h("button", { type: "button", class: "tf-button" }, this.getAttribute("label") ?? EXPORT_LABELS[format]);
    button.addEventListener("click", () => {
      void client().then((c) => c.export(format, query));
    });
    this.replaceChildren(button);
  }
}

export const LAYOUT_ELEMENTS: Record<string, CustomElementConstructor> = {
  "tf-container": TfContainer,
  "tf-float": TfFloat,
  "tf-legend": TfLegend,
  "tf-text": TfText,
  "tf-image": TfImage,
  "tf-export": TfExport,
};
