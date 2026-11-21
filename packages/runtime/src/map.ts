/**
 * `<tf-map>` Phase 1 renderer: a US state tile map (one equal square per state, in a grid that keeps
 * rough geography). It needs no boundary files, so it works inside the offline sandbox and draws
 * identically in snapshots. Filled geographic maps (MapLibre + deck.gl) arrive in Phase 4.
 */

import { s } from "./dom.js";
import { formatValue } from "./format.js";
import type { Column, Scalar, Tuple } from "./protocol.js";

/** State name → [postal code, row, column]. */
export const US_STATE_TILES: Record<string, [string, number, number]> = {
  Alaska: ["AK", 0, 0],
  Maine: ["ME", 0, 10],
  Vermont: ["VT", 1, 9],
  "New Hampshire": ["NH", 1, 10],
  Washington: ["WA", 2, 0],
  Idaho: ["ID", 2, 1],
  Montana: ["MT", 2, 2],
  "North Dakota": ["ND", 2, 3],
  Minnesota: ["MN", 2, 4],
  Illinois: ["IL", 2, 5],
  Wisconsin: ["WI", 2, 6],
  Michigan: ["MI", 2, 7],
  "New York": ["NY", 2, 8],
  "Rhode Island": ["RI", 2, 9],
  Massachusetts: ["MA", 2, 10],
  Oregon: ["OR", 3, 0],
  Nevada: ["NV", 3, 1],
  Wyoming: ["WY", 3, 2],
  "South Dakota": ["SD", 3, 3],
  Iowa: ["IA", 3, 4],
  Indiana: ["IN", 3, 5],
  Ohio: ["OH", 3, 6],
  Pennsylvania: ["PA", 3, 7],
  "New Jersey": ["NJ", 3, 8],
  Connecticut: ["CT", 3, 9],
  California: ["CA", 4, 0],
  Utah: ["UT", 4, 1],
  Colorado: ["CO", 4, 2],
  Nebraska: ["NE", 4, 3],
  Missouri: ["MO", 4, 4],
  Kentucky: ["KY", 4, 5],
  "West Virginia": ["WV", 4, 6],
  Virginia: ["VA", 4, 7],
  Maryland: ["MD", 4, 8],
  Delaware: ["DE", 4, 9],
  Arizona: ["AZ", 5, 1],
  "New Mexico": ["NM", 5, 2],
  Kansas: ["KS", 5, 3],
  Arkansas: ["AR", 5, 4],
  Tennessee: ["TN", 5, 5],
  "North Carolina": ["NC", 5, 6],
  "South Carolina": ["SC", 5, 7],
  "District of Columbia": ["DC", 5, 8],
  Oklahoma: ["OK", 6, 3],
  Louisiana: ["LA", 6, 4],
  Mississippi: ["MS", 6, 5],
  Alabama: ["AL", 6, 6],
  Georgia: ["GA", 6, 7],
  Hawaii: ["HI", 7, 0],
  Texas: ["TX", 7, 3],
  Florida: ["FL", 7, 8],
};

const BY_CODE = new Map(Object.entries(US_STATE_TILES).map(([name, [code]]) => [code, name]));

export interface MapData {
  columns: Column[];
  rows: Scalar[][];
}

export interface MapOptions {
  width: number;
  highlight?: Tuple[] | null | undefined;
  onSelect?: ((tuple: Tuple | null) => void) | undefined;
}

export class MapError extends Error {}

function stateOf(value: Scalar): string | undefined {
  if (typeof value !== "string") return undefined;
  if (US_STATE_TILES[value]) return value;
  return BY_CODE.get(value.toUpperCase());
}

/** Render the tile map; ``geo`` is a state column (names or postal codes), ``color`` a measure. */
export function renderStateMap(geo: string, color: string, data: MapData, options: MapOptions): SVGSVGElement {
  const gi = data.columns.findIndex((c) => c.name === geo);
  const ci = data.columns.findIndex((c) => c.name === color);
  if (gi < 0) throw new MapError(`the query has no column "${geo}"`);
  if (ci < 0) throw new MapError(`the query has no column "${color}"`);
  const colorColumn = data.columns[ci] as Column;
  const values = new Map<string, Scalar[]>();
  for (const row of data.rows) {
    const state = stateOf(row[gi] ?? null);
    if (state) values.set(state, row);
  }
  const numbers = [...values.values()].map((r) => r[ci]).filter((v): v is number => typeof v === "number");
  const lo = Math.min(0, ...numbers);
  const hi = Math.max(...numbers, lo + 1);

  const cols = 11;
  const gap = 3;
  const size = Math.max(16, Math.floor((options.width - gap * (cols - 1)) / cols));
  const width = cols * size + (cols - 1) * gap;
  const height = 8 * size + 7 * gap + 26;
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", class: "tf-svg tf-map", "aria-label": `${color} by ${geo}` });
  const highlight = options.highlight?.length ? options.highlight : null;

  for (const [name, [code, row, col]] of Object.entries(US_STATE_TILES)) {
    const x = col * (size + gap);
    const y = row * (size + gap);
    const data_row = values.get(name);
    const value = data_row?.[ci];
    const tile = s("g", { class: "tf-tile" });
    const rect = s("rect", { x, y, width: size, height: size, rx: 3 });
    if (typeof value === "number") {
      const share = hi === lo ? 1 : (value - lo) / (hi - lo);
      rect.setAttribute("class", "tf-mark tf-c0");
      rect.setAttribute("fill-opacity", (0.18 + 0.82 * share).toFixed(2));
      const tuple: Tuple = { [geo]: data_row?.[gi] ?? name };
      if (highlight && !highlight.some((t) => t[geo] === tuple[geo])) rect.classList.add("tf-dim");
      rect.append(s("title", {}, `${name}\n${color}: ${formatValue(value, colorColumn)}`));
      if (options.onSelect) {
        const select = options.onSelect;
        rect.addEventListener("click", (event) => {
          event.stopPropagation();
          select(tuple);
        });
      }
    } else {
      rect.setAttribute("class", "tf-empty");
      rect.append(s("title", {}, `${name}: no data`));
    }
    tile.append(rect, s("text", { x: x + size / 2, y: y + size / 2 + 4, "text-anchor": "middle", class: "tf-tile-label" }, code));
    svg.append(tile);
  }
  // Legend: low → high.
  const legendY = height - 14;
  svg.append(
    s("text", { x: 0, y: legendY + 10 }, formatValue(lo, colorColumn)),
    s("rect", { x: 70, y: legendY, width: 80, height: 10, class: "tf-c0", "fill-opacity": "0.18" }),
    s("rect", { x: 150, y: legendY, width: 80, height: 10, class: "tf-c0" }),
    s("text", { x: 236, y: legendY + 10 }, formatValue(hi, colorColumn)),
  );
  return svg;
}
