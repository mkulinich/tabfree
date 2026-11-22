/**
 * Message protocol between a sandboxed TabFree dashboard and the host page (ADR-0002).
 *
 * The dashboard runs in an iframe with `sandbox="allow-scripts"` on a separate origin and
 * without `allow-same-origin`, so its origin is opaque: `event.origin` is the string "null"
 * and cannot authenticate anything. Instead:
 *
 * 1. The dashboard posts a {@link HelloMessage} to `window.parent`.
 * 2. The host accepts it only when `event.source === iframe.contentWindow`, creates a
 *    `MessageChannel`, and transfers one port with a {@link ConnectMessage}.
 * 3. All further traffic uses that port. A document that replaces the dashboard (for example
 *    after the frame navigates) does not hold the port and never receives data.
 *
 * Both sides validate every message with the parsers below and drop anything that does not
 * match exactly: unknown types, unexpected keys, or out-of-range values. Dashboards only ever
 * name queries declared in `tabfree.json`; the host runs them through the governed query API.
 */

export const PROTOCOL_VERSION = 0;

export type Scalar = string | number | boolean | null;
export type Tuple = Record<string, Scalar>;
export type Period = "minute" | "hour" | "day" | "week" | "month" | "quarter" | "year";

export interface RelativeDate {
  period: Period;
  from?: number;
  to?: number;
  anchor?: string;
}

export interface Wildcard {
  match: "contains" | "starts_with" | "ends_with" | "exact";
  value: string;
  caseSensitive?: boolean;
}

/** Mirrors `filterValue` in the dashboard manifest schema. */
export type FilterValue =
  | { members: Scalar[] }
  | { min?: Scalar; max?: Scalar }
  | { relative: RelativeDate }
  | { wildcard: Wildcard };

export interface Highlight {
  source: string;
  tuples: Tuple[];
}

export interface DashboardState {
  filters: Record<string, FilterValue>;
  parameters: Record<string, Scalar>;
  highlight: Highlight | null;
}

export type ColumnType =
  | "string"
  | "integer"
  | "float"
  | "decimal"
  | "boolean"
  | "date"
  | "datetime"
  | "geography";

export interface Column {
  name: string;
  type: ColumnType;
  role?: "dimension" | "measure";
  /** Tableau/Excel-style number format from the model, e.g. "$#,##0" or "0.0%". */
  format?: string;
}

export type FilterKind = "categorical" | "range" | "relative_date" | "wildcard";

/** What a dashboard needs to render a filter control; mirrors `filter` in the manifest. */
export interface FilterSpec {
  label: string;
  kind: FilterKind;
  multi: boolean;
  default: FilterValue | null;
}

export type ParameterType = "string" | "integer" | "float" | "boolean" | "date" | "datetime";

export interface ParameterSpec {
  label: string;
  type: ParameterType;
  default: Scalar;
  list?: Scalar[];
  min?: number;
  max?: number;
  step?: number;
}

export interface Theme {
  mode: "light" | "dark";
}

export type ErrorCode =
  | "forbidden"
  | "not_found"
  | "invalid_request"
  | "query_failed"
  | "timeout"
  | "rate_limited"
  | "internal";

// ---- Dashboard → host ---------------------------------------------------------------------------

export interface HelloMessage {
  v: 0;
  type: "hello";
}
export interface QueryMessage {
  v: 0;
  type: "query";
  id: string;
  query: string;
}
/** Ask for the members a filter control can offer; answered with a ResultMessage (query = filter). */
export interface DomainMessage {
  v: 0;
  type: "domain";
  id: string;
  filter: string;
}
export interface SetFilterMessage {
  v: 0;
  type: "setFilter";
  filter: string;
  value: FilterValue | null;
}
export interface SetParameterMessage {
  v: 0;
  type: "setParameter";
  parameter: string;
  value: Scalar;
}
export interface SelectMessage {
  v: 0;
  type: "select";
  source: string;
  tuples: Tuple[];
}
export interface ResizeMessage {
  v: 0;
  type: "resize";
  height: number;
}
export interface DashboardErrorMessage {
  v: 0;
  type: "error";
  message: string;
}
export type ExportFormat = "csv" | "crosstab" | "png" | "pdf";
/** Ask the host to export: summary data or a crosstab of one query, or an image/PDF of the view. */
export interface ExportMessage {
  v: 0;
  type: "export";
  format: ExportFormat;
  query?: string;
}

/** Go to another dashboard (navigation buttons, P6.11); the host checks the viewer may open it. */
export interface NavigateMessage {
  v: 0;
  type: "navigate";
  dashboard: string;
}

