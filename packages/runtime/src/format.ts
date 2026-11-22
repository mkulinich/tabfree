/**
 * Display formatting for query values, following the Tableau/Excel number formats stored in the
 * semantic model ("$#,##0", "0.0%", "#,##0.00 kg"). Only the common subset is supported; anything
 * else falls back to a sensible default for the column type.
 */

import type { Column, Scalar } from "./protocol.js";

export const NULL_LABEL = "Null";

export type DateStyle = "iso" | "short" | "medium" | "long";

/** Number and date localisation (P7.4): the viewer's language by default; dashboards may set it. */
let LOCALE = defaultLocale();
let DATES: DateStyle = "iso";

function defaultLocale(): string {
  const language = typeof navigator !== "undefined" ? navigator.language : undefined;
  try {
    return language ? (Intl.getCanonicalLocales(language)[0] ?? "en-US") : "en-US";
  } catch {
    return "en-US";
  }
}

/** Set the locale for numbers and how dates read ("iso" keeps 2024-03-01, Tableau-style defaults). */
export function setLocale(locale: string | null, dates: DateStyle = "iso"): void {
  try {
    LOCALE = locale ? (Intl.getCanonicalLocales(locale)[0] ?? defaultLocale()) : defaultLocale();
  } catch {
    LOCALE = defaultLocale();
  }
  DATES = dates;
  numberFormats.clear();
}

export function currentLocale(): string {
  return LOCALE;
}

const numberFormats = new Map<string, Intl.NumberFormat>();

interface NumberFormat {
  prefix: string;
  suffix: string;
  decimals: number;
  grouping: boolean;
  percent: boolean;
}

const cache = new Map<string, NumberFormat | null>();

/** Parse the positive section of a number format; null when it is not a supported pattern. */
export function parseNumberFormat(pattern: string): NumberFormat | null {
  const cached = cache.get(pattern);
  if (cached !== undefined) return cached;
  const positive = pattern.split(";")[0] ?? "";
  // Split off the prefix first so no two unbounded groups can match the same text (no ReDoS).
  const first = positive.search(/[#0.,]/);
  const lead = first < 0 ? positive : positive.slice(0, first);
  const body = /^([#0,]*)(?:\.(0+|#+))?([^#0.,]*)$/.exec(first < 0 ? "" : positive.slice(first));
  const match = body ? [body[0], lead, body[1], body[2], body[3]] : null;
  let parsed: NumberFormat | null = null;
  if (match && (match[2] ?? "").length > 0) {
    const prefix = unquote(match[1] ?? "");
    const suffix = unquote(match[4] ?? "");
    parsed = {
      prefix: prefix.replace("%", ""),
      suffix: suffix.replace("%", ""),
      decimals: (match[3] ?? "").length,
      grouping: (match[2] ?? "").includes(","),
      percent: prefix.includes("%") || suffix.includes("%"),
    };
    if (parsed.percent) parsed.suffix = `${parsed.suffix}%`;
  }
  cache.set(pattern, parsed);
  return parsed;
}

function unquote(text: string): string {
  return text.replace(/"([^"]*)"/g, "$1").replace(/\\(.)/g, "$1");
}

export function formatNumber(value: number, format: NumberFormat): string {
  const scaled = format.percent ? value * 100 : value;
  const key = `${LOCALE}|${format.decimals}|${format.grouping}`;
  let formatter = numberFormats.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, {
      minimumFractionDigits: format.decimals,
      maximumFractionDigits: format.decimals,
      useGrouping: format.grouping,
    });
    numberFormats.set(key, formatter);
  }
  const text = formatter.format(Math.abs(scaled));
  // "-0.00" is just "0.00": the sign shows only when the rounded value is not zero.
  const sign = scaled < 0 && Number(Math.abs(scaled).toFixed(format.decimals)) !== 0 ? "-" : "";
  return `${sign}${format.prefix}${text}${format.suffix}`;
}

const DEFAULT_FLOAT: NumberFormat = { prefix: "", suffix: "", decimals: 2, grouping: true, percent: false };
const DEFAULT_INTEGER: NumberFormat = { prefix: "", suffix: "", decimals: 0, grouping: true, percent: false };

/** Format one value for display. Never returns HTML: callers put the result in textContent. */
export function formatValue(value: Scalar, column: Pick<Column, "type" | "format" | "role">): string {
  if (value === null) return NULL_LABEL;
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number") {
    const explicit = column.format ? parseNumberFormat(column.format) : null;
    if (explicit) return formatNumber(value, explicit);
    // Whole-number dimensions without a format (date parts such as YEAR, IDs) show no separators.
    if (column.role === "dimension" && Number.isInteger(value)) return String(value);
    if (column.type === "integer" || Number.isInteger(value)) return formatNumber(value, DEFAULT_INTEGER);
    return formatNumber(value, DEFAULT_FLOAT);
  }
  if (column.type === "date") return DATES === "iso" ? value.slice(0, 10) : localDate(value.slice(0, 10), false);
  if (column.type === "datetime") return DATES === "iso" ? formatDatetime(value) : localDate(value, true);
  return value;
}

function localDate(value: string, withTime: boolean): string {
  const iso = value.length <= 10 ? `${value}T00:00:00Z` : `${value.replace(" ", "T").slice(0, 19)}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return value;
  const style = DATES === "iso" ? "medium" : DATES;
  return new Intl.DateTimeFormat(LOCALE, withTime ? { dateStyle: style, timeStyle: "short", timeZone: "UTC" } : { dateStyle: style, timeZone: "UTC" }).format(date);
}

function formatDatetime(value: string): string {
  const text = value.replace("T", " ");
  return text.endsWith(" 00:00:00") ? text.slice(0, 10) : text.slice(0, 19);
}

/** Compact axis labels: 1.2K, 3.4M (keeps the format's prefix, suffix and percent). */
export function formatAxis(value: number, column: Pick<Column, "type" | "format">): string {
  const explicit = column.format ? parseNumberFormat(column.format) : null;
  if (explicit?.percent) return formatNumber(value, { ...explicit, decimals: Math.min(explicit.decimals, 1) });
  const abs = Math.abs(value);
  const units: [number, string][] = [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [size, unit] of units) {
    if (abs >= size) {
      const base = explicit ?? DEFAULT_INTEGER;
      return formatNumber(value / size, { ...base, decimals: abs / size < 10 ? 1 : 0, suffix: `${unit}${base.suffix}` });
    }
  }
  return formatNumber(value, explicit ? { ...explicit } : Number.isInteger(value) ? DEFAULT_INTEGER : DEFAULT_FLOAT);
}
