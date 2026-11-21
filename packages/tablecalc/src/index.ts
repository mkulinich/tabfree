/**
 * @tabfree/tablecalc: Tableau table calculations over a query result (ADR-004: this engine is the
 * source of truth). See engine.ts for the pipeline, addressing.ts for Compute Using, quick.ts for
 * the quick-calc formulas and evaluator.ts for the formula language.
 */

export { applyTableCalcs, evaluateExpression } from "./engine.js";
export { addressingFields, layoutOf, type Layout } from "./addressing.js";
export { densify, MAX_DENSIFIED_ROWS, type Densified } from "./densify.js";
export { parseExpression, type BinaryOp, type Expr } from "./parser.js";
export { fieldRef, quickCalcExpression, type QuickCalc } from "./quick.js";
export { rank } from "./evaluator.js";
export {
  TableCalcError,
  type AddressingSpec,
  type Aggregation,
  type Column,
  type ColumnType,
  type ComputeUsing,
  type Frame,
  type RelativeTo,
  type Scalar,
  type SecondaryCalc,
  type SortSpec,
  type Table,
  type TableCalc,
  type TableCalcFilter,
  type TableCalcOptions,
  type TableCalcType,
} from "./types.js";