export type DashboardMessage =
  | NavigateMessage
  | HelloMessage
  | QueryMessage
  | DomainMessage
  | SetFilterMessage
  | SetParameterMessage
  | SelectMessage
  | ResizeMessage
  | DashboardErrorMessage
  | ExportMessage;

// ---- Host → dashboard ---------------------------------------------------------------------------

export interface ConnectMessage {
  v: 0;
  type: "connect";
}
export interface InitMessage {
  v: 0;
  type: "init";
  dashboard: { id: string; title: string };
  state: DashboardState;
  theme: Theme;
  filters: Record<string, FilterSpec>;
  parameters: Record<string, ParameterSpec>;
}
export interface ResultMessage {
  v: 0;
  type: "result";
  id: string;
  query: string;
  columns: Column[];
  rows: Scalar[][];
  truncated: boolean;
}
export interface HostErrorMessage {
  v: 0;
  type: "error";
  id?: string;
  code: ErrorCode;
  message: string;
}
export interface StateMessage {
  v: 0;
  type: "state";
  state: DashboardState;
}

export type HostMessage = ConnectMessage | InitMessage | ResultMessage | HostErrorMessage | StateMessage;

// ---- Validation ---------------------------------------------------------------------------------

export const LIMITS = {
  idLength: 128,
  fieldNameLength: 256,
  stringValueLength: 4096,
  messageLength: 2000,
  tupleFields: 64,
  tuples: 10_000,
  members: 10_000,
  maxHeight: 100_000,
} as const;

/** Query, filter and parameter names, as in the manifest schema. */
const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const SLUG = /^[a-z0-9][a-z0-9-]{0,99}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const PERIODS: readonly unknown[] = ["minute", "hour", "day", "week", "month", "quarter", "year"];
const MATCHES: readonly unknown[] = ["contains", "starts_with", "ends_with", "exact"];
const COLUMN_TYPES: readonly unknown[] = [
  "string",
  "integer",
  "float",
  "decimal",
  "boolean",
  "date",
  "datetime",
  "geography",
];
const FILTER_KINDS: readonly unknown[] = ["categorical", "range", "relative_date", "wildcard"];
const EXPORT_FORMATS: readonly unknown[] = ["csv", "crosstab", "png", "pdf"];
const PARAMETER_TYPES: readonly unknown[] = ["string", "integer", "float", "boolean", "date", "datetime"];
const ERROR_CODES: readonly unknown[] = [
  "forbidden",
  "not_found",
  "invalid_request",
  "query_failed",
  "timeout",
  "rate_limited",
  "internal",
];

type Obj = Record<string, unknown>;

/** A plain object (rejects arrays, null, class instances and null-prototype objects). */
function isObject(value: unknown): value is Obj {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

/** True when `obj` has every required key and nothing outside required + optional. */
function hasExactKeys(obj: Obj, required: readonly string[], optional: readonly string[] = []): boolean {
  return (
    required.every((key) => Object.hasOwn(obj, key)) &&
    Object.keys(obj).every((key) => required.includes(key) || optional.includes(key))
  );
}

function isName(value: unknown): value is string {
  return typeof value === "string" && NAME.test(value);
}

function isId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= LIMITS.idLength;
}

export function isScalar(value: unknown): value is Scalar {
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  return typeof value === "string" && value.length <= LIMITS.stringValueLength;
}

function isFieldKey(key: string): boolean {
  return key.length > 0 && key.length <= LIMITS.fieldNameLength && key !== "__proto__";
}

export function isTuple(value: unknown): value is Tuple {
  if (!isObject(value)) return false;
  const entries = Object.entries(value);
  return entries.length <= LIMITS.tupleFields && entries.every(([k, v]) => isFieldKey(k) && isScalar(v));
}

function isTupleList(value: unknown): value is Tuple[] {
  return Array.isArray(value) && value.length <= LIMITS.tuples && value.every(isTuple);
}

function isRelativeDate(value: unknown): value is RelativeDate {
  if (!isObject(value) || !hasExactKeys(value, ["period"], ["from", "to", "anchor"])) return false;
  if (!PERIODS.includes(value.period)) return false;
  for (const key of ["from", "to"]) {
    if (Object.hasOwn(value, key) && !Number.isInteger(value[key])) return false;
  }
  return !Object.hasOwn(value, "anchor") || (typeof value.anchor === "string" && ISO_DATE.test(value.anchor));
}

