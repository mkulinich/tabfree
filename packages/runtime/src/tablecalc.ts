/**
 * Table calculations over a query result. The engine lives in `@tabfree/tablecalc` (ADR-004: the
 * JS engine is the source of truth); the runtime re-exports it so the host and dashboards keep one
 * import path. `Column` and `Scalar` stay the protocol's own (the engine's are structurally identical).
 */

export {
  applyTableCalcs,
  evaluateExpression,
  quickCalcExpression,
  parseExpression,
  TableCalcError,
  type AddressingSpec,
  type Aggregation,
  type ComputeUsing,
  type Frame,
  type SecondaryCalc,
  type SortSpec,
  type Table,
  type TableCalc,
  type TableCalcFilter,
  type TableCalcOptions,
  type TableCalcType,
} from "@tabfree/tablecalc";
