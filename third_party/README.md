# third_party

This folder holds code and specifications vendored unmodified from other projects. Every file is byte-identical to its upstream copy at the pinned commit. Don't edit these files by hand: change TabFree's own code instead, or update the vendored copy as described in its `PROVENANCE.md`.

| Directory | What | Upstream | Commit | Licence |
|---|---|---|---|---|
| [`tableau-document-schemas/`](tableau-document-schemas/PROVENANCE.md) | Tableau's official workbook (`.twb`) XSDs for 2026.1, 2026.2 and 2026.3 | [tableau/tableau-document-schemas](https://github.com/tableau/tableau-document-schemas) | `b01c07d0192c2d5ad67e8c48dd3b29b1be5e52f0` (2026-11-03) | Apache-2.0 |
| [`tableau-calc-grammar/`](tableau-calc-grammar/PROVENANCE.md) | ANTLR 4 grammars for the Tableau calculation language: `TableauCalculationGrammar.g4` (calculated fields) and `TableauExpression.g4` (Prep editor) | [tableau/Tableau-AI-Model-Evaluation-Tool](https://github.com/tableau/Tableau-AI-Model-Evaluation-Tool) | `6480355e81738ab341af3482a983581b55f8be07` (2026-11-06) | Apache-2.0 |

Both were fetched on 2026-11-20. Each directory holds:
- the upstream `LICENSE.txt`. Neither upstream repository has a `NOTICE` file.
- a `PROVENANCE.md` that maps each file to its upstream path and gives its size and SHA-256.

TabFree is Apache-2.0, so redistributing these files only requires keeping their licence texts and attributions, which these directories do.

Uses in the build plan:
- **P2.1:** the TWB/TDS parser validates against the XSDs and keeps a list of known deviations.
- **P2.2:** the calc parser is generated from `TableauCalculationGrammar.g4`.

Tableau's test files (workbooks, data sources, extracts and TDVT) are in [`../corpus/`](../corpus/README.md), not here.

`.gitattributes` turns off line-ending conversion here, so the files keep their upstream bytes and the recorded SHA-256 values stay valid on every platform.
