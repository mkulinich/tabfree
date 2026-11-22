# ADR-012: Parse Tableau calculations with a hand-written parser, checked against the official grammar

## Status

Proposed, 2026-11-20.

If accepted, this amends item 1 of [ADR-003](0003-own-tableau-semantics-compiler.md), which named the ANTLR4 Python target. The rest of ADR-003 is unchanged.

## Context

ADR-003 planned to parse formulas with the vendored Apache-2.0 grammar (`third_party/tableau-calc-grammar/TableauCalculationGrammar.g4`) through ANTLR4's Python target. Building P1.10 surfaced three problems with that route:

- **Toolchain.** Generating the parser needs a Java runtime and the ANTLR tool jar at build time. Neither is in the toolchain ([ADR-005](0005-python-and-typescript.md)), and the build machine has no Java runtime.
- **Types and speed.** Generated ANTLR Python code is untyped and would need a mypy exclusion, against the project's `--strict` rule. Its runtime is also markedly slower than a small recursive-descent parser.
- **Grammar coverage.** The grammar is a validator for an evaluation tool rather than Tableau's production grammar. It encodes precedence as rule order, lists function names as tokens (so new functions are syntax errors), and accepts `ELSE IF` only as a nested `IF`.

The language itself is small: literals, bracketed fields, operators at seven precedence levels, `IF`/`CASE`, `IN`, function calls and LOD braces.

## Decision

We will keep a hand-written recursive-descent parser in `tabfree_compiler.calc`. It produces an immutable, fully typed AST and follows the vendored grammar's tokens and Tableau's documented precedence: unary minus, then `^`, then `* / %`, then `+ -`, then comparisons and `IN`, then `NOT`, `AND`, `OR`. It treats function names as identifiers, so the function table, not the parser, decides what is supported. The vendored grammar remains the reference. Disagreements are settled by Tableau's behaviour, as recorded by TDVT and the corpus.

The parser treats formulas as untrusted input: it caps formula length and nesting depth and never evaluates anything.

## Consequences

### Positive

- No Java or code-generation step; the parser is about 400 lines, typed under `mypy --strict`, with no new dependencies.
- Error messages carry positions, and function support grows without touching syntax.
- Measured on 2026-11-20: all 1,603 calculated-field formulas in the corpus and all 1,404 TDVT expressions parse.

### Negative

- We own the parser's correctness. Drift from Tableau's grammar is caught only by tests: the corpus-wide parse test, the TDVT gate and unit tests.
- Contributors cannot regenerate it from the `.g4` file when Tableau adds syntax; changes are manual.

## Alternatives considered

- **ANTLR4 Python target, as in ADR-003:** rejected for the toolchain and typing reasons above.
- **ANTLR4 TypeScript target, shared with the browser:** the compiler runs server-side in Python (ADR-005), and the runtime receives compiled results, not formulas.
- **A parser-combinator or Lark grammar:** adds a dependency for a grammar this small, with no gain in fidelity.

## Validation and revisit triggers

- **Validation:** the corpus-wide parse test (`packages/compiler/tests/test_calc.py`) and the TDVT gate (`packages/compiler/tests/test_tdvt.py`) stay green as the corpus grows in P1.4 and P2.1.
- **Revisit if:** Tableau publishes a production grammar, or more than a handful of real workbook formulas fail to parse for syntax reasons.

## References

- [ADR-003](0003-own-tableau-semantics-compiler.md), decision item 1
- [BUILD_PLAN.md](../BUILD_PLAN.md), P1.10 and P2.2
- `third_party/tableau-calc-grammar/` (vendored grammar and its provenance)
- [Tableau operator precedence](https://help.tableau.com/current/pro/desktop/en-us/functions_operators.htm)
