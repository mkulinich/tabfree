# corpus

This is TabFree's test corpus: Tableau files with permissive licences, copied unchanged from Tableau's official GitHub repositories (`tableau/*`). It feeds four parts of the build plan:

- the TWB/TDS parser (P1.8, P2.1)
- the extract converter (P1.9, P2.8)
- the calc parser and compiler (P2.2, P2.3)
- the conformance suites (P2.9)

`manifest.json` records the provenance, licence and checksum of every file.

**Files are untrusted.** Parse XML only with `defusedxml`. Read `.twbx`/`.tdsx` members in memory, with size limits. Never execute anything from this folder, and never put code in it.

## Contents

996 files, 40,126,091 bytes (about 40 MB), from 24 repositories. Assembled on 2026-11-20.

| Kind | Files | MB | What |
|---|---:|---:|---|
| `twb` | 59 | 21.33 | workbook XML |
| `twbx` | 39 | 12.46 | packaged workbooks; 32 of them contain `.hyper`/`.tde` extracts (see `packaged_extracts` in the manifest) |
| `tds` | 21 | 0.50 | data source XML |
| `tdsx` | 4 | 0.29 | packaged data sources |
| `hyper` | 5 | 1.64 | Hyper extracts |
| `tde` | 3 | 0.94 | legacy extracts |
| `tdvt-expr` | 302 | 0.06 | TDVT expression tests |
| `tdvt-expected` | 552 | 2.79 | TDVT expected results |
| `tdvt-data` | 9 | 0.02 | TDVT TestV1 data (`Calcs.csv`) and the DDL/load scripts |
| `other` | 2 | 0.09 | TDVT test metadata (categories, functions, priorities) |

The licence files in `sources/` and the manifest add about 0.4 MB.

That makes 123 workbook and data-source documents (98 with a `<workbook>` root, 25 with a `<datasource>` root). Between them they cover:

- file-format versions 8.1, 8.2, 8.3, 9.0, 9.2, 9.3, 10.0 to 10.5, and 18.1
- `source-build` values from Tableau 9.3 to 2026.2, plus synthetic `0.0.0` builds

Highlights:
- Real-world Postgres/custom-SQL workbooks: `community-tableau-server-insights`, with four versions of each data source.
- Server-log dashboards with packaged Hyper extracts: Logshark.
- Workbooks for the TabMon, VizAlerts, TabMigrate and log-viewer tools.
- Superstore data apps and extension samples.
- Maps: `uaf-test-app`.
- The TDVT results workbook and data sources.
- Tableau's own Document API and TSC test assets.

## Layout

```
corpus/
  manifest.json                     provenance + checksums for every file below
  workbooks/<owner>__<repo>/<upstream path>     .twb, .twbx
  datasources/<owner>__<repo>/<upstream path>   .tds, .tdsx
  extracts/<owner>__<repo>/<upstream path>      .hyper, .tde
  tdvt/
    exprtests/<suite>/setup.*.txt               expression tests     (upstream tdvt/tdvt/exprtests/)
    exprtests/<suite>/expected.setup.*.txt      expected results
    metadata/test_metadata.csv, priority.csv    test -> category/function/priority (upstream tdvt/tdvt/metadata/)
    datasets/TestV1/Calcs.csv, DDL/, postgres/  test data and load scripts (upstream tests/datasets/TestV1/)
  sources/<owner>__<repo>/LICENSE[.txt], NOTICE  licence files copied from each upstream repo
  private/                                       customer workbooks: gitignored, NEVER commit
```

- Upstream relative paths are kept, so names may contain spaces and `[...]`.
- Licence files keep their upstream names (`LICENSE` or `LICENSE.txt`). Sub-directory licences keep their paths too, for example `sources/tableau__connector-plugin-sdk/tdvt/LICENSE.txt`.

## Provenance policy

1. **Official Tableau repositories with verified permissive licences only.** A repository qualifies only if it is in the `tableau` GitHub organisation and has an OSI-permissive licence: MIT, Apache-2.0, BSD-2-Clause or BSD-3-Clause.
   - The licence must be confirmed twice: by the GitHub licence API and by reading the licence text.
   - Sub-directory licences that cover the copied files were checked as well.
   - Repositories with no licence, or with `NOASSERTION`, are excluded.
