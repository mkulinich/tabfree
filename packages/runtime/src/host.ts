/**
 * The host side of the dashboard sandbox: runs in the TabFree portal, never inside a dashboard.
 *
 * `mountDashboard` creates the sandboxed iframe, authenticates the dashboard's hello by
 * `event.source`, moves all traffic onto a private MessagePort, and enforces the manifest: only
 * declared queries, filters and parameters are accepted, and selections only trigger declared
 * actions. Data comes from `runQuery`, which calls the governed query API; the server, not this
 * code, applies permissions and row-level security.
 */

import {
  PROTOCOL_VERSION,
  isFilterValue,
  isScalar,
  parseDashboardMessage,
  type Column,
  type DashboardMessage,
  type DashboardState,
  type ErrorCode,
  type ExportFormat,
  type FilterSpec,
  type FilterValue,
  type HostMessage,
  type ParameterSpec,
  type ParameterType,
  type Scalar,
  type Theme,
  type Tuple,
} from "./protocol.js";
import {
  applyTableCalcs,
  type Aggregation,
  type TableCalc,
  type TableCalcFilter,
  type TableCalcOptions,
} from "./tablecalc.js";

// ---- The parts of tabfree.json the host reads ------------------------------------------------

export interface ManifestFilter {
  label?: string;
  field?: string;
  expr?: string;
  kind: FilterSpec["kind"];
  multi?: boolean;
  appliesTo: "all" | string[];
  default?: FilterValue;
}

export interface ManifestParameter {
  label?: string;
  type: ParameterType;
  default: Scalar;
  allowed?: "any" | { list: Scalar[] } | { range: { min?: number | string; max?: number | string; step?: number } };
}

export interface ManifestAction {
  type: string;
  source: "all" | string[];
  targets?: "all" | string[];
  fields?: (string | { source: string; target: string })[];
  clear?: "keep_filter" | "show_all" | "exclude_all";
  /** Parameter actions: the parameter to set from the selected mark's ``field``. */
  parameter?: string;
  field?: string;
  /** URL actions: a template with <Field Name> placeholders. */
  url?: string;
  /** Set actions: the model set, the output whose values become members, and how. */
  set?: string;
  mode?: "assign" | "add" | "remove";
  openIn?: "new_tab" | "same_tab";
}

/**
 * A set action's new members (P6.11): undefined leaves the set alone, null restores the model's own
 * definition. Members come from the action's ``field`` (or the selection's only field). On an empty
 * selection, ``clear`` decides: keep, remove_all (empty set) or add_all (the model's definition).
 */
export function setMembers(action: ManifestAction, current: Scalar[] | undefined, tuples: Tuple[]): Scalar[] | null | undefined {
  if (!tuples.length) {
    const clear = (action as { clear?: string }).clear ?? "keep";
    return clear === "remove_all" ? [] : clear === "add_all" ? null : undefined;
  }
  const values: Scalar[] = [];
  for (const tuple of tuples) {
    const keys = Object.keys(tuple);
    const name = action.field ?? (keys.length === 1 ? keys[0] : undefined);
    if (name === undefined || !(name in tuple)) return undefined;
    const value = tuple[name] ?? null;
    if (!values.includes(value)) values.push(value);
  }
  const mode = action.mode ?? "assign";
  if (mode === "assign") return values;
  const base = current ?? [];
  if (mode === "add") return [...base, ...values.filter((v) => !base.includes(v))];
  return base.filter((v) => !values.includes(v));
}

