/** Value helpers: normalisation, ordering and the statistics behind WINDOW_* functions. */

import type { Scalar } from "./types.js";

/** NaN and infinities are not values Tableau can show; they become null. */
export function normalize(value: Scalar | undefined): Scalar {
  if (value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    if (value === 0) return 0; // no -0
  }
  return value;
}

export function isNumber(value: Scalar): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function typeRank(value: Scalar): number {
  if (typeof value === "boolean") return 0;
  if (typeof value === "number") return 1;
  return 2;
}

/** Total order: booleans, numbers, strings (ISO dates sort chronologically); nulls last. */
export function compareScalars(a: Scalar, b: Scalar): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
  const ta = typeRank(a);
  const tb = typeRank(b);
  if (ta !== tb) return ta - tb;
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

export function numbers(values: readonly Scalar[]): number[] {
  return values.filter(isNumber);
}

export function sum(values: readonly number[]): number | null {
  if (!values.length) return null;
  let total = 0;
  for (const v of values) total += v;
  return total;
}

export function mean(values: readonly number[]): number | null {
  const total = sum(values);
  return total === null ? null : total / values.length;
}

export function extreme(values: readonly Scalar[], which: "min" | "max"): Scalar {
  let best: Scalar = null;
  for (const v of values) {
    if (v === null) continue;
    if (best === null) best = v;
    else {
      const c = compareScalars(v, best);
      if (which === "min" ? c < 0 : c > 0) best = v;
    }
  }
  return best;
}

/** Linear interpolation between closest ranks (PERCENTILE.INC), as Tableau's PERCENTILE does. */
export function percentile(values: readonly number[], p: number): number | null {
  if (!values.length || !(p >= 0 && p <= 1)) return null;
  const sorted = [...values].sort((x, y) => x - y);
  const pos = (sorted.length - 1) * p;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const a = sorted[lo] ?? 0;
  const b = sorted[hi] ?? 0;
  return a + (b - a) * (pos - lo);
}

export function variance(values: readonly number[], population: boolean): number | null {
  const n = values.length;
  if (n === 0 || (!population && n < 2)) return null;
  const m = (sum(values) ?? 0) / n;
  let ss = 0;
  for (const v of values) ss += (v - m) ** 2;
  return ss / (population ? n : n - 1);
}

/** Pairs where both sides are numbers. */
export function pairs(xs: readonly Scalar[], ys: readonly Scalar[]): [number[], number[]] {
  const a: number[] = [];
  const b: number[] = [];
  xs.forEach((x, i) => {
    const y = ys[i] ?? null;
    if (isNumber(x) && isNumber(y)) {
      a.push(x);
      b.push(y);
    }
  });
  return [a, b];
}

export function covariance(xs: readonly number[], ys: readonly number[], population: boolean): number | null {
  const n = xs.length;
  if (n === 0 || (!population && n < 2)) return null;
  const mx = (sum(xs) ?? 0) / n;
  const my = (sum(ys) ?? 0) / n;
  let s = 0;
  for (let i = 0; i < n; i++) s += ((xs[i] ?? 0) - mx) * ((ys[i] ?? 0) - my);
  return s / (population ? n : n - 1);
}

export function correlation(xs: readonly number[], ys: readonly number[]): number | null {
  const cov = covariance(xs, ys, true);
  const vx = variance(xs, true);
  const vy = variance(ys, true);
  if (cov === null || vx === null || vy === null || vx === 0 || vy === 0) return null;
  return cov / Math.sqrt(vx * vy);
}

/** A stable key for grouping rows by a tuple of values (1 and "1" stay distinct). */
export function tupleKey(values: readonly Scalar[]): string {
  return JSON.stringify(values);
}