function isWildcard(value: unknown): value is Wildcard {
  return (
    isObject(value) &&
    hasExactKeys(value, ["match", "value"], ["caseSensitive"]) &&
    MATCHES.includes(value.match) &&
    typeof value.value === "string" &&
    value.value.length <= LIMITS.stringValueLength &&
    (!Object.hasOwn(value, "caseSensitive") || typeof value.caseSensitive === "boolean")
  );
}

export function isFilterValue(value: unknown): value is FilterValue {
  if (!isObject(value)) return false;
  if (hasExactKeys(value, ["members"])) {
    const { members } = value;
    return Array.isArray(members) && members.length <= LIMITS.members && members.every(isScalar);
  }
  if (hasExactKeys(value, ["relative"])) return isRelativeDate(value.relative);
  if (hasExactKeys(value, ["wildcard"])) return isWildcard(value.wildcard);
  const keys = Object.keys(value);
  return keys.length > 0 && hasExactKeys(value, [], ["min", "max"]) && keys.every((k) => isScalar(value[k]));
}

function isRecordOf<T>(value: unknown, isValue: (v: unknown) => v is T): value is Record<string, T> {
  return isObject(value) && Object.entries(value).every(([k, v]) => NAME.test(k) && isValue(v));
}

export function isDashboardState(value: unknown): value is DashboardState {
  if (!isObject(value) || !hasExactKeys(value, ["filters", "parameters", "highlight"])) return false;
  const { filters, parameters, highlight } = value;
  if (!isRecordOf(filters, isFilterValue) || !isRecordOf(parameters, isScalar)) return false;
  if (highlight === null) return true;
  return (
    isObject(highlight) &&
    hasExactKeys(highlight, ["source", "tuples"]) &&
    isName(highlight.source) &&
    isTupleList(highlight.tuples)
  );
}

function isColumn(value: unknown): value is Column {
  return (
    isObject(value) &&
    hasExactKeys(value, ["name", "type"], ["role", "format"]) &&
    typeof value.name === "string" &&
    isFieldKey(value.name) &&
    COLUMN_TYPES.includes(value.type) &&
    (!Object.hasOwn(value, "role") || value.role === "dimension" || value.role === "measure") &&
    (!Object.hasOwn(value, "format") || (typeof value.format === "string" && value.format.length <= 128))
  );
}

function isLabel(value: unknown): value is string {
  return typeof value === "string" && value.length <= 200;
}

function isFilterSpec(value: unknown): value is FilterSpec {
  return (
    isObject(value) &&
    hasExactKeys(value, ["label", "kind", "multi", "default"]) &&
    isLabel(value.label) &&
    FILTER_KINDS.includes(value.kind) &&
    typeof value.multi === "boolean" &&
    (value.default === null || isFilterValue(value.default))
  );
}

function isOptionalNumber(obj: Obj, key: string): boolean {
  return !Object.hasOwn(obj, key) || (typeof obj[key] === "number" && Number.isFinite(obj[key]));
}

function isParameterSpec(value: unknown): value is ParameterSpec {
  if (!isObject(value) || !hasExactKeys(value, ["label", "type", "default"], ["list", "min", "max", "step"])) {
    return false;
  }
  const { list } = value;
  return (
    isLabel(value.label) &&
    PARAMETER_TYPES.includes(value.type) &&
    isScalar(value.default) &&
    (list === undefined || (Array.isArray(list) && list.length <= LIMITS.members && list.every(isScalar))) &&
    ["min", "max", "step"].every((k) => isOptionalNumber(value, k))
  );
}

