/**
 * Public types of the table-calculation engine. They mirror `tableCalc`, `secondaryCalc`, `frame`,
 * `computeUsing` and `tableCalcType` in the dashboard manifest schema, and the result shape of
 * `@tabfree/runtime` (structurally identical `Column`/`Scalar`, so results pass straight through).
 */

export type Scalar = string | number | boolean | null;

export type ColumnType = "string" | "integer" | "float" | "decimal" | "boolean" | "date" | "datetime" | "geography";

export interface Column {
  name: string;
  type: ColumnType;
  /** Columns without `role: "measure"` are dimensions: they address or partition table calcs. */
  role?: "dimension" | "measure";
  format?: string;
}

/** A query result: aggregated rows of dimension and measure columns. */
export interface Table {
  columns: Column[];
  rows: Scalar[][];
}

export type ComputeUsing =
  | "table_across"
  | "table_down"
  | "table_across_then_down"
  | "table_down_then_across"
  | "pane_across"
  | "pane_down"
  | "pane_across_then_down"
  | "pane_down_then_across"
  | "cell"
  | "specific";

export type TableCalcType =
  | "running_sum"
  | "running_avg"
  | "running_min"
  | "running_max"
  | "running_count"
  | "window_sum"
  | "window_avg"
  | "window_min"
  | "window_max"
  | "window_count"
  | "window_median"
  | "difference"
  | "percent_difference"
  | "percent_of_total"
  | "percentile"
  | "rank"
  | "rank_dense"
  | "rank_modified"
  | "rank_unique"
  | "rank_percentile"
  | "moving_avg"
  | "lookup"
  | "index"
  | "first"
  | "last"
  | "size"
  | "total"
  | "custom";

/** Window bounds as offsets from the current row; "first"/"last" mean the partition's ends. */
export interface Frame {
  start?: number | "first";
  end?: number | "last";
}

/** The manifest's `sort`: order by an output column, optionally with a manual member order. */
export interface SortSpec {
  by: string;
  dir?: "asc" | "desc";
  /** Manual sort: members in this order first, then the rest by value. */
  order?: readonly Scalar[];
}

/** How a calculation is addressed and partitioned (Tableau's "Compute Using"). */
export interface AddressingSpec {
  computeUsing?: ComputeUsing;
  /** Output columns the calculation moves along, outermost first (computeUsing "specific"). */
  addressing?: readonly string[];
  /** Output columns that restart the calculation. Dimensions in neither list also partition. */
  partitioning?: readonly string[];
  /** Order along the addressing (Tableau's custom sort). Default: the addressing fields' values. */
  orderBy?: readonly SortSpec[];
  /** Tableau's "At the level": an addressing field; the calc steps only when it (or an outer field) changes. */
  atLevel?: string;
  /** Tableau's "Restarting every": this addressing field and the ones above it become partitioning. */
  restartEvery?: string;
}

export type RelativeTo = "previous" | "next" | "first" | "last";

export interface SecondaryCalc extends AddressingSpec {
  type: Exclude<TableCalcType, "custom">;
  relativeTo?: RelativeTo;
  frame?: Frame;
  direction?: "asc" | "desc";
  offset?: number;
  percentile?: number;
  nullIfIncomplete?: boolean;
}

export interface TableCalc extends AddressingSpec {
  /** Output column name. An existing column with this name is replaced (e.g. a SQL push-down value). */
  name: string;
  type: TableCalcType;
  /** The result column the calculation applies to. */
  of?: string;
  /** For type "custom": a Tableau table-calculation formula. */
  expr?: string;
  frame?: Frame;
  relativeTo?: RelativeTo;
  offset?: number;
  /** With type "percentile": WINDOW_PERCENTILE at this fraction (0..1) instead of RANK_PERCENTILE. */
  percentile?: number;
  /** Moving/window calcs: null where the frame does not fit inside the partition. */
  nullIfIncomplete?: boolean;
  direction?: "asc" | "desc";
  /** A secondary calculation applied to this calculation's result. */
  then?: SecondaryCalc;
}

/** A table-calculation filter: rows where `expr` is TRUE are kept, after every calc has run. */
export interface TableCalcFilter extends AddressingSpec {
  expr: string;
}

/** How TOTAL() re-aggregates a bare result column. */
export type Aggregation = "sum" | "avg" | "min" | "max" | "count" | "attr";

export interface TableCalcOptions {
  /**
   * Dimension columns on the Columns shelf ("across"), outermost first. Every other dimension is on
   * the Rows shelf ("down"), outermost first in column order. Default: none, so every dimension is down.
   */
  across?: readonly string[];
  /**
   * Densification (Tableau's domain completion): `true` completes every dimension, a list completes
   * those dimensions within each combination of the other dimensions. Domains are the values seen
   * in the result; inserted rows have null measures.
   */
  densify?: boolean | readonly string[];
  /** Remove densified rows from the output after the calcs (and filters) have used them. */
  dropDensified?: boolean;
  /** Table-calculation filters, applied after the calcs without changing their values. */
  filters?: readonly TableCalcFilter[];
  /** How TOTAL() re-aggregates bare measure columns (default "sum"; dimensions use "attr"). */
  aggregations?: Readonly<Record<string, Aggregation>>;
}

export class TableCalcError extends Error {
  override name = "TableCalcError";
}