/** A URL action's link for a selected mark; null when the template is not http(s) (P6.11). */
export function actionUrl(template: string, tuple: Tuple): string | null {
  const url = template.replace(/<([^<>]+)>/g, (_m, name: string) =>
    encodeURIComponent(tuple[name] === null || tuple[name] === undefined ? "" : String(tuple[name])),
  );
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** The parts of a manifest query the host needs for table calculations (computed in the browser). */
export interface ManifestQuery {
  measures?: (string | { field?: string; agg?: string; expr?: string; as?: string })[];
  tableCalcs?: TableCalc[];
  layout?: { across?: string[] };
  densify?: boolean | string[];
  dropDensified?: boolean;
  tableCalcFilters?: TableCalcFilter[];
}

const TOTAL_AGGREGATIONS: readonly string[] = ["sum", "avg", "min", "max", "count", "attr"];

/** Engine options for a query: layout, densification, filters and how TOTAL() re-aggregates. */
export function tableCalcOptions(query: ManifestQuery): TableCalcOptions {
  const aggregations: Record<string, Aggregation> = {};
  for (const m of query.measures ?? []) {
    if (typeof m !== "string" && m.agg && TOTAL_AGGREGATIONS.includes(m.agg)) {
      aggregations[m.as ?? m.field ?? ""] = m.agg as Aggregation;
    }
  }
  const options: TableCalcOptions = { aggregations };
  return {
    ...options,
    ...(query.layout?.across ? { across: query.layout.across } : {}),
    ...(query.densify !== undefined ? { densify: query.densify } : {}),
    ...(query.dropDensified !== undefined ? { dropDensified: query.dropDensified } : {}),
    ...(query.tableCalcFilters ? { filters: query.tableCalcFilters } : {}),
  };
}

export type { DashboardState, ExportFormat, Theme } from "./protocol.js";

export interface Manifest {
  id: string;
  title: string;
  filters?: Record<string, ManifestFilter>;
  parameters?: Record<string, ManifestParameter>;
  queries: Record<string, ManifestQuery>;
  actions?: ManifestAction[];
}

/** What the host asks the governed query API for. The server maps it onto the manifest. */
export interface QueryRequest {
  dashboard: string;
  query: string;
  state: DashboardState;
  /** Current selections by source query, for the manifest's filter actions. */
  selections: Record<string, Tuple[]>;
}

export interface DomainRequest {
  dashboard: string;
  filter: string;
  state: DashboardState;
}

export interface QueryResponse {
  columns: Column[];
  rows: Scalar[][];
  truncated: boolean;
}

export class HostQueryError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message);
  }
}

export interface MountOptions {
  container: HTMLElement;
  manifest: Manifest;
  /** The bundle's index.html. */
  html: string;
  /** Source of the frame runtime (dist/bundle/frame.js), inlined because the frame has no network. */
  runtime: string;
  runQuery: (request: QueryRequest) => Promise<QueryResponse>;
  runDomain: (request: DomainRequest) => Promise<QueryResponse>;
  theme?: Theme;
  initialState?: Partial<DashboardState>;
  onStateChange?: (state: DashboardState, selections: Record<string, Tuple[]>) => void;
  onDashboardError?: (message: string) => void;
  /** The dashboard asked for an export; the portal checks capabilities and downloads it. */
  onExport?: (request: ExportRequest) => void;
  /** A navigation button asked for another dashboard; the portal checks the viewer may open it. */
  onNavigate?: (dashboard: string) => void;
  /** A URL action fired (http and https only); the portal opens it, without an opener. */
  onOpenUrl?: (url: string, target: "new_tab" | "same_tab") => void;
  /**
   * Load the dashboard from this URL on the separate usercontent origin (P3.6) instead of an inline
   * srcdoc. The server sends the bundle with its own CSP there, so the portal page can keep a strict
   * policy of its own; ``html`` and ``runtime`` are then unused.
   */
  frameUrl?: string;
}

export interface ExportRequest {
  dashboard: string;
  format: ExportFormat;
  query?: string;
  state: DashboardState;
  selections: Record<string, Tuple[]>;
}

export interface DashboardHost {
  readonly iframe: HTMLIFrameElement;
  readonly state: DashboardState;
  setTheme(theme: Theme): void;
  setState(state: Partial<DashboardState>): void;
  destroy(): void;
}

/** CSP for the frame: inline runtime and bundle code only, no network of any kind. */
export const FRAME_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; " +
  "font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'";