/** Validate a message sent by a dashboard. Returns null for anything malformed. */
export function parseDashboardMessage(data: unknown): DashboardMessage | null {
  if (!isObject(data) || data.v !== PROTOCOL_VERSION || typeof data.type !== "string") return null;
  switch (data.type) {
    case "hello":
      return hasExactKeys(data, ["v", "type"]) ? { v: 0, type: "hello" } : null;
    case "query": {
      const { id, query } = data;
      if (!hasExactKeys(data, ["v", "type", "id", "query"]) || !isId(id) || !isName(query)) return null;
      return { v: 0, type: "query", id, query };
    }
    case "domain": {
      const { id, filter } = data;
      if (!hasExactKeys(data, ["v", "type", "id", "filter"]) || !isId(id) || !isName(filter)) return null;
      return { v: 0, type: "domain", id, filter };
    }
    case "setFilter": {
      const { filter, value } = data;
      if (!hasExactKeys(data, ["v", "type", "filter", "value"]) || !isName(filter)) return null;
      if (value === null) return { v: 0, type: "setFilter", filter, value: null };
      return isFilterValue(value) ? { v: 0, type: "setFilter", filter, value } : null;
    }
    case "setParameter": {
      const { parameter, value } = data;
      if (!hasExactKeys(data, ["v", "type", "parameter", "value"]) || !isName(parameter)) return null;
      return isScalar(value) ? { v: 0, type: "setParameter", parameter, value } : null;
    }
    case "select": {
      const { source, tuples } = data;
      if (!hasExactKeys(data, ["v", "type", "source", "tuples"]) || !isName(source)) return null;
      return isTupleList(tuples) ? { v: 0, type: "select", source, tuples } : null;
    }
    case "resize": {
      const { height } = data;
      if (!hasExactKeys(data, ["v", "type", "height"]) || typeof height !== "number") return null;
      return Number.isFinite(height) && height >= 0 && height <= LIMITS.maxHeight
        ? { v: 0, type: "resize", height }
        : null;
    }
    case "error": {
      const { message } = data;
      if (!hasExactKeys(data, ["v", "type", "message"]) || typeof message !== "string") return null;
      return { v: 0, type: "error", message: message.slice(0, LIMITS.messageLength) };
    }
    case "export": {
      const { format, query } = data;
      if (!hasExactKeys(data, ["v", "type", "format"], ["query"]) || !EXPORT_FORMATS.includes(format)) return null;
      if (query === undefined) return { v: 0, type: "export", format: format as ExportFormat };
      return isName(query) ? { v: 0, type: "export", format: format as ExportFormat, query } : null;
    }
    case "navigate": {
      const { dashboard } = data;
      if (!hasExactKeys(data, ["v", "type", "dashboard"]) || typeof dashboard !== "string") return null;
      return /^[a-z0-9][a-z0-9-]{0,99}$/.test(dashboard) ? { v: 0, type: "navigate", dashboard } : null;
    }
    default:
      return null;
  }
}

/** Validate a message sent by the host. Returns null for anything malformed. */
export function parseHostMessage(data: unknown): HostMessage | null {
  if (!isObject(data) || data.v !== PROTOCOL_VERSION || typeof data.type !== "string") return null;
  switch (data.type) {
    case "connect":
      return hasExactKeys(data, ["v", "type"]) ? { v: 0, type: "connect" } : null;
    case "init": {
      const { dashboard, state, theme, filters, parameters } = data;
      if (!hasExactKeys(data, ["v", "type", "dashboard", "state", "theme", "filters", "parameters"])) return null;
      if (!isRecordOf(filters, isFilterSpec) || !isRecordOf(parameters, isParameterSpec)) return null;
      if (!isObject(dashboard) || !hasExactKeys(dashboard, ["id", "title"])) return null;
      const { id, title } = dashboard;
      if (typeof id !== "string" || !SLUG.test(id) || typeof title !== "string") return null;
      if (!isDashboardState(state) || !isObject(theme) || !hasExactKeys(theme, ["mode"])) return null;
      const { mode } = theme;
      if (mode !== "light" && mode !== "dark") return null;
      return { v: 0, type: "init", dashboard: { id, title }, state, theme: { mode }, filters, parameters };
    }
    case "result": {
      const { id, query, columns, rows, truncated } = data;
      const keys = ["v", "type", "id", "query", "columns", "rows", "truncated"];
      if (!hasExactKeys(data, keys) || !isId(id) || !isName(query) || typeof truncated !== "boolean") {
        return null;
      }
      if (!Array.isArray(columns) || !columns.every(isColumn)) return null;
      const width = columns.length;
      const rowsOk =
        Array.isArray(rows) &&
        rows.every((row) => Array.isArray(row) && row.length === width && row.every(isScalar));
      return rowsOk ? { v: 0, type: "result", id, query, columns, rows: rows as Scalar[][], truncated } : null;
    }
    case "error": {
      const { id, code, message } = data;
      if (!hasExactKeys(data, ["v", "type", "code", "message"], ["id"])) return null;
      if (!ERROR_CODES.includes(code) || typeof message !== "string") return null;
      const errorCode = code as ErrorCode;
      if (id === undefined) return { v: 0, type: "error", code: errorCode, message };
      return isId(id) ? { v: 0, type: "error", id, code: errorCode, message } : null;
    }
    case "state": {
      const { state } = data;
      if (!hasExactKeys(data, ["v", "type", "state"]) || !isDashboardState(state)) return null;
      return { v: 0, type: "state", state };
    }
    default:
      return null;
  }
}
