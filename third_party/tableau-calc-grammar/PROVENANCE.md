# Provenance: tableau-calc-grammar

These are Tableau's ANTLR 4 grammars for its calculation language. They come from Tableau's AI model evaluation tool and are vendored unmodified. BUILD_PLAN P2.2 uses `TableauCalculationGrammar.g4` as the basis of the calc parser.

| | |
|---|---|
| Upstream | <https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool> |
| Licence | Apache-2.0. Checked three ways: the GitHub licence API, the `LICENSE.txt` text (copied unchanged), and the upstream README's "License" section ("Apache-2.0. See LICENSE.txt"). Upstream has no `NOTICE` file. |
| Branch | `main` |
| Commit | `6480355e81738ab341af3482a983581b55f8be07` (committed 2026-11-06T21:08:54Z) |
| Fetched | 2026-11-20, read-only through the GitHub API (`gh api .../contents/<path>?ref=<commit>`) |
| Modifications | None. Files are byte-identical to upstream, checked against their git blob SHAs. Upstream directories were flattened, as mapped below. |

## Files

| Vendored path | Upstream path | Bytes | SHA-256 |
|---|---|---|---|
| `TableauCalculationGrammar.g4` | `packages/evals/src/validator/grammar/TableauCalculationGrammar.g4` | 8285 | `ec9949c69df390648d1ef82d83e100325c3b1490ddf8a31b1909ce94ce017ba1` |
| `TableauExpression.g4` | `packages/evals/src/prep/grammar/TableauExpression.g4` | 13888 | `e91b6d57347a1ed2af1a0a431424127a4655715db6c51564bb25cce1e9f53b3d` |
| `CALC_GRAMMAR.md` | `docs/CALC_GRAMMAR.md` | 3724 | `4628db01e479057cd81fb5e570ce18184ee9ab59074eae7b8507c799bacd9818` |
| `LICENSE.txt` | `LICENSE.txt` | 11444 | `7f32b32434490332df34d81b4b91f310f5885920ecb9890774b38b772f08c660` |

## What each grammar is

- **`TableauCalculationGrammar.g4`** is the grammar for calculated fields in Tableau Desktop and Server. Its header reads "Calculation grammar based on work by Dan Cory"; keep that attribution. Upstream uses it to validate calculated fields: it fixes tokenization, operator precedence and syntax, including `{FIXED | INCLUDE | EXCLUDE ...}` LOD expressions. Use it for the TabFree calc parser.
- **`TableauExpression.g4`** is the Tableau Prep editor grammar. It deliberately accepts incomplete constructs, such as unfinished LOD statements, so that the editor can give feedback while the user types. It can't stand in for the calculated-field grammar. It is here for reference only.
- **`CALC_GRAMMAR.md`** is upstream's own note on the two grammars and how it generates parsers from them. Upstream generates TypeScript with `antlr-ng`; TabFree can generate for any ANTLR 4 target.

## Updating

To update:
1. Fetch the new grammar at a pinned commit and check its git blob SHA.
2. Replace the file here and update the table above and `third_party/README.md`.
3. Regenerate any parser built from it.

Never edit the grammars here by hand. Keep TabFree's own grammar changes in TabFree's source tree, and diff them against this baseline.
