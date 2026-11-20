# Provenance: tableau-document-schemas

These are Tableau's official XML Schema Definitions (XSD) for the workbook (`.twb`) format. They are vendored unmodified.

| | |
|---|---|
| Upstream | <https://github.com/tableau/tableau-document-schemas> |
| Licence | Apache-2.0 (`LICENSE.txt`, copied unchanged; upstream has no `NOTICE` file) |
| Branch | `main` |
| Commit | `b01c07d0192c2d5ad67e8c48dd3b29b1be5e52f0` (committed 2026-11-03T21:59:50Z) |
| Fetched | 2026-11-20, read-only through the GitHub API (`gh api .../contents/<path>?ref=<commit>`) |
| Modifications | None. Every file was checked against its upstream git blob SHA. |

## Files

| Vendored path | Upstream path | Bytes | SHA-256 |
|---|---|---|---|
| `schemas/2026_1/twb_2026.1.0.xsd` | `schemas/2026_1/twb_2026.1.0.xsd` | 297129 | `8bb236b72c1c388aca92bcd00164f65c0d4f5efeaf225252ce0619d8f631e923` |
| `schemas/2026_2/twb_2026.2.0.xsd` | `schemas/2026_2/twb_2026.2.0.xsd` | 306943 | `ff330e7df790e1479c75fe364a610202aafa2d691872f77bc7c991aed0b3a733` |
| `schemas/2026_3/twb_2026.3.0.xsd` | `schemas/2026_3/twb_2026.3.0.xsd` | 310792 | `0379da3a9c6e2d035be5112aad0d08b0c9d0476ef36917189a31ae4f7e76eac1` |
| `README.md` | `README.md` | 7535 | `a678c1c60a4365c50c70fa9270165ef97543dff217762e0f159346f22fc4b75c` |
| `LICENSE.txt` | `LICENSE.txt` | 11444 | `a32115015c1eea6186e519fa03ab3b77844170a75cf57ddbeb57f15dae44729a` |

Each XSD's header comment records its publication date and the Tableau build it was generated from:

| Schema | Published | Source build |
|---|---|---|
| 2026.1.0 | 2026-02-27 | 20261.26.0211.1127 |
| 2026.2.0 | 2026-06-15 | 20262.26.0528.2205 |
| 2026.3.0 | 2026-10-30 | 20263.26.1022.1645 |

## Notes for users

- **TWB only.** Upstream publishes no separate `.tds` schema. A `.tds` file is a `<datasource>` root holding the same content as a workbook's `<datasources>/<datasource>`. The XSD declares `datasource` only as a local element (`DataSource-CT`), not as a global one, so a validator can't take a `.tds` root directly. To validate one, wrap it in a minimal `<workbook>` or validate it against the `DataSource-CT` type.
- **No `.twbx` support.** Validate the `.twb` inside the zip.
- **Version mapping.** A workbook's `version='26.1'` corresponds to `twb_2026.1.0.xsd`. Older workbooks (for example `version='18.1'`, which Tableau has written since 2018.1) predate these schemas. Expect differences, and record them as known deviations (BUILD_PLAN P2.1).
- **Partial validation.** Some content is not validated: connection attributes, calculation formulas, and references between named objects. Search the XSD for `processContents="skip"` to find these parts.
- **Imports without locations.** Each XSD imports the namespaces `http://www.tableausoftware.com/xml/user` and `http://www.w3.org/XML/1998/namespace` with no `schemaLocation`. Configure your validator to resolve or ignore these imports, and never let it fetch them over the network.
- Upstream's `README.md` is kept unmodified. It covers the `<ManifestByVersion />` manifest form, localization annotations (2026.2+), and the REST API validation endpoints.

## Updating

When upstream publishes a new schema (for example `schemas/2027_1/`):
1. Fetch it at a pinned commit.
2. Check its git blob SHA.
3. Add it here under its upstream path.
4. Update the tables above and `third_party/README.md`.

Never edit the XSDs by hand.
