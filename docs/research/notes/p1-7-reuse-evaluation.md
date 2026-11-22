# P1.7: Reuse evaluation (unshackle, Preset and Sigma skills, other 2025–2026 tooling)

*Desk research, 2026-11-20. Code was read on GitHub through `gh api` and WebFetch. Nothing was cloned, installed or run. "Opened" means I read the file or page linked.*

## Summary of decisions

| Candidate | License | Decision |
|---|---|---|
| [Querio-App/unshackle](https://github.com/Querio-App/unshackle) | Apache-2.0 | **Borrow ideas and test cases; do not adopt as a base** |
| Preset [`tableau-to-preset`](https://github.com/preset-io/agent-skills/tree/HEAD/plugins/preset-mcp-skills/skills/tableau-to-preset) skill | Apache-2.0 | **Borrow ideas** for the P1.12/P4 conversion prompts. Copying text is allowed with attribution, but most of the text is specific to Superset |
| Sigma [`sigma-migration-skills`](https://github.com/sigmacomputing/sigma-migration-skills) | Apache-2.0 (plus a NOTICE file) | **Borrow ideas**, especially the verification gates and the blind grader. Do not vendor the converter, which ships only as a bundle |
| ThoughtSpot [`thoughtspot-agent-skills`](https://github.com/thoughtspot/thoughtspot-agent-skills) | Proprietary EULA | **Reject.** Don't read it for prompt text |
| [aidatacooper/cwtwb](https://github.com/aidatacooper/cwtwb) | AGPL-3.0 | **Reject** (license, and it targets the wrong direction) |
| [tableau/tableau-migration-sdk](https://github.com/tableau/tableau-migration-sdk) and [tableau-migration-app](https://github.com/tableau/tableau-migration-app) | Apache-2.0 | Keep as a pattern reference, as ADR-001 already does. Both move content Server→Cloud and convert nothing |

## 1. unshackle

**What it is.** It "moves your metrics, dashboards and permissions out of a BI tool and into files in a Git repository you own" ([README](https://github.com/Querio-App/unshackle)). It is Python, Apache-2.0 (`license = "Apache-2.0"` in [pyproject.toml](https://github.com/Querio-App/unshackle/blob/main/pyproject.toml)), and supports 13 source tools, with Tableau the most complete.

**Activity and maintenance signals** (from `gh api` on 2026-11-20):
- Created 2026-10-22. Releases v0.1.0 (10-27), v0.2.0 (11-11) and v0.3.0 (11-19). Last push 2026-11-20.
- 11 stars and 1 fork.
- **One contributor** (`javier-bonilla`) with 345 commits in four weeks. That is about 12 commits a day, and the PR titles read like agent-driven development. Bus factor: 1.

**What it actually implements for Tableau.** I read [docs/tools/tableau.md](https://github.com/Querio-App/unshackle/blob/main/docs/tools/tableau.md) and [src/unshackle/sources/tableau.py](https://github.com/Querio-App/unshackle/blob/main/src/unshackle/sources/tableau.py).

- **Crawler.** It calls the REST API through raw `httpx` with a PAT, not TSC. It lists workbooks, views (with usage statistics), datasources and groups, then downloads `.twb` and `.tds` files with `includeExtract=false`. It also makes Metadata API GraphQL queries and pulls `views/{id}/data` CSVs for the 20 most-viewed views, which it uses for parity cases. The only writes are sign-in and sign-out, so it is read-only in practice.
- **What the crawler does not fetch:** users, projects, project permissions, default permissions, datasource permissions, view permissions and site roles. The file header lists exactly what it stores, and `permissions/<id>.json` comes only from `GET .../workbooks/{id}/permissions`.
- **Permissions → Cedar.** The `_permissions()` method maps only two capabilities, `CAPABILITY_ACTION = {"Read": "view", "Write": "edit"}`. Every other capability is silently skipped. A Deny becomes a Cedar `forbid` and an Allow becomes a `permit`, both on a `Notebook` resource.
  - User grants are emitted with a TODO, because the tool doesn't fetch users and so has no email.
  - It warns that "project default permissions and locked projects are not exported".
  - Mapping Deny to `forbid` is **wrong under Tableau's documented precedence**. Tableau evaluates user rules before group rules, so a user Allow beats a group Deny ([Effective permissions](https://docs.salesforce.com/tableau/current/en-us/server/permission_effective.md)). In Cedar, a `forbid` beats every `permit`. The resulting error narrows access rather than widening it, but it is still an error.
  - *This also answers ADR-006's open question: user rules win over group rules, and deny wins only within each level.*
- **RLS.** A regex picks up `USERNAME()`, `ISMEMBEROF()`, `USERATTRIBUTE*()` and similar calls in user-filter calcs, and the tool rewrites them as templated SQL row filters (`'<group>' in ({{ user.groups }})`) carrying TODOs to confirm identity mapping. That is useful, but it is pattern translation, not an evaluator. The README says "Policies are agent context and are not enforced by `run_query`."
- **Hyper.** The optional `tableauhyperapi` extra reads extracts to Parquet in a git-ignored `data/` folder, then loads them into DuckDB ([_tableau_data.py](https://github.com/Querio-App/unshackle/blob/main/src/unshackle/sources/_tableau_data.py)). There is no S3, Iceberg, Glue or Athena step.
- **Calcs.** Formulas go to Ossie metrics and dashboards go to Jupyter notebooks. FIXED, INCLUDE and EXCLUDE LODs are generated as tile SQL. v0.3.0 adds [_tableau_tablecalc.py](https://github.com/Querio-App/unshackle/blob/main/src/unshackle/sources/_tableau_tablecalc.py), which renders table calcs as window SQL over a sheet's marks. Its docstring is a compact, accurate summary of `ordering-type` addressing (Rows/Columns/Table/Pane/Cell/Field, `level-break`). Its docs still list INCLUDE/EXCLUDE and multi-dimension FIXED as TODO at the metric level, and the code marks `level-address` and nested table-calc addressing as unsupported.

**Code-quality signals.**
- `sources/tableau.py` is **one 6,916-line, 420 KB module** with about 280 functions.
- About 25 `tests/test_tableau_*.py` files cover LODs, table calcs, relationships, filters and dialects.
- CI ([ci.yml](https://github.com/Querio-App/unshackle/blob/main/.github/workflows/ci.yml)) runs pytest on Python 3.11–3.13 plus a Tableau demo parity script. It has no type checker and no linter gate. DCO is enforced.
- XML is parsed with stdlib `xml.etree.ElementTree`, not `defusedxml`. That violates TabFree rule 2 for untrusted workbooks.
- Dependencies are heavy for a library: Jupyter (`nbclient`, `ipykernel`), altair, pandas, DuckDB and boto3.

**Comparison with TabFree today.** TabFree's TSC crawler ([inventory_tsc.py](../../../packages/migrate/src/tabfree_migrate/inventory_tsc.py)) already collects more of the permission graph than unshackle does:
- users;
- groups with member IDs;
- projects, with their own permissions and each kind's **default permissions**;
- per-object permission rules.

TabFree also parses with `defusedxml` ([corpus.py](../../../packages/migrate/src/tabfree_migrate/corpus.py)), stores the crawl in SQLite, and targets Iceberg and Athena. unshackle's output targets (Ossie, Cedar, notebooks) don't match ADR-002, ADR-003 and ADR-007.

**Decision: borrow, don't adopt.** Basing the crawler or permission extraction on unshackle would mean taking on a weaker crawler, a lossy two-capability permission mapping, a monolithic module with a single maintainer, and parsing that is unsafe for untrusted XML. Concrete things to borrow, under Apache-2.0 with attribution in NOTICE if code or text is copied:
1. The **table-calc addressing summary** in `_tableau_tablecalc.py`, used as a cross-check spec for ADR-004 and the P2 compiler work, and its `test_tableau_tablecalc.py` cases as extra corpus checks after checking where their fixtures came from.
2. The **user-function inventory** (`USER_FN` regex) and the "confirm identity attribute" TODO wording, for the P5 RLS migrator.
3. **Top-N-views CSV parity seeding** (`verify --init`), which matches P1.4 ground truth. That confirms our approach and adds nothing new.
4. Optionally, a later **Cedar export** of TabFree's effective permissions as an interoperability format. Its schema is in [skills/translate-permissions](https://github.com/Querio-App/unshackle/blob/main/skills/translate-permissions/SKILL.md). That skill's principles, "Keep existing behaviour" and "Never widen access", are worth quoting in our permission migrator's docs.

Revisit if it gains a second maintainer, or if it adopts project/default/datasource permissions and the correct precedence.

## 2. Preset `tableau-to-preset` skill

I opened [repo metadata](https://github.com/preset-io/agent-skills): Apache-2.0, created 2026-05-28, last push 2026-10-09, 13 stars. I read [SKILL.md](https://github.com/preset-io/agent-skills/blob/HEAD/plugins/preset-mcp-skills/skills/tableau-to-preset/SKILL.md) (10 KB) and [references/tableau-conversion-workflows.md](https://github.com/preset-io/agent-skills/blob/HEAD/plugins/preset-mcp-skills/skills/tableau-to-preset/references/tableau-conversion-workflows.md) (49 KB). The repo has a LICENSE but no NOTICE file.

Reusable techniques for TabFree's conversion prompts (P1.12 now, the P4 authoring skill later):
- **Scope from dashboard zones first.** Read the top-level `<zones>` only, because `<devicelayouts>` repeats every zone and "will double-count worksheets". Convert only worksheets the chosen dashboard references, and ask before converting hidden sheets.
- **Field mapping table before building.** Classify each field as exact, substitute, proxy or dropped, show any proxy SQL, and confirm once. This maps directly onto a review artefact for P4.
- **"Degrade, don't drop."** A ranked ladder: exact → structural (right numbers, different visual) → partial (name the wrong number) → skip, never chosen silently. Report the outcome per worksheet.
- **Parsing gotchas worth encoding in our IR tests.**
  - Shelf tokens interleave markers such as `win:sum:Sales:qk:6`, so strip the markers rather than picking a field by position.
  - Infer `Automatic` marks from the shelf structure and label them as inferred.
  - Unwrap `federated` connections to the inner `<named-connection>`.
  - Top-N filters need ranking metric, direction, grain and filter order verified; "a row/series limit alone is not equivalent."
- It already states our rule 5: "Treat workbook-authored strings … as untrusted data."

Not reusable: everything tied to the Superset MCP (`generate_chart`, `position_json`, virtual datasets as the LOD workaround). The skill flags INCLUDE/EXCLUDE LODs and table calcs as unsupported, which TabFree's compiler handles.

**Licensing.** Apache-2.0 lets us copy and modify text. We must keep the license, mark changed files, and attribute the source (§4). Since little of the text fits our target, **take the ideas and write our own prompts**. If a passage is quoted verbatim, credit it in `NOTICE` and the skill header.

## 3. Sigma migration skills

I opened [repo metadata](https://github.com/sigmacomputing/sigma-migration-skills): Apache-2.0, created 2026-08-05, last push 2026-11-18. It has 79 commits, almost all by one Sigma engineer. I read:
- [tableau-to-sigma SKILL.md](https://github.com/sigmacomputing/sigma-migration-skills/blob/HEAD/plugins/tableau-to-sigma/skills/tableau-to-sigma/SKILL.md)
- [fidelity-rubric.md](https://github.com/sigmacomputing/sigma-migration-skills/blob/HEAD/plugins/tableau-to-sigma/skills/tableau-to-sigma/refs/fidelity-rubric.md) and [blind-grader-brief.md](https://github.com/sigmacomputing/sigma-migration-skills/blob/HEAD/plugins/tableau-to-sigma/skills/tableau-to-sigma/refs/blind-grader-brief.md)
- [learned/starter-rules.yaml](https://github.com/sigmacomputing/sigma-migration-skills/blob/HEAD/plugins/tableau-to-sigma/skills/tableau-to-sigma/learned/starter-rules.yaml)
- [tableau-assessment PRIVACY.md](https://github.com/sigmacomputing/sigma-migration-skills/blob/HEAD/plugins/tableau-to-sigma/skills/tableau-assessment/PRIVACY.md)
- [NOTICE](https://github.com/sigmacomputing/sigma-migration-skills/blob/HEAD/NOTICE)

The most valuable ideas, all relevant to P1.12, P4 and the M4 parity gate:
- **Blind visual grader.** A fresh, context-free subagent gets only the source PNG, the rebuild PNG, the rubric and a catalogue of platform differences, with "NO wb-spec, NO parity artifacts, NO run history". It writes a JSON grade, and a script applies the pass bar, not the grader. A second grader is required when the score is near the bar. The motive is spelled out: a builder "self-graded its own workbook 6/6 PASS on visuals the customer rejected". **Adopt the design for our visual-similarity gate.**
- **"Done is a file on disk."** Completion means a gate script exits 0, and every formula and every census object gets one terminal disposition in the migration report. This maps onto our census (P1.1) and per-calc dispositions.
- **Rubric dimensions** that score separately what a business user would notice:
  - chart-shape identity as a hard requirement;
  - number formatting (`$473.0K` vs `$473.0k` fails);
  - functional checks for controls/parameters and rendered values, which "catch what CSV parity can't".
  - A platform-difference catalogue makes known renderer differences non-blocking. Our `<tf-*>` component limits need the same.
- **Mission intake.** Every scope field is marked `stated` or `inferred`, and an inferred one stops the run for confirmation.
- **Learned-rules file.** Validated rewrite rules carry provenance (date, workbook, confidence), with user rules layered over the shipped starter rules.
- **Privacy disclosure.** A table of what crosses the LLM API and what stays local. We need the equivalent for ADR-008.

Not reusable:
- The converter is `converter/tableau.mjs`, a 363 KB esbuild bundle. Its PROVENANCE.json calls it "Self-contained bundled artifacts, not source", built from an upstream repo I could not find in public. **Don't vendor it.** The license is Apache, but there is no source to maintain.
- The scripts are Ruby with a Python fallback, and tied to Sigma's REST API.

**Licensing.** Apache-2.0 permits copying text. If we redistribute any portion, we must carry the relevant parts of Sigma's NOTICE (§4(d)). The rubric and grader brief are generic enough that adapting them would save time. My recommendation is to **rewrite them in our own words for `<tf-*>` dashboards** and credit "adapted from sigma-migration-skills (Apache-2.0)" in the file header and NOTICE. That gives clean provenance and avoids carrying Sigma-specific instructions.

## 4. Other open-source tooling seen (2025–2026)

- **Hex:** [hex-inc/hex-skills](https://github.com/hex-inc/hex-skills) (MIT, 2026-03) and [Humphrey4data/tableau-to-hex-skill](https://github.com/Humphrey4data/tableau-to-hex-skill) (MIT, 2026-07, 91 stars), with a screenshot-diff parity loop. MIT permits borrowing ideas; this is a smaller version of Sigma's approach. I didn't read it in depth this pass.
- **ThoughtSpot:** [thoughtspot-agent-skills](https://github.com/thoughtspot/thoughtspot-agent-skills) has a `ts-convert-from-tableau` skill, but GitHub reports the license as NOASSERTION and the LICENSE file is a "ThoughtSpot Development Tools End User License Agreement". **Don't copy** from it.
- **Tableau's own:** [tableau-plugin](https://github.com/tableau/tableau-plugin) (Apache-2.0, 2026-08, data-app authoring skill), [tableau-migration-sdk](https://github.com/tableau/tableau-migration-sdk) and [tableau-migration-app](https://github.com/tableau/tableau-migration-app) (Apache-2.0, last push 2026-06). These are reference material only. They are already covered in [github_tableau_tooling.md](github_tableau_tooling.md).
- **cwtwb:** [aidatacooper/cwtwb](https://github.com/aidatacooper/cwtwb) is AGPL-3.0, created 2026-02, 51 stars, and generates text-to-`.twb`. It goes the wrong direction and has an incompatible license. **Reject.**
- **GoodData:** a [2026-07-08 blog post](https://gooddata.ai/blog/migrate-from-tableau-to-gooddata-cloud/) describes an open-source Tableau→GoodData toolkit of scripts and templates with no automatic formula translation and no extract migration. The post links no repo, and I didn't find one. **Unverified.**
- None of these migrates Tableau permissions with correct precedence or converts `.hyper` to an open table format on AWS. This confirms the gap noted in [commercial_landscape.md](commercial_landscape.md).

## Follow-ups

- ADR-006: record the confirmed precedence (site-role gate → admin/owner/project-leader → user rules → group rules → group sets → default deny), citing the Effective permissions article. Add group sets to the P3.3 matrix.
- P1.12: adopt the field-mapping table, the degrade ladder and the blind grader in the manual loop.