/** Index just after the first `<head>` or `<head ...>` tag, or -1. Linear: no backtracking regex. */
function headEnd(html: string): number {
  const lower = html.toLowerCase();
  for (let i = lower.indexOf("<head"); i >= 0; i = lower.indexOf("<head", i + 5)) {
    const next = lower.charAt(i + 5);
    if (next === ">" || /\s/.test(next)) {
      const close = lower.indexOf(">", i + 5);
      return close < 0 ? -1 : close + 1;
    }
  }
  return -1;
}

/** The frame document: the bundle's HTML with the CSP and the runtime injected first in <head>. */
export function buildSrcdoc(html: string, runtime: string): string {
  const csp = `<meta http-equiv="Content-Security-Policy" content="${FRAME_CSP}">`;
  const script = `<script>${runtime.replace(/<\/(script)/gi, "<\\/$1")}</script>`;
  const at = headEnd(html);
  if (at >= 0) return `${html.slice(0, at)}${csp}${script}${html.slice(at)}`;
  return `<!doctype html><html><head>${csp}${script}</head><body>${html}</body></html>`;
}

export function filterSpecs(manifest: Manifest): Record<string, FilterSpec> {
  const out: Record<string, FilterSpec> = {};
  for (const [name, f] of Object.entries(manifest.filters ?? {})) {
    out[name] = {
      label: f.label ?? f.field ?? name,
      kind: f.kind,
      multi: f.multi ?? f.kind === "categorical",
      default: f.default ?? null,
    };
  }
  return out;
}

export function parameterSpecs(manifest: Manifest): Record<string, ParameterSpec> {
  const out: Record<string, ParameterSpec> = {};
  for (const [name, p] of Object.entries(manifest.parameters ?? {})) {
    const spec: ParameterSpec = { label: p.label ?? name, type: p.type, default: p.default };
    const allowed = p.allowed;
    if (allowed && allowed !== "any" && "list" in allowed) spec.list = allowed.list;
    if (allowed && allowed !== "any" && "range" in allowed) {
      const { min, max, step } = allowed.range;
      if (typeof min === "number") spec.min = min;
      if (typeof max === "number") spec.max = max;
      if (typeof step === "number") spec.step = step;
    }
    out[name] = spec;
  }
  return out;
}

export function initialState(manifest: Manifest, overrides: Partial<DashboardState> = {}): DashboardState {
  const filters: Record<string, FilterValue> = {};
  for (const [name, f] of Object.entries(manifest.filters ?? {})) {
    if (f.default) filters[name] = f.default;
  }
  const parameters: Record<string, Scalar> = {};
  for (const [name, p] of Object.entries(manifest.parameters ?? {})) parameters[name] = p.default;
  return {
    filters: { ...filters, ...overrides.filters },
    parameters: { ...parameters, ...overrides.parameters },
    highlight: overrides.highlight ?? null,
  };
}

/** Coerce a parameter value to its declared type and allowed values; null when not allowed. */
export function coerceParameter(spec: ParameterSpec, value: Scalar): Scalar | undefined {
  let v: Scalar = value;
  if (spec.type === "integer" || spec.type === "float") {
    const n = typeof value === "string" ? Number(value) : value;
    if (typeof n !== "number" || !Number.isFinite(n)) return undefined;
    v = spec.type === "integer" ? Math.round(n) : n;
    if (spec.min !== undefined && v < spec.min) return undefined;
    if (spec.max !== undefined && v > spec.max) return undefined;
  } else if (spec.type === "boolean") {
    if (typeof value !== "boolean") return undefined;
  } else if (typeof value !== "string") {
    return undefined;
  }
  if (spec.list && !spec.list.includes(v)) return undefined;
  return v;
}

function kindAccepts(kind: FilterSpec["kind"], value: FilterValue): boolean {
  if (kind === "categorical") return "members" in value;
  if (kind === "relative_date") return "relative" in value || "min" in value || "max" in value;
  if (kind === "wildcard") return "wildcard" in value;
  return "min" in value || "max" in value;
}