2. **Pinned and byte-identical.** Each file was fetched read-only at a pinned commit through `gh api .../contents/<path>?ref=<sha>`.
   - Each was checked against its git blob SHA from the commit's tree.
   - Nothing was edited. `.gitattributes` turns off line-ending conversion, so the SHA-256 values stay valid.
3. **Licence texts travel with the files.** Each upstream's licence (and `NOTICE`, if present) is in `sources/<owner>__<repo>/`. The manifest's `sources[]` names those licence files.
4. **Size limits.**
   - No file over 10,000,000 bytes. Bigger files are listed under `external` in the manifest, with a pinned URL.
   - The whole corpus stays under about 60 MB.
   - Identical files are stored once, and the other upstream locations are listed in `also_in`.
5. **Customer workbooks go in `corpus/private/` only.** This covers the "about 20 sanitised workbooks from our own server" in P0.5, and anything else that is not openly licensed.
   - `corpus/private/` is gitignored, both in the root `.gitignore` and in `corpus/.gitignore`.
   - Never commit it, copy it elsewhere in the repository, or list it in `manifest.json`.

## Sources

| Repository | Licence | Commit (date) | Files | MB |
|---|---|---|---|---:|
| [tableau/Logshark](https://github.com/tableau/Logshark) | MIT AND Apache-2.0 ¹ | `4369ab757857` (2026-11-11) | 20 twbx | 2.98 |
| [tableau/TabMigrate](https://github.com/tableau/TabMigrate) | MIT | `dc71b3ccdf8b` (2020-12-05) | 1 twb | 0.23 |
| [tableau/TabMon](https://github.com/tableau/TabMon) | MIT | `a1196ef05d87` (2020-08-17) | 4 twb | 2.73 |
| [tableau/Visualization-Linting](https://github.com/tableau/Visualization-Linting) | MIT | `a7092e760a7f` (2024-12-12) | 2 twb, 1 twbx | 0.96 |
| [tableau/VizAlerts](https://github.com/tableau/VizAlerts) | MIT | `7da5975b6184` (2023-10-10) | 3 twb, 2 tde | 2.53 |
| [tableau/community-tableau-server-insights](https://github.com/tableau/community-tableau-server-insights) | MIT | `c31e5650aed4` (2026-06-02) | 24 twb | 13.44 |
| [tableau/connector-plugin-sdk](https://github.com/tableau/connector-plugin-sdk) | MIT | `1f9571b2503c` (2026-07-21) | 552 tdvt-expected, 302 tdvt-expr, 9 tdvt-data, 7 tds, 2 other, 1 hyper, 1 twb | 3.43 |
| [tableau/custom-portal-sample](https://github.com/tableau/custom-portal-sample) | MIT | `944e91ef432a` (2017-10-06) | 1 twbx | 0.22 |
| [tableau/datadev-hackathon](https://github.com/tableau/datadev-hackathon) | MIT | `437061a2dbae` (2021-03-01) | 2 twb, 1 twbx | 0.23 |
| [tableau/document-api-python](https://github.com/tableau/document-api-python) | MIT | `447357fc1ad2` (2026-07-02) | 11 twb, 8 tds, 2 twbx, 1 tdsx | 0.74 |
| [tableau/e-bikes-demo](https://github.com/tableau/e-bikes-demo) | MIT | `33425fd97252` (2026-06-02) | 1 twbx | 0.09 |
| [tableau/extension-hierarchy-navigator-sandboxed](https://github.com/tableau/extension-hierarchy-navigator-sandboxed) | MIT | `060d544fc385` (2022-06-09) | 3 twbx | 4.53 |
| [tableau/extensions-api](https://github.com/tableau/extensions-api) | Apache-2.0 | `738f6b89c705` (2026-10-29) | 1 twb | 0.21 |
| [tableau/hyper-api-samples](https://github.com/tableau/hyper-api-samples) | MIT | `28b90838fae0` (2026-03-17) | 3 hyper | 1.38 |
| [tableau/js-api-samples](https://github.com/tableau/js-api-samples) | MIT | `4e6d23de3d38` (2023-03-18) | 1 twbx | 0.09 |
| [tableau/server-client-python](https://github.com/tableau/server-client-python) | MIT | `02cd0df938b0` (2026-11-13) | 2 tds, 2 twbx, 1 tdsx, 1 tde, 1 hyper, 1 twb | 0.88 |
| [tableau/tab-compare](https://github.com/tableau/tab-compare) | MIT | `5d7099d43af4` (2022-12-01) | 1 twbx | 0.04 |
| [tableau/tab-s3-converter](https://github.com/tableau/tab-s3-converter) | Apache-2.0 | `06902b036c09` (2026-10-23) | 2 twb, 1 twbx | 0.19 |
| [tableau/tabcloud_platform_data_loader](https://github.com/tableau/tabcloud_platform_data_loader) | Apache-2.0 | `7b1b81d81cf4` (2026-08-11) | 1 tds | 0.06 |
| [tableau/tabcmd](https://github.com/tableau/tabcmd) | MIT | `30243d46eb18` (2026-11-02) | 3 tds, 1 tdsx, 1 twb, 1 twbx | 0.27 |
| [tableau/tableau-log-viewer](https://github.com/tableau/tableau-log-viewer) | Apache-2.0 | `0996ddb9e2ad` (2026-06-25) | 2 twb | 0.26 |
| [tableau/tableau-mcp](https://github.com/tableau/tableau-mcp) | Apache-2.0 | `15c18397c9f8` (2026-11-20) | 3 twb, 1 tdsx, 1 twbx | 0.88 |
| [tableau/tableau-plugin](https://github.com/tableau/tableau-plugin) | Apache-2.0 | `0864ec2b85a1` (2026-11-20) | 1 twb | 0.00 |
| [tableau/uaf-test-app](https://github.com/tableau/uaf-test-app) | Apache-2.0 | `6ad5c98f5ee0` (2023-03-24) | 3 twbx | 3.76 |

¹ Logshark's root has two licence files:
- `LICENSE`: MIT, 2016.
- `LICENSE.txt`: Apache-2.0, added on 2026-06-25 in "Add Salesforce Open Source Template files".

Both are permissive. Both are copied, and we comply with both.

Full SHAs are in `manifest.json` (`sources[]` and each file's `source_commit`). `tableau/rest-api-samples` (MIT) is also mentioned there: its `World Indicators-En-US.twbx` is byte-identical to a server-client-python file, so it appears only as `also_in`.

## TDVT

TDVT (Tableau Datasource Verification Tool) comes from `tableau/connector-plugin-sdk` (MIT). This corpus has 854 of TDVT's 855 expression-test files: 302 tests and 552 expected results.

- **Format.**
  - `setup.<name>.txt` holds one Tableau calculation per line.
  - `expected.setup.<name>.txt` holds the expected results as `<results><test name='<calc>' tolerance=...><table><schema>...<tuple><value>`.
  - `expected.setup.<name>.N.txt` holds alternative results that are also accepted, for example a different NULL or float rendering.
- **Suites.**
  - `standard`: 231 tests, 468 expected. This is the core suite against Calcs.
  - Smaller suites: `iso8601week`, `regexcalcs`, `tdeonly`, `dateparse`, `staples`, `bigquery`, `snowflake`, `bind`, `initialsql`, `median`, `percentile`, `lodcalcs`.
  - `pretest` and `drivertest` are connectivity checks, not calc semantics.
- **Data.**
  - Most suites run against the 17-row `Calcs` table: `tdvt/datasets/TestV1/Calcs.csv` (with a headers variant) and `DDL/Calcs.sql`.
  - The Hyper copy is `extracts/tableau__connector-plugin-sdk/tdvt/test/tool_test/tds/Calcs (TestV1).hyper`.
  - TDVT's `.tds` definitions are under `datasources/tableau__connector-plugin-sdk/tdvt/` (`cast_calcs.*`, `Staples.*`).
- **Staples data is external.** The `staples` suite and a few others need the Staples table. `Staples_utf8.csv` (24 MB) and the 10 MB `expected.setup.staples_data.txt` are listed under `external` instead, so `setup.staples_data.txt` has no expected file in the corpus. Their DDL and load scripts are included.
- **Metadata.** `tdvt/metadata/test_metadata.csv` (tab-separated) maps each test to a category, a function and a priority. It is useful for per-function pass rates.

## manifest.json

Top-level keys: `manifest_version`, `generated` (UTC ISO 8601), `description`, `kinds`, `summary`, `sources[]`, `files[]`, `external[]`, `excluded[]`.

Each `files[]` entry has:
- `path`: repo-relative, with forward slashes
- `kind`: `twb | twbx | tds | tdsx | hyper | tde | tdvt-expr | tdvt-expected | tdvt-data | other`
- `source_repo`, `source_path`, `source_commit`, `source_url`, `git_blob_sha`
- `license`: an SPDX expression
- `sha256`, `bytes`

Optional fields:
- **`twb`/`tds`:** `root_tag`, plus `source_build`, `version` and `original_version` when the root has them, and `well_formed`. `parse_error` appears when the file does not parse.
- **`twbx`/`tdsx`:** the same fields, read from `inner_document` (the shallowest `.twb`/`.tds` in the zip, as the Document API does). Optionally `other_inner_documents` and `packaged_extracts` (each with `path` and `bytes`).
- **TDVT files:** `tdvt_suite`.
- **Duplicates:** `also_in` lists other upstream locations of an identical file.
- **Deliberately invalid files:** `expect_invalid: true`, with `expect_invalid_reason`. Treat a parse failure on these as the expected result. Two upstream negative fixtures from `tableau/tableau-mcp` are marked:
  - `tests/e2e/fixtures/workbooks/badtwbx.twbx`
  - `tests/e2e/fixtures/workbooks/malformed-datasource.twb`

  Every other document is well-formed. The regeneration script fails if `well_formed` and `expect_invalid` disagree.

`external[]` lists files that were deliberately not copied, with `url`, `raw_url` (pinned to the commit), `git_blob_sha`, `bytes` and `reason`. `excluded[]` lists files rejected for licence reasons.

## Adding files and regenerating the manifest

1. **Check the licence.**
   - `gh api repos/OWNER/REPO/license --jq '.license.spdx_id,.path'`, then read the text.
   - Check for sub-directory licences or a `NOTICE`.
   - If it is not MIT, Apache-2.0 or BSD, don't add the file. Record it under Excluded below.
2. **Pin and fetch.**
   - `sha=$(gh api repos/OWNER/REPO/commits/BRANCH --jq .sha)`.
   - `gh api -H "Accept: application/vnd.github.raw" "repos/OWNER/REPO/contents/PATH?ref=$sha" > file`.
   - Download into a scratch directory, not into the repository. Compare the file to the blob SHA from `gh api "repos/OWNER/REPO/git/trees/$sha?recursive=1"`.
3. **Copy it in.** Put the file at `<area>/<owner>__<repo>/<upstream path>`. For a new repository, also:
   - copy its `LICENSE`/`NOTICE` into `sources/<owner>__<repo>/`
   - add a `sources[]` entry with `repo`, `url`, `branch`, `commit`, `commit_date`, `license` and `license_files`
4. **Write the entry.** Add a `files[]` entry with `path`, `kind`, `source_repo`, `source_path`, `source_commit`, `license`, `source_url` and `git_blob_sha`. Add `expect_invalid` if the file is a deliberate negative case.
5. **Regenerate.** Run the script below. Keep it outside `corpus/`, for example as `tools/corpus_manifest.py`.
   - `uv run --no-project --with defusedxml python -I tools/corpus_manifest.py corpus` fills in `sha256`, `bytes`, the document fields and the totals.
   - `... --check corpus` changes nothing and exits 1 if:
     - a file has no entry, or an entry has no file
     - provenance is missing
     - a file no longer matches its checksum
     - `well_formed` contradicts `expect_invalid`

     CI should run it.

   Note: pre-commit hooks that rewrite files (end-of-file, trailing whitespace, line endings) or validate XML must exclude `corpus/` and `third_party/`.
6. Update the counts in this README.

## External (not copied)

| Upstream file | Bytes | Reason |
|---|---:|---|
| tableau/document-api-python `samples/show_workbook_info/geocoding.twbx` | 68,256,596 | over 10 MB |
| tableau/tableau-mcp `tests/e2e/fixtures/workbooks/forecast.twbx` | 21,286,080 | over 10 MB |
| tableau/connector-plugin-sdk `tests/datasets/TestV1/Staples_utf8.csv` | 24,300,478 | over 10 MB (Staples TDVT data) |
| tableau/connector-plugin-sdk `tests/datasets/TestV1/Staples_utf8_headers.csv` | 24,301,237 | over 10 MB |
| tableau/connector-plugin-sdk `tdvt/tdvt/exprtests/staples/expected.setup.staples_data.txt` | 10,132,967 | over 10 MB |
| tableau/connector-plugin-sdk `tests/datasets/TestV1/Batters.csv`, `Batters_headers.csv` | 9.4 MB each | not used by the expression tests |

Pinned URLs are in `manifest.json` → `external`.

Files that are permissively licensed but were not copied because they are redundant or out of scope:
- **LogShark end-to-end test outputs** (`LogShark.Tests/TestData/EndToEndTests/Expected/`): 40 `.twbx` and 100 `.hyper`. These are generated copies of the 20 `LogShark/Workbooks` templates, which are included.
- **14 byte-identical duplicates.** These are recorded as `also_in`.
- **Tableau chart templates** (`.tbm` bookmarks): 137 in `tableau-mcp/src/desktop/data/templates`, about 14 MB, and 135 in `tableau-plugin`. They have a `<bookmark>` root. They could seed a chart-type corpus for the worksheet-to-chart converter later.
- **Prep flows:** server-client-python `test/assets/SampleFlow.tfl`, and e-bikes-demo `eBikes Inventory and Sales Data Flow.tflx` (66 MB).
- **TDVT logical-query tests:** `tdvt/tdvt/logicaltests/`, 231 files, about 8.5 MB. They are written in Tableau's internal logical-query XML, not as calculations.

## Excluded (licence)

| Repository | Licence | Files found | Reason |
|---|---|---|---|
| tableau/emb-hot-ca-v3 | none | `resources/redirect_byohotembed.twbx`, `resources/workbook_byohotembed.twbx` | no licence file, so all rights reserved |

These `tableau/*` repositories have no licence (`NONE`) or an unrecognised one (`NOASSERTION`), and contain no Tableau files, so nothing was taken from them:
- **No licence:** Accelerators, dcpat, embedding-playbook, extension-single-checkbox-parameters, metadata-api-samples, mobile-connected-client, sf-exp-cloud-tableau-embedded-analytics, tabjolt, tableau-data-dictionary, tableau.github.io, wdc3.
- **Unrecognised licence:** hyper-db, tableau-ui, vail.

## Notes

- Some files are Tableau's own public sample content:
  - Superstore
  - World Indicators
  - author e-mail addresses in extension manifests (all `@tableau.com`)
  - placeholder addresses such as `@mycompany.com`
  - AWS's documented example key `AKIAIOSFODNN7EXAMPLE` in a tab-s3-converter fixture

  A scan found no credentials or non-empty password attributes.
- `<workbook version='18.1'>` has been written unchanged since 2018.1. Read the real version from `source-build` (BUILD_PLAN P2.1).

<details>
<summary><code>corpus_manifest.py</code>: manifest regeneration and check script</summary>

```python
"""Recompute the derived fields of corpus/manifest.json from the files on disk.

Usage: uv run --no-project --with defusedxml python -I corpus_manifest.py [--check] CORPUS_DIR

Keeps the provenance you wrote (kind, source_*, license, also_in, ...). Recomputes sha256,
bytes and the document fields (root_tag, source_build, version, ...), plus the per-source
and overall totals. Fails if a file has no entry, an entry has no file, or provenance is
missing. With --check it only reports, so CI can prove the corpus is unchanged.
"""
import datetime
import hashlib
import io
import json
import pathlib
import sys
import zipfile

from defusedxml import ElementTree as DET

AREAS = ("workbooks", "datasources", "extracts", "tdvt")
REQUIRED = ("kind", "source_repo", "source_path", "source_commit", "license")
DOC_FIELDS = ("inner_document", "other_inner_documents", "root_tag", "source_build", "version",
              "original_version", "well_formed", "parse_error", "packaged_extracts")


def xml_info(data):
    info = {}
    try:
        _, root = next(iter(DET.iterparse(io.BytesIO(data), events=("start",))))
        info["root_tag"] = root.tag
        for attr in ("source-build", "version", "original-version"):
            if attr in root.attrib:
                info[attr.replace("-", "_")] = root.attrib[attr]
        DET.fromstring(data)
        info["well_formed"] = True
    except Exception as e:  # malformed files are kept on purpose as negative cases
        info.update(well_formed=False, parse_error="%s: %s" % (type(e).__name__, e))
    return info


def zip_info(data, ext):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        members = [zi for zi in zf.infolist() if not zi.is_dir()]
    except Exception as e:
        return {"well_formed": False, "parse_error": "%s: %s" % (type(e).__name__, e)}
    docs = sorted((zi for zi in members if zi.filename.lower().endswith(ext)),
                  key=lambda zi: (zi.filename.count("/"), zi.filename))
    if not docs:
        return {"well_formed": False, "parse_error": "no %s inside the package" % ext}
    info = {"inner_document": docs[0].filename}
    if len(docs) > 1:
        info["other_inner_documents"] = [d.filename for d in docs[1:]]
    if docs[0].file_size > 300_000_000:
        return dict(info, parse_error="inner document larger than 300000000 bytes; not parsed")
    try:
        info.update(xml_info(zf.read(docs[0])))
    except Exception as e:
        return dict(info, well_formed=False, parse_error="%s: %s" % (type(e).__name__, e))
    extracts = [{"path": m.filename, "bytes": m.file_size} for m in members
                if m.filename.lower().endswith((".hyper", ".tde"))]
    if extracts:
        info["packaged_extracts"] = extracts
    return info


def derived(path, kind):
    data = path.read_bytes()
    out = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if kind in ("twb", "tds"):
        out.update(xml_info(data))
    elif kind in ("twbx", "tdsx"):
        out.update(zip_info(data, "." + kind[:3]))
    return out


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    check = "--check" in sys.argv
    corpus = pathlib.Path(args[0]).resolve()
    root = corpus.parent
    manifest = json.loads((corpus / "manifest.json").read_text("utf-8"))
    entries = {e["path"]: e for e in manifest["files"]}
    on_disk = {p.relative_to(root).as_posix() for a in AREAS
               for p in (corpus / a).rglob("*") if p.is_file()}
    problems = ["in manifest but missing on disk: " + p for p in sorted(set(entries) - on_disk)]
    problems += ["on disk but not in manifest (add an entry with its provenance): " + p
                 for p in sorted(on_disk - set(entries))]
    changed = 0
    for path, e in sorted(entries.items()):
        problems += ["%s: missing %s" % (path, k) for k in REQUIRED if not e.get(k)]
        if path not in on_disk:
            continue
        new = derived(root / path, e.get("kind"))
        if (new.get("well_formed") is False) != bool(e.get("expect_invalid")):
            problems.append("%s: well_formed=%s but expect_invalid=%s" % (
                path, new.get("well_formed"), bool(e.get("expect_invalid"))))
        old = {k: e[k] for k in ("sha256", "bytes") + DOC_FIELDS if k in e}
        if new != old:
            changed += 1
            if check:
                problems.append("%s: sha256/bytes/document fields differ from the file" % path)
            for k in DOC_FIELDS:
                e.pop(k, None)
            e.update(new)
    sources = {s["repo"]: s for s in manifest["sources"]}
    for repo in sorted({e["source_repo"] for e in entries.values()}):
        s = sources.get(repo)
        if s is None:
            problems.append("no sources entry (licence, commit) for " + repo)
            continue
        licences = s.get("license_files") or []
        if not licences:
            problems.append("no license_files for " + repo)
        problems += ["licence file missing: " + f for f in licences if not (root / f).is_file()]
        mine = [e for e in entries.values() if e["source_repo"] == repo]
        s["files"], s["bytes"] = len(mine), sum(e["bytes"] for e in mine)
    kinds = {}
    for e in entries.values():
        k = kinds.setdefault(e["kind"], {"files": 0, "bytes": 0})
        k["files"] += 1
        k["bytes"] += e["bytes"]
    manifest["summary"] = {"files": len(entries), "bytes": sum(e["bytes"] for e in entries.values()),
                           "by_kind": dict(sorted(kinds.items()))}
    manifest["files"] = [entries[p] for p in sorted(entries)]
    print("%d files, %d with changed derived fields, %d problems" % (len(entries), changed, len(problems)))
    for p in problems:
        print("  " + p)
    if problems:
        sys.exit(1)
    if not check:
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        manifest["generated"] = now.isoformat().replace("+00:00", "Z")
        text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        (corpus / "manifest.json").write_text(text, "utf-8")


if __name__ == "__main__":
    main()
```

</details>