function inList(list: "all" | string[] | undefined, name: string): boolean {
  return list === "all" || (list ?? []).includes(name);
}

function sameTuple(a: Tuple, b: Tuple): boolean {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((k) => a[k] === b[k]);
}

export function mountDashboard(options: MountOptions): DashboardHost {
  const { manifest } = options;
  const filters = filterSpecs(manifest);
  const parameters = parameterSpecs(manifest);
  let state = initialState(manifest, options.initialState);
  let theme: Theme = options.theme ?? { mode: "light" };
  let selections: Record<string, Tuple[]> = {};
  let port: MessagePort | null = null;

  const iframe = document.createElement("iframe");
  // allow-scripts only: no allow-same-origin, so the frame's origin is opaque and it has no
  // cookies, storage or access to this page.
  iframe.setAttribute("sandbox", "allow-scripts");
  iframe.setAttribute("referrerpolicy", "no-referrer");
  iframe.setAttribute("allow", "");
  iframe.setAttribute("title", manifest.title);
  iframe.style.cssText = "width:100%;border:0;display:block;min-height:200px";
  const load = () => {
    if (options.frameUrl) iframe.src = options.frameUrl;
    else iframe.srcdoc = buildSrcdoc(options.html, options.runtime);
  };
  load();

  const send = (message: HostMessage) => port?.postMessage(message);
  const broadcast = () => {
    send({ v: 0, type: "state", state });
    options.onStateChange?.(state, selections);
  };

  const onWindowMessage = (event: MessageEvent) => {
    // The frame's origin is "null", so the sender is identified by its window object.
    if (event.source !== iframe.contentWindow || iframe.contentWindow === null) return;
    const message = parseDashboardMessage(event.data);
    if (message?.type !== "hello") return;
    port?.close();
    const channel = new MessageChannel();
    port = channel.port1;
    port.onmessage = (e: MessageEvent) => void handle(parseDashboardMessage(e.data));
    iframe.contentWindow.postMessage({ v: PROTOCOL_VERSION, type: "connect" }, "*", [channel.port2]);
    send({
      v: 0,
      type: "init",
      dashboard: { id: manifest.id, title: manifest.title },
      state,
      theme,
      filters,
      parameters,
    });
  };

  const fail = (id: string, code: ErrorCode, message: string) => send({ v: 0, type: "error", id, code, message });

  async function handle(message: DashboardMessage | null): Promise<void> {
    if (message === null) return;
    switch (message.type) {
      case "query": {
        const query = manifest.queries[message.query];
        if (!query) return fail(message.id, "not_found", `no query named "${message.query}"`);
        try {
          // Viz in tooltip: the hovered mark counts as selected for this one request only. The server
          // applies only the manifest's declared filter actions, so a hover narrows nothing undeclared.
          const requestSelections = message.hover
            ? { ...selections, [message.hover.source]: message.hover.tuples }
            : selections;
          const response = await options.runQuery({
            dashboard: manifest.id,
            query: message.query,
            state,
            selections: requestSelections,
          });
          const calcs = query.tableCalcs ?? [];
          const needed = calcs.length > 0 || (query.tableCalcFilters ?? []).length > 0 || query.densify !== undefined;
          const table = needed ? applyTableCalcs(response, calcs, tableCalcOptions(query)) : response;
          send({ v: 0, type: "result", id: message.id, query: message.query, ...table, truncated: response.truncated });
        } catch (error) {
          const code = error instanceof HostQueryError ? error.code : "query_failed";
          fail(message.id, code, error instanceof Error ? error.message : "query failed");
        }
        return;
      }
      case "domain": {
        if (!filters[message.filter]) return fail(message.id, "not_found", `no filter named "${message.filter}"`);
        try {
          const response = await options.runDomain({ dashboard: manifest.id, filter: message.filter, state });
          send({ v: 0, type: "result", id: message.id, query: message.filter, ...response });
        } catch (error) {
          const code = error instanceof HostQueryError ? error.code : "query_failed";
          fail(message.id, code, error instanceof Error ? error.message : "domain query failed");
        }
        return;
      }
      case "setFilter": {
        const spec = filters[message.filter];
        if (!spec) return;
        const others = Object.entries(state.filters).filter(([k]) => k !== message.filter);
        const next: Record<string, FilterValue> = Object.fromEntries(others);
        if (message.value !== null) {
          if (!isFilterValue(message.value) || !kindAccepts(spec.kind, message.value)) return;
          next[message.filter] = message.value;
        }
        state = { ...state, filters: next };
        broadcast();
        return;
      }
      case "setParameter": {
        const spec = parameters[message.parameter];
        if (!spec || !isScalar(message.value)) return;
        const value = coerceParameter(spec, message.value);
        if (value === undefined) return;
        state = { ...state, parameters: { ...state.parameters, [message.parameter]: value } };
        broadcast();
        return;
      }
      case "select": {
        const actions = (manifest.actions ?? []).filter((a) => inList(a.source, message.source));
        if (!actions.length) return;
        let changed = false;
        for (const action of actions) {
          if (action.type === "filter") {
            // "Keep filtered values" (Tableau): clearing the selection leaves the last filter in place.
            if (!message.tuples.length && action.clear === "keep_filter") continue;
            const current = selections[message.source] ?? [];
            const same =
              current.length === message.tuples.length && current.every((t, i) => sameTuple(t, message.tuples[i] ?? {}));
            if (same) continue;
            selections = Object.fromEntries(Object.entries(selections).filter(([k]) => k !== message.source));
            if (message.tuples.length) selections[message.source] = message.tuples;
            changed = true;
          } else if (action.type === "highlight") {
            state = { ...state, highlight: message.tuples.length ? { source: message.source, tuples: message.tuples } : null };
            changed = true;
          } else if (action.type === "parameter" && action.parameter && action.field) {
            // Parameter action (P6.11): the selected mark's value becomes the parameter's value.
            const spec = parameters[action.parameter];
            const first = message.tuples[0];
            if (!spec || !first || !(action.field in first)) continue;
            const value = coerceParameter(spec, first[action.field] ?? null);
            if (value === undefined || state.parameters[action.parameter] === value) continue;
            state = { ...state, parameters: { ...state.parameters, [action.parameter]: value } };
            changed = true;
          } else if (action.type === "set" && action.set) {
            const next = setMembers(action, state.sets?.[action.set], message.tuples);
            if (next === undefined) continue;
            const name = action.set;
            const sets = Object.fromEntries(Object.entries(state.sets ?? {}).filter(([k]) => k !== name));
            if (next !== null) sets[name] = next;
            state = { ...state, sets };
            changed = true;
          } else if (action.type === "url" && action.url && message.tuples[0]) {
            const url = actionUrl(action.url, message.tuples[0]);
            if (url) options.onOpenUrl?.(url, action.openIn ?? "new_tab");
          }
        }
        if (changed) broadcast();
        return;
      }
      case "navigate":
        options.onNavigate?.(message.dashboard);
        return;
      case "resize":
        iframe.style.height = `${Math.max(200, message.height)}px`;
        return;
      case "error":
        options.onDashboardError?.(message.message);
        return;
      case "export": {
        if (message.query !== undefined && !manifest.queries[message.query]) return;
        const request: ExportRequest = { dashboard: manifest.id, format: message.format, state, selections };
        if (message.query !== undefined) request.query = message.query;
        options.onExport?.(request);
        return;
      }
      default:
        return;
    }
  }

  window.addEventListener("message", onWindowMessage);
  options.container.append(iframe);

  return {
    iframe,
    get state() {
      return state;
    },
    setTheme(next: Theme) {
      theme = next;
      // The theme travels with init; a running frame picks it up on reload.
      load();
    },
    setState(partial: Partial<DashboardState>) {
      state = { ...state, ...partial };
      broadcast();
    },
    destroy() {
      window.removeEventListener("message", onWindowMessage);
      port?.close();
      iframe.remove();
    },
  };
}
