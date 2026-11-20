# Tableau File Formats, Extracts, Calculation Language and Visual Specs: Converter Reference for TabFree

Research date: 2026-11-20. Versions in scope: Tableau 2025.x/2026.x, TWB XSD 2026.1–2026.3, Hyper API 0.0.26479 (Sep 2026).
Method: Tableau-authored primary sources (official XSD, Hyper docs repo, Tableau help pages, Tableau GitHub sample workbooks, connector SDK). I read sample `.twb` XML text with the authenticated `gh` CLI; I did not download or run any binaries. "Observed" means the snippet comes verbatim from a real Tableau-saved file whose URL is cited. The SQL translation templates are my own designs built on the cited semantics, so they appear under **Inferences** and need conformance testing.

---

## Q1. What is the anatomy of the .twb/.tds XML, including the shelf/field-reference encoding?

### Takeaway
A `.twb` is one plain XML document, and a `.tds` is the `<datasource>` subtree on its own. Tableau's official XSD (2026.x) now defines the element order and the enumerations. The converter has to walk four areas:
- **`<datasources>`**: the federated connection with named connections, the physical relations (join, union, custom SQL), the logical `object-graph`, then `metadata-records`, columns (calcs, params, groups, bins, sets), and the extract.
- **`<worksheets>`**: per-view `datasource-dependencies` with `column-instance`s, then filters, sorts, panes/encodings, and the `rows`/`cols` table-algebra.
- **`<dashboards>`**: a zone tree.
- **Workbook-level** `<actions>`, `<windows>` and `<thumbnails>`.

Shelves reference fields as `[datasource].[derivation:field:typekey(:n)]` (examples: `[federated.x].[sum:Sales:qk]`, `[none:Category:nk]`, `[usr:Calculation_123:qk:2]`), combined with Polaris operators: `/` is nest, `+` is concatenation, `*` is cross.

### Cited Findings

#### Workbook root, versioning, manifest, feature-flag ("fcp") names
- The README's sample root is `<workbook original-version='26.1' source-build='0.0.0 (0000.0.0.0)' source-platform='win' version='26.1' xmlns:user='http://www.tableausoftware.com/xml/user'>`. TWB version string `26.1` corresponds to `twb_2026.1.0.xsd`. Inside `<document-format-change-manifest>` you can use a single `<ManifestByVersion />` instead of listing features — [tableau-document-schemas README](https://github.com/tableau/tableau-document-schemas)
- Files saved by recent releases still carry `version='18.1'`:
  - `source-build='2025.2.3 (20252.25.0912.2314)' … version='18.1'` — [hr.twb (community, 2025.2.3)](https://github.com/shreyamalogi/Predictive-HR-Intelligence-Analytics/blob/main/hr.twb)
  - `source-build='2024.2.0 (20242.24.0426.0923)' … version='18.1'` — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)
  - `source-build='2019.1.10 …' version='18.1'` — [ts_users_02.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb)
- The XSD's `WorkbookFile-CT` sequence is, in order:
  1. DocumentManifest
  2. RepositoryLocation
  3. Preferences
  4. StyleTheme and Styles
  5. LocalData
  6. DataSources
  7. **DataSourceRelationships** (blending)
  8. MapSources
  9. SharedViews
  10. Actions
  11. Worksheets
  12. Dashboards
  13. Windows
  14. Datagraph
  15. Thumbnails
  16. Shapes
  17. ReferencedExtensions
  18. ExplainData
  19. DataOrientation
  20. TabAgentConfig
  21. AcceleratorDetails
  22. WorkbookOptimizer

  Source: [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Real files contain feature-flag-prefixed element and attribute names. Examples observed:
  - `<_.fcp.ObjectModelEncapsulateLegacy.true...object-graph>` and `<_.fcp.ObjectModelTableType.true...column … datatype='table' …>` — [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb)
  - `_.fcp.VConnDownstreamExtractsWithWarnings.true...user-specific='false'` — [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb)
  - Manifest entries such as `<_.fcp.AnimationOnByDefault.true...AnimationOnByDefault />` — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)

  The 2026.3 XSD contains zero `_.fcp` strings — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)

#### Datasource: connection, named connections, physical relations (joins, custom SQL)
- Observed federated datasource, joins and custom SQL — [ts_users_02.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb):
```xml
<connection class='federated'>
  <named-connections>
    <named-connection caption='TS Users' name='postgres.0f1lpxa0xz4dy51ffe9520g92jqn'>
      <connection authentication='username-password' class='postgres' dbname='workgroup' port='8060'
                  server='your-repository-server' username='readonly' workgroup-auth-mode='prompt' … />
    </named-connection>
  </named-connections>
  <relation join='left' type='join'>
    <clause type='join'>
      <expression op='='>
        <expression op='[users].[site_id]' />
        <expression op='[Site Admin Permissions (Custom SQL)].[site_id]' />
      </expression>
    </clause>
    …
    <relation connection='postgres.0f1l…' name='users' table='[public].[users]' type='table' />
    <relation connection='postgres.0f1l…' name='Usage Stats (Custom SQL)' type='text'>/* … */ SELECT …</relation>
  </relation>
  <cols>
    <map key='[web_editing_enabled]' value='[sites].[web_editing_enabled]' />  <!-- field -> table.column -->
  </cols>
  <refresh increment-key='' incremental-updates='false' />
  <metadata-records> … </metadata-records>
```
- Schema enumerations, all from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - `RelationType-ST`: `join subquery table stored-proc text union batch-union pivot project collection`
  - `JoinType-ST`: `inner left right full`
  - `MetadataRecord-Class-ST`: `column measure level dimension hierarchy remote-set granularity pair-hierarchy capability primary-key foreign-key sampled-data unique-constraint table-statistic`
- Observed `metadata-record` — [ts_users_02.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb):
```xml
<metadata-record class='column'>
  <remote-name>id</remote-name><remote-type>3</remote-type><local-name>[id]</local-name>
  <parent-name>[users]</parent-name><remote-alias>id</remote-alias><ordinal>1</ordinal>
  <local-type>integer</local-type><aggregation>Sum</aggregation><precision>10</precision>
  <contains-null>false</contains-null>
  <attributes><attribute datatype='string' name='DebugRemoteType'>&quot;SQL_INTEGER&quot;</attribute> …</attributes>
</metadata-record>
```
- I tallied `remote-type` against `local-type` across 7 Tableau-saved sample files. Sources: [ts_users](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb), [ts_background_tasks](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb), [TDVT Results](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb), [QueryInfo.twb](https://github.com/tableau/tableau-log-viewer/blob/master/resources/workbooks/QueryInfo.twb)

  | remote-type | local-type |
  |---|---|
  | 2, 3, 20 | integer |
  | 5, 131 | real |
  | 7 | date or datetime |
  | 133 | date |
  | 135 | datetime |
  | 11 | boolean |
  | 72, 129, 130 | string |

#### Logical layer (2020.2+ relationships, "object model")
- Observed relationships — [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb):
```xml
<_.fcp.ObjectModelEncapsulateLegacy.true...object-graph>
  <objects>
    <object caption='Orders' id='Orders_ECFCA1FB690A41FE803BC071773BA862'> <properties context=''> <relation …/> </properties> </object>
    <object caption='People' id='People_D73023733B004CC1B3CB1ACF62F4A965'> … </object>
    <object caption='Returns' id='Returns_2AA0FE4D737A4F63970131D0E7480A03'> … </object>
  </objects>
  <relationships>
    <relationship>
      <expression op='='><expression op='[Region]' /><expression op='[Region (People)]' /></expression>
      <first-end-point object-id='Orders_ECFCA1FB690A41FE803BC071773BA862' />
      <second-end-point object-id='People_D73023733B004CC1B3CB1ACF62F4A965' />
    </relationship>
    <relationship> … [Order ID] = [Order ID (Returns)] … </relationship>
  </relationships>
</_.fcp.ObjectModelEncapsulateLegacy.true...object-graph>
```
- Relationship end points also allow these attributes in the XSD: `unique-key`, `guaranteed-value`, `is-db-set-unique-key`, `is-db-set-guaranteed-value`, `object-id`. The `object-graph` element allows `is-legacy` and `semantic-layer` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Each logical table gets a "count" pseudo-column:
  - Definition: `<_.fcp.ObjectModelTableType.true...column caption='Returns' datatype='table' name='[__tableau_internal_object_id__].[Returns_2AA0…]' role='measure' type='quantitative' />` — [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb)
  - Shelf usage: `[textscan.1gc4…].[__tableau_internal_object_id__].[cnt:test_results_combined.csv_C894710E…:qk]` — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)
- A single-table object with an Excel connection: `<relation connection='excel-direct.1a51qv…' name='WA_Fn-UseC_-HR-Employee-Attriti' table='[&apos;WA_Fn-UseC_-HR-Employee-Attriti$&apos;]' type='table'><columns gridOrigin='A1:AI1471:no:A1:AI1471:0' header='yes' outcome='6'><column datatype='integer' name='Age' ordinal='0' />…` — [hr.twb](https://github.com/shreyamalogi/Predictive-HR-Intelligence-Analytics/blob/main/hr.twb)

#### Columns: calculated fields, parameters, groups, bins, sets, folders, hierarchies, date options
- Calculated field: `<column caption='Time Since Last Login (days)' datatype='integer' name='[Calculation_854276568149831680]' role='measure' type='quantitative'><calculation class='tableau' formula='DATEDIFF(&apos;day&apos;, [login_at], NOW())' />`. Formulas are XML-escaped and keep `//` comments and `&#13;&#10;` newlines. An observed RLS calc mixes `ISMEMBEROF('YOUR_ACCESS_USER_GROUP')` with `CONTAINS([server_admins_string], ';' + USERNAME() + ';')` — [ts_users_02.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb)
- Column attributes in the XSD:
  - Identity, role and type: `role`, `default-role`, `aggregation`, `aggregation-param`, `type`, `datatype`, `user-datatype`, `default-format`, `caption`, `pivot`, `hidden`, `semantic-role`, `alias`, `folder-name`
  - Date: `fiscal-year-start`
  - Parameters: `param-domain-type`, `value`, `source-field`, `default-value-field`
  - Other: `aggregate-role-from`, `is-auto-gen-lod-field`
  - `<calculation>` attributes: `class formula scope-isolation solve-order peg decimals size-parameter size new-bin column default`

  Enumerations:
  - `DataSource-CalculationType-ST`: `tableau passthrough bin categorical-bin`
  - `DataType-ST`: `unknown integer real string datetime date boolean tuple spatial table`
  - `FieldRole-ST`: `dimension measure unknown`
  - `FieldType-ST`: `nominal ordinal quantitative unknown`
  - `DomainType-ST`: `any list range`

  Source: [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Parameter, observed — [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb):
```xml
<column caption='Top Customers' datatype='integer' name='[Parameter 1]' param-domain-type='range' role='measure' type='quantitative' value='5'>
  <calculation class='tableau' formula='5' />
  <range granularity='5' max='20' min='5' />
</column>
```
  Parameters live in a datasource named `Parameters` (`inline='true'`) and are referenced as `[Parameters].[Parameter 1]` — [tableau-mcp parameter example](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/parameter.json). That example lists `param-domain-type` values `all|list|range`, but the XSD enumerates `any|list|range` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd).
- Set (Top N by profit, driven by a parameter), observed — [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb):
```xml
<group name='[Top Customers by Profit]' name-style='unqualified' user:ui-builder='filter-group'>
  <groupfilter count='[Parameters].[Parameter 1]' end='top' function='end' units='records' user:ui-marker='end' user:ui-top-by-field='true'>
    <groupfilter direction='DESC' expression='SUM([Profit])' function='order' user:ui-marker='order'>
      <groupfilter function='level-members' level='[Customer Name]' user:ui-enumeration='all' user:ui-marker='enumerate' />
    </groupfilter>
  </groupfilter>
</group>
```
  Set/filter function vocabulary (`Function-ST`): `ancestors crossjoin descendants empty-level end except extract filter hierarchy-members intersection level-members member named-set order range relative-date-range reference reorder-dimensionality tuples union visual-totals` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Bin and group, observed — [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb):
  - Bin: `<calculation class='bin' decimals='0' formula='[Profit]' peg='0' size-parameter='[Parameters].[Parameter 2]' />`
  - Group: `<calculation class='categorical-bin' column='[Product Name]' new-bin='true'>`
- Folders and aliases, observed: `<folder name='Security' role='dimensions'>` and `<aliases enabled='yes' />` — [ts_users_02.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb)
- Hierarchies and date options in the XSD — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - `<drill-paths><drill-path name=…><field>…</field>`
  - `<date-options>` with `start-of-week` ∈ {`seven-day-period sunday monday tuesday wednesday thursday friday saturday`} and `fiscal-year-start` ∈ {`january … december`}
  - Column-level `fiscal-year-start` (unsignedInt)

#### Worksheets: view, dependencies, filters, sorts, panes and encodings, shelves
- Observed worksheet skeleton, condensed — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb):
```xml
<worksheet name='Details'>
  <layout-options><title><formatted-text><run bold='true'>&lt;Sheet Name&gt;</run>…</formatted-text></title></layout-options>
  <table>
    <view>
      <datasources>…</datasources>
      <datasource-dependencies datasource='textscan.1gc4…'>
        <column-instance column='[Priority]' derivation='None' name='[none:Priority:nk]' pivot='key' type='nominal' />
        <column-instance column='[Calculation_1376834876472950788]' derivation='User' name='[usr:Calculation_1376834876472950788:ok]' pivot='key' type='ordinal' />
      </datasource-dependencies>
      <filter class='categorical' column='[textscan.1gc4…].[none:Passed:nk]'>
        <groupfilter function='level-members' level='[none:Passed:nk]' user:ui-enumeration='all' user:ui-marker='enumerate' />
      </filter>
      <manual-sort column='[textscan.1gc4…].[none:Priority:nk]' direction='ASC'>
        <dictionary><bucket>&quot;Required&quot;</bucket><bucket>&quot;Required If Supported&quot;</bucket>…</dictionary>
      </manual-sort>
      <slices><column>[textscan.1gc4…].[none:Passed:nk]</column>…</slices>
    </view>
    <style>…</style>
    <panes>
      <pane id='4' selection-relaxation-option='selection-relaxation-allow'>
        <view><breakdown value='auto' /></view>
        <mark class='Bar' />
        <encodings>
          <color column='[textscan.1gc4…].[none:Calculation_1487173092642668549:nk]' />
          <lod column='[textscan.1gc4…].[none:Test Case:nk]' />
        </encodings>
      </pane>
    </panes>
    <rows>[textscan.1gc4…].[none:Calculation_1487173092642668549:nk]</rows>
    <cols>[textscan.1gc4…].[ctd:Test Case:qk]</cols>
    <subtotals><column>[textscan.1gc4…].[none:Calculation_1487173092652093447:nk]</column></subtotals>
  </table>
</worksheet>
```
- Nested row headers are written with `/`. Observed: `<rows>([textscan.1gc4…].[none:Priority:nk] / ([textscan.1gc4…].[none:TestName:nk] / ([textscan.1gc4…].[none:Test Case:nk] / …</rows>` — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)
- Polaris table algebra has three operators: concatenation (`+`, ordered union), cross (`×`, Cartesian product) and nest (`/`, cross restricted to combinations present in the data). VizQL uses the same algebra — [Polaris paper](https://datavis.cs.columbia.edu/files/papers/polaris.pdf), [Polaris slides](https://cs598.github.io/slides/polaris.pdf), [MSC arXiv 2408.04798](https://arxiv.org/pdf/2408.04798)
- Column-instance naming is `[derivation:FieldName:typePivot]`. Suffixes: `nk` = nominal key, `ok` = ordinal key, `qk` = quantitative key, `nq` = rare discrete measure. Shelf references are `[datasourceId].[column-instance-name]` — [tableau-mcp column-instance-naming.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/column-instance-naming.json)
- Derivation-to-prefix pairs I observed across the sample files above: `None→none`, `Sum→sum`, `Avg→avg`, `Count→cnt`, `CountD→ctd`, `Attribute→attr`, `User→usr`, `Day-Trunc→tdy`, `Hour-Trunc→thr`.
  - A quick table calc stacks a prefix: `<column-instance column='[query-hash]' derivation='CountD' name='[pcto:ctd:query-hash:qk:2]' …><table-calc ordering-type='Columns' type='PctTotal' /></column-instance>` — [QueryInfo.twb](https://github.com/tableau/tableau-log-viewer/blob/master/resources/workbooks/QueryInfo.twb), [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb)
  - tableau-mcp lists different derivation spellings (`Attr`, `TruncMonth`) and gives `[tmo:Order Date:qk]` as the truncated-month example. This conflicts with the observed `Attribute` and `Day-Trunc` spellings — [tableau-mcp column-instance-naming.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/column-instance-naming.json)
- Table-calc configuration lives in the `column-instance` inside `datasource-dependencies`, for example `[usr:Calculation_X:qk:2]` with `<table-calc ordering-type='Field' ordering-field='[ds].[Sub-Category]'/>`. The `:2` suffix is the table-calc instance id — [tableau-mcp table-calc.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/table-calc.json)
- `<table-calc>` attributes, from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - Attributes: `field ordering-type ordering-field level-break level-address aggregation type from to window-options diff-options pcttotal-options rank-options tc-options`
  - `TCType-ST`: `None CumTotal WindowTotal Difference PctDiff PctValue PctTotal Rank PctRank Custom`
  - `OrderingType-ST`: `None Table TableCol Rows Columns Pane PaneCol RowInPane ColumnInPane CellInPane Field`
  - `WindowOptions-ST`: `IncludeCurrent`, `NullIfIncomplete`
  - `PctTotalOptions-ST`: `IncludePages`
- Filters, from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - `Filter-Class-ST`: `categorical quantitative relative-date`
  - Filter attributes: `context` (boolean), `filter-group`, `included-values`, `period-type-v2`, `first-period`, `last-period`, `include-future`, `include-null`, `kind` (`hide`)
  - `PeriodTypeV2-ST`: `year iso-year quarter iso-quarter month week iso-week day hour minute second`
  - `FilterScope-ST`: `pervasive logical-tables datasource-scoped`
  - Quantitative filters carry `<min>`/`<max>` children
- Other observed filter forms, from [QueryInfo.twb](https://github.com/tableau/tableau-log-viewer/blob/master/resources/workbooks/QueryInfo.twb), [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb), [Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb):
  - Range: `<groupfilter from='&quot;construct-protocol&quot;' function='range' level='[none:event:nk]' to='&quot;sort-panes&quot;' …/>`
  - Condition/wildcard: `<groupfilter expression='ISNULL([Notes]) OR (NOT(CONTAINS(LOWER([Notes]), LOWER(&apos;suspended&apos;))))' function='filter' …/>`
  - Top N: `<groupfilter count='20' end='top' function='end' units='records' …>` wrapping `function='order'`
- The tableau-mcp relative-date example uses a `<date-range anchor='today' first-period='-30' period-type='days'/>` child, which differs from the XSD's attribute form above. Its column format is `[federated.XXXX].[[Order Date]]` — [tableau-mcp filter-relative-date.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/filter-relative-date.json)
- Sorts:
  - `SortType-ST`: `natural alphabetic computed manual`; the XSD has `computed-sort`, `manual-sort` and `natural-sort` elements — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
  - View-level computed sort: `<sort class='computed' column='[ds].[none:Sub-Category:nk]' direction='DESC' using='[ds].[sum:Sales:qk]'/>` — [tableau-mcp sort.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/sort.json)
- Marks and encodings, from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - Mark classes: `Automatic Text Icon Shape Rectangle Bar GanttBar Square Circle Heatmap PolyLine Line Polygon Area Pie Multipolygon VizExtension`
  - `StackingMode-ST`: `off on auto`
  - `ShelfType-ST`: `none columns rows pages filter image measures showme geometry encoding`
  - Pane attributes include `x-axis-name`, `y-axis-name`, `x-index`, `y-index`
  - The style `<encoding>` element has `fold` and `synchronized` attributes (axis folding and synchronization)
- Measure Names/Values, observed: `[:Measure Names]` is used as a column, as a color encoding (`<color column='[federated.1rk9… (copy 2)].[:Measure Names]' />`) and as a filter whose members are quoted column-instance names (`<groupfilter function='member' level='[:Measure Names]' member='&quot;[federated.0p79…].[avg:cols:qk]&quot;' />`). Measure Values appears as `[Multiple Values]` on text/size encodings — [QueryInfo.twb](https://github.com/tableau/tableau-log-viewer/blob/master/resources/workbooks/QueryInfo.twb), [Timeline.twb](https://github.com/tableau/tableau-log-viewer/blob/master/resources/workbooks/Timeline.twb)
- Dual axis, as described by tableau-mcp: the shelf is `([federated.XXXX].[sum:Sales:qk]+[federated.XXXX].[sum:Profit:qk])` with per-measure panes (`id='1'` Bar, `id='2'` Line) plus an auto `__combined_axes` pane. The same file claims a single shared axis uses `*`, which I have not verified — [tableau-mcp dual-axis.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/dual-axis.json)
- Reference lines and trend lines, from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - `<reference-line>` attributes include `axis-column value-column scope label-type formula value percentile probability tile-count boxplot-whisker-type fill-above fill-below reference-parameter show-lines percentage-bands`
  - `ReferenceLineFormulaType-ST`: `constant total sum min max average median quantiles percentile stdev confidence medianconfidence`
  - Scope values: `per-cell per-pane per-table`
  - `TrendLineFitType-ST`: `linear polynomial log exp power`
  - `ForecastModelType-ST`: `auto-season auto custom`

#### Dashboards, actions, stories, windows, thumbnails
- Observed dashboard zone tree. Coordinates use a 0–100000 relative space, and containers are `layout-flow` with `param='horz|vert'` — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb):
```xml
<dashboard name='TDVT Test Results'>
  <size sizing-mode='automatic' />
  <zones>
    <zone h='100000' id='2' type-v2='layout-basic' w='100000' x='0' y='0'>
      <zone h='98726' id='24' param='horz' type-v2='layout-flow' w='99318' x='341' y='637'>
        …
        <zone h='4140' id='29' layout-strategy-id='distribute-evenly' param='horz' type-v2='layout-flow' …>
          <zone fixed-size='286' h='4140' id='31' is-fixed='true' type-v2='empty' w='16561' x='50021' y='14968'>
          <zone h='4140' id='26' mode='compact' param='[Parameters].[Parameter 2]' type-v2='paramctrl' …>
```
  This dashboard has two top-level zones under `<zones>`: `layout-basic` and `color`, i.e. a legend outside the tiled tree (observed via XML parse of the same file).
- Dashboard enumerations, from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - `ZoneType-ST`: `invalid visual color shape size map highlighter filter currpage empty title text bitmap web add-in dashboard-object paramctrl flipboard flipboard-nav layout-basic layout-flow`
  - `ZoneLayoutType-ST`: `basic free-form flow distribute-evenly trivial`
  - `DashboardSizingMode-ST`: `unspecified automatic fixed range vscroll`
  - Zone attributes include `x y w h id name type-v2 layout-strategy-id param hidden is-fixed fixed-size alt-text url show-title image-file-url paired-zone-id`
  - `<devicelayouts><devicelayout auto-generated=…>`
  - There is no `floating` attribute.
- tableau-mcp gives two authoring constraints: `sizing-mode='fixed'` requires min = max width and height, otherwise the load fails; zone IDs must be unique across all dashboards — [tableau-mcp dashboard-tiled.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/dashboard-tiled.json)
- Observed filter action — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb):
```xml
<actions>
  <action caption='Filter1' name='[Action1]'>
    <activation auto-clear='true' type='on-select' />
    <source dashboard='TDVT Test Results' type='sheet'><exclude-sheet name='Scorecard' />…</source>
    <command command='tsc:tsl-filter'>
      <param name='exclude' value='Test Cast Performance by Numbers,Test Cast Performance by Percent' />
      <param name='special-fields' value='all' />
      <param name='target' value='TDVT Test Results' />
    </command>
  </action>
</actions>
```
- Action vocabulary in the XSD — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd):
  - `ActivationMethod-ST`: `explicit on-select on-hover`
  - `SourceType-ST`: `all datasource sheet`
  - `UrlActionType-ST`: `default-zone-or-browser browser specific-zone`
  - The command name is only constrained by the pattern `[^:]+:[^:]+`; URL actions use a `<link>` element.
  - Tableau's MCP author-action tool distinguishes `parameter`, `set` and `url` actions — [tableau-mcp author-action](https://github.com/tableau/tableau-mcp/blob/main/docs/docs/desktop-tools/data-source/author-action.md)
- Stories are `<dashboard type='storyboard'>` (`Dashboard-DashboardType-ST`) with `flipboard`/`flipboard-nav` zones, `<flipboard><story-points><story-point captured-sheet=…>`, plus `currentDeltas` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Observed window: `<windows source-height='30'><window class='dashboard' maximized='true' name='TDVT Test Results' tab-color='#f66273'><viewpoints><viewpoint name='Scorecard'><zoom type='entire-view' /></viewpoint>…<highlight><color-one-way><field>[…].[none:Passed:nk]</field>` — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb). `Window-WindowType-ST` is `worksheet dashboard`, and the XSD defines a `<thumbnail>` element under `Workbook-Thumbnails-G` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Blending links sit at workbook level as `<datasource-relationship source=… target=…>` with `column-mapping` key/value pairs — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)

### Inferences
- **Normalize "fcp" names first.** Before any XPath, rewrite `_.fcp.<Flag>.true...<name>` to `<name>` and drop the `.false...` variants. Otherwise relationships, table-count columns and some extract attributes are invisible to a naive parser, and the XSD will not validate them (the XSD has no `_.fcp` strings).
- **Don't trust `version`/`original-version` for versioning.** Samples saved by 2019.1–2025.2 all say `18.1`. Parse `source-build` (for example `20252.25…` means 2025.2) and choose behaviour and XSD by that value.
- **Floating zones.** The XSD has no `floating` flag, and the TDVT dashboard keeps its color legend as a second top-level zone. So floating objects are most likely top-level `<zone>`s after the root `layout-basic` zone, with absolute x/y/w/h in the same 0–100000 space. Treat this as likely and confirm on a dashboard with known floating objects.
- **Relationship performance options.** `unique-key` and `guaranteed-value` on end points very likely persist the Performance Options (cardinality and referential integrity). The SQL generator needs them for join-culling and dedup decisions.
- **remote-type codes.** The codes match OLE DB `DBTYPE` numbers: 2 = I2, 3 = I4, 5 = R8, 7 = DATE, 11 = BOOL, 20 = I8, 72 = GUID, 129 = STR, 130 = WSTR, 131 = NUMERIC, 133 = DBDATE, 135 = DBTIMESTAMP. Use `local-type` as Tableau's logical type and `remote-type` only for precision hints.
- **Calculation references.** Formulas reference other calcs by internal name (`[Calculation_123…]`) while the UI shows the `caption`. Build a name↔caption map per datasource. Parameter references are `[Parameters].[Parameter N]`.
- **Recommended JSON spec shape per worksheet.**
  - `datasource` refs and `fields[]`: name, caption, role, type, datatype, calc AST, default aggregation, format.
  - `columnInstances[]`: derivation, typeKey, tableCalc config.
  - `shelves`: a rows/cols algebra tree with `nest`, `cross` and `concat` nodes.
  - `panes[]`: mark class, encodings by channel, axis names.
  - `filters[]`: class, stage (extract, datasource, context, dimension, measure, table-calc), members/range/relative/top-N/condition.
  - `sorts[]`, `referenceLines[]`, `trendLines[]`, `totals`, and `style` (formats, palettes).
  - Dashboards become a zone tree with absolute layout computed from the relative coordinates.

### Gaps
- The command strings for highlight, URL, go-to-sheet, parameter and set actions are only verified for `tsc:tsl-filter`. I could not verify `tsc:brush` and similar names from a primary source; harvest them from real workbooks.
- I could not observe the date-part prefixes for Year, Quarter, Month, Week and Weekday or the Month-Trunc spelling (`tmn` vs `tmo`) in a real file during this session: GitHub code search hit its rate limit. Build an empirical prefix table from TabFree's own workbook corpus.
- No sample observed for dynamic-parameter XML (the XSD shows only the `source-field` and `default-value-field` attributes), device layouts, story points, image/web/extension zones, thumbnails encoding (presumably base64 PNG), or data-blending link XML in practice.

---

## Q2. Is there an official schema, and what do the XSDs and the Document API cover?

### Takeaway
Yes. Since February 2026 Tableau publishes official, Apache-2.0 XSDs for TWB 2026.1, 2026.2 and 2026.3. They give syntactic validation only: connection attributes, calc contents and cross-references are not validated, and there is no TWBX support. Tableau Server 2026.2 and Cloud (June 2026) added REST endpoints for semantic validation. The Python Document API is unsupported ("As-Is"), was last released in 2022, and only reads and updates connections and fields. It cannot create files, edit worksheets or dashboards, or add extracts.

### Cited Findings
- The repo contains `schemas/2026_1/twb_2026.1.0.xsd`, `2026_2/twb_2026.2.0.xsd` and `2026_3/twb_2026.3.0.xsd` — [tableau-document-schemas](https://github.com/tableau/tableau-document-schemas)
  - Formally published in February 2026 as an officially supported baseline; Apache-2.0 licensed.
  - Starting with 2026.2, the XSD carries `<user:localizable>` annotations.
  - The XSD does not validate "attributes in connection elements", "calculated field contents, like function names and object references" or "references to other named workbook contents". These regions are marked `processContents="skip"`.
  - "The schemas do not support building or validating packaged workbook files (TWBX)."
- The 2026.3 XSD has 7,916 lines, 85 named complex types and 18 `processContents="skip"` regions. Connection-G uses `<xs:anyAttribute namespace="##local" processContents="skip"/>` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- REST endpoints Validate Workbook, Validate Workbook and Upload, and Validate Uploaded Workbook arrived in Tableau Cloud June 2026 / Server 2026.2 — [tableau-document-schemas README](https://github.com/tableau/tableau-document-schemas), [REST reference](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm#validate_workbook)
- The Document API is an "*unsupported* way to programmatically make updates" — [document-api-python README](https://github.com/tableau/document-api-python)
  - Supports TWB, TWBX, TDE and TDSX files from roughly Tableau 9.x.
  - Reads connection info (server, username, database, auth type, connection type) and updates server, username and database.
  - Lists all fields and the fields used by each sheet.
  - "*doesn't* support creating files from scratch, adding extracts into workbooks or data sources, or updating field information".
  - The latest release is v0.11 (2022-11-07) — [releases](https://github.com/tableau/document-api-python/releases)
- Its field model only parses `caption, datatype, role, type, alias, calculation` plus metadata `aggregation` — [field.py](https://github.com/tableau/document-api-python/blob/master/tableaudocumentapi/field.py)
- Other XML tooling:
  - `tableau_tools`: "Tableau REST API, XML modification, tabcmd and repository tools", 223 stars, active 2026 — [bryanthowell-tableau/tableau_tools](https://github.com/bryanthowell-tableau/tableau_tools)
  - Tableau's official MCP server (Apache-2.0) ships TWB XML authoring examples and validators, for example `selfReferentialFixedLod.ts` — [tableau/tableau-mcp](https://github.com/tableau/tableau-mcp/tree/main/src/desktop)
  - Its Desktop tools include *Validate Calculated Field*, which returns `isValid`, errors, `dataType` and `isAggregate` through Tableau Desktop's local External Client API, "Available November 2026 and later" — [validate-calculated-field](https://github.com/tableau/tableau-mcp/blob/main/docs/docs/desktop-tools/data-source/validate-calculated-field.md)

### Inferences
- Use the XSD that matches the workbook's `source-build` for a structural sanity pass: schema-aware parsing, enumeration lists, element order.
- Write TabFree's own typed model and semantic checks (calc parser, reference resolver).
- Keep a raw-XML passthrough (`unparsed` blobs) for elements the model doesn't understand, so nothing is silently lost.
- The Document API is only useful for connection rewrites; for full fidelity, parse the XML directly (lxml with namespace `user`).

### Gaps
- Not checked: whether XSDs for pre-2026 versions will be published, and what exactly changes between 2026.1 and 2026.3 (no diff done).
- Whether REST validation can run against arbitrary old-version TWBs is unknown.

---

## Q3. How do packaging (.twbx/.tdsx/.tds), legacy .tde and .hyper work, and how can they be read and exported? (Hyper API, pantab, Parquet/Iceberg, performance, licence, non-Hyper readers)

### Takeaway
- **Packages.** `.twbx` and `.tdsx` are zip packages: the XML document plus copies of file-based data, extracts, images, shapes and custom geocoding.
- **Reading `.hyper`.** Use Tableau's Hyper API: Python, C++ and Java, Apache-2.0 since July 2024, latest 0.0.26479 (3 Sep 2026). Its SQL `COPY … TO` writes CSV, Parquet or Arrow directly to local disk or S3. It reads Parquet, CSV, Arrow and Iceberg v1/v2 externally but cannot write Iceberg.
- **Legacy `.tde`.** It is gone from current products. Desktop 2024.2 was the last version to open it, and Tableau's own conversion sample reads `.tde` through Hyper's undocumented `external(..., format => 'tde')` with Hyper API 0.0.17537 pinned.
- **Other readers.** I found no independent, open-source `.hyper` reader. pantab (BSD-3) wraps Hyper for pandas, pyarrow and polars.

### Cited Findings
- Packaged workbooks — [Packaged Workbooks help](https://help.tableau.com/current/pro/desktop/en-us/save_savework_packagedworkbooks.htm):
  - They contain the workbook plus copies of local file data sources: background images, custom geocoding, custom shapes, local cube files, Access, Excel, `.hyper` extracts, and text files.
  - Renaming to `.zip` unpacks them into the `.twb` and folders.
  - Live database connections and published Tableau Server data sources are not included unless extracted locally.
  - "The content of the .twbx file is stored in plain text."
- The Document API opens `.twbx`/`.tdsx` with `zipfile.is_zipfile`, finds the first `.twb`/`.tds` in the archive, and notes "A .twbx can contain .tds files if it contains custom geocoding" — [xfile.py](https://github.com/tableau/document-api-python/blob/master/tableaudocumentapi/xfile.py)
- An observed extract connection points to a `.hyper` path with schema `Extract`: `<connection … class='hyper' dbname='C:/Users/…/TS Background Tasks.hyper' default-settings='yes' schema='Extract' …><relation name='Extract' table='[Extract].[Extract]' type='table' />` — [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb)
- Hyper API platform and licence, from [installation.md](https://github.com/tableau/hyper-db/blob/main/website/docs/installation.md):
  - Available for Python, C++ and Java on Windows, macOS and Linux; 64-bit only; Python 3.11+.
  - "The Hyper API packages are released under the Apache 2.0 License"; the documentation is MIT.
- Release history, from [Hyper releases](https://tableau.github.io/hyper-db/docs/releases):
  - 0.0.19691 (4 Jul 2024): "Hyper API is now available under the Apache 2.0 license."
  - Latest is 0.0.26479 (3 Sep 2026).
  - 0.0.26225 (Aug 2026) added `LISTAGG`, `STRING_AGG`, `ARRAY_AGG … ORDER BY` and others.
  - 0.0.24081 added `split` and `regexp_split_to_array`; 0.0.23135 added `regexp_substr`, `regexp_count` and `regexp_instr`.
  - 0.0.21200 added Azure Blob support and dropped CentOS/RHEL 7.
  - 0.0.19484 added native Apple Silicon.
  - 0.0.17782 deprecated .NET; 0.0.17971 opened its source.
  - 0.0.8707 (Oct 2019): Hyper API "replaces Extract API 2.0 for .hyper files".
- Hyper's SQL dialect is now documented as the "Salesforce Data Cloud SQL dialect"; Data-Cloud-only functions are not in Hyper API — [sql.md](https://github.com/tableau/hyper-db/blob/main/website/docs/sql.md)
- `COPY TO`, added in 0.0.17360 (Jul 2023) — [releases.md](https://github.com/tableau/hyper-db/blob/main/website/docs/releases.md), archived [copy_to.md](https://github.com/tableau/hyper-db/blob/0fb9fa323c5236c490c59a96a3b57f0583381cf6/website/docs/sql/command/copy_to.md):
  - Synopsis: `COPY { <table_name> [ (<column_name> [, ...] ) ] | ( <query> ) } TO <target_location> [ WITH (<option> [, ...]) ]` with `FORMAT => <format_name>`.
  - Documented example: `COPY products TO s3_location('s3://mybucket/mydirectory/products.parquet', access_key_id => '', secret_access_key => '')`. The format is inferred from the extension; a `region =>` option exists; files "are written directly by the server".
- External formats — archived [formats.md](https://github.com/tableau/hyper-db/blob/0fb9fa323c5236c490c59a96a3b57f0583381cf6/website/docs/sql/external/formats.md):
  - Supported: text, CSV (optionally gzip), Parquet v1/v2, Iceberg ("version 1 and 2 are supported; version 3 is not supported"; path must point to the table directory), Arrow (`arrowfile`, `arrowstream`).
  - "Array types can only be read from, but not written to, Apache Iceberg and Apache Parquet."
  - `MAX_FILE_SIZE` makes `COPY TO` write multiple files.
  - Options include `SANITIZE`, `ENCODING` and `ON_CAST_FAILURE`.
- Reading-side milestones — [Hyper releases](https://tableau.github.io/hyper-db/docs/releases):
  - Iceberg external format arrived in 0.0.15735 (Oct 2022), when S3 reading became default-on.
  - Parquet row-group min/max pruning arrived in 0.0.14751.
  - `external()` and temporary external tables arrived in 0.0.14265.
  - Delta Lake is not mentioned.
- Hyper reads S3 natively "without the need to install any external dependencies like boto or aws-cli" — [native-s3 sample](https://github.com/tableau/hyper-api-samples/tree/main/Community-Supported/native-s3)
- Database file format versions — [hyper_process.md](https://github.com/tableau/hyper-db/blob/main/website/docs/hyper-api/hyper_process.md), [releases](https://tableau.github.io/hyper-db/docs/releases):
  - The `default_database_version` setting accepts 0–4.
  - v1 is deprecated.
  - v2 handles collation-independent text: Tableau 2022.1.2+, Prep 2022.3+.
  - v3 adds 128-bit numerics: Desktop 2022.4.1, Server 2023.1+.
  - v4 adds 32-bit floats: 2024.3+.
  - Since 0.0.23576 (Oct 2025) the default is version 2.
  - Older products fail with "unsupported version 3 (max supported version: 2)".
- Up- or down-converting a file version means copying all tables into a new file — [convert-hyper-file sample](https://github.com/tableau/hyper-api-samples/tree/main/Community-Supported/convert-hyper-file)
- `.tde` support in Tableau's own sample — [tde-to-hyper sample](https://github.com/tableau/hyper-api-samples/tree/main/Community-Supported/tde-to-hyper):
  - The README says ".tde file format will be deprecated by end of 2023" and pins `tableauhyperapi` 0.0.17537.
  - The script enumerates tables with `SELECT "SCHEMAS_NAME","TABLES_NAME" FROM external(<tde>, format => 'tde', "table" => 'SYS.TABLES') JOIN external(<tde>, format => 'tde', "table" => 'SYS.SCHEMAS') ON "TABLES_PARENT"="SCHEMAS_ID" WHERE "SCHEMAS_NAME" <> 'SYS' AND "TABLES_NAME"<>'$TableauMetadata' …`
  - It then runs `CREATE TABLE … AS SELECT * FROM external(<tde>, format => 'tde', "table" => 'schema.table')`.
- `.tde` deprecation timeline — [Extract Upgrade to .hyper Format](https://help.tableau.com/current/pro/desktop/en-us/extracting_upgrade.htm) (via search summary):
  - Desktop 2024.2 is the last version supporting `.tde` workbooks and data sources.
  - Tableau Cloud and Public changed in March 2023; Server 2023.1 disables `.tde` uploads by default.
  - Upgrade is done with Extract > Upgrade, is irreversible, and the result does not open in versions earlier than 10.5.
- pantab — [pantab README](https://github.com/innobi/pantab), [pantab examples](https://pantab.readthedocs.io/en/latest/examples.html):
  - BSD-3 licence; latest v5.3.0 (2026-04-08).
  - "Binary distributions of pantab include the Tableau Hyper shared library and executable, which are licensed under the terms of the Apache 2.0 license."
  - pandas, PyArrow and Polars are first-class since 4.0; writing accepts any Arrow PyCapsule object; `return_type="pyarrow"|"polars"`.
  - Functions: `frames_from_hyper` (keys are `TableName`), `frame_from_hyper_query`, `table_mode="a"`.
  - Geography is passed as WKT and read back as WKB; 32-bit floats need `default_database_version` ≥ 4.
- Extract storage options were renamed in 2020.2 from Single/Multiple Tables to Logical/Physical Tables — [Extract your data](https://help.tableau.com/current/pro/desktop/en-us/extracting_data.htm). The XSD's `Extract-Type-ST` has the single value `normalized` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Other readers:
  - Search found no open-source or DuckDB reader that opens `.hyper` without the Hyper API. The practical workaround is to export to Parquet/CSV with Hyper and query that elsewhere — [Query Parquet with DuckDB and Hyper (benchmark blog)](https://aetperf.github.io/2022/10/04/Query-Parquet-files-with-DuckDB-and-Tableau-Hyper-engines.html)
  - The `tableau/hyper-db` repo is "Documentation for Hyper", not engine source — [tableau/hyper-db](https://github.com/tableau/hyper-db)

### Inferences
- **Extract-to-lake pipeline.** All steps run inside `hyperd`, so rows never pass through Python:
```python
from tableauhyperapi import HyperProcess, Telemetry, Connection, TableName, escape_string_literal

with (
    HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hp,
    Connection(hp.endpoint, database=hyper_path) as c,
):
    for schema in c.catalog.get_schema_names():
        for t in c.catalog.get_table_names(schema):  # multi-table (normalized/physical) extracts
            cols = c.catalog.get_table_definition(
                t
            ).columns  # -> Glue/Iceberg schema + Tableau metadata
            c.execute_command(f"""COPY {t} TO s3_location({escape_string_literal(f"s3://lake/{ds}/{t.name.unescaped}/part.parquet")},
                                   access_key_id => …, secret_access_key => …, region => 'us-east-1')
                                   WITH (FORMAT => 'parquet', MAX_FILE_SIZE => 512000000)""")
```
  Credentials come from the user's secret store, never literals in code. Then register the files: Athena `CREATE EXTERNAL TABLE … STORED AS PARQUET`, then CTAS into Iceberg (Q4). Alternatively, `COPY` to local Parquet and upload with the AWS SDK if `s3_location` can't take STS session tokens.
- **Licence.** Since July 2024, embedding and redistributing `tableauhyperapi` (including the `hyperd` binary, per pantab's statement) in an open-source tool is permitted under Apache-2.0. Keep the NOTICE and licence texts and the telemetry opt-out. The engine remains closed-source, so TabFree depends on a vendor binary for reading `.hyper`. Mitigation: convert once and keep the open formats as the system of record.
- **`.tde`.**
  1. Try the `format => 'tde'` path with the pinned 0.0.17537 in an isolated container.
  2. If that fails, run Extract > Upgrade on a ≤ 2024.2 Desktop or Server.
  3. Better still, re-extract from the source DB, or refresh on Tableau Server before download. A server-side refresh already rewrites `.tde` to `.hyper` on 2023.1+ servers.
- **Type mapping.** Hyper `NUMERIC(38,x)` (128-bit, v3) maps to Parquet decimal(38). 32-bit `REAL` (v4) maps to float. Check `TIMESTAMP` vs `TIMESTAMPTZ` and `tableau.tabgeography` (export as WKT/WKB string) case by case. Text collations matter for case-insensitive comparisons; see Gaps.

### Gaps
- Unverified whether `format => 'tde'` still works in Hyper API 0.0.26479. It is undocumented, and I did not execute binaries.
- Parquet writer options (compression codec, row-group size), Iceberg writing (not documented, so assume none), and support for S3 session tokens in `s3_location` were not found. The current Data Cloud SQL reference pages for COPY TO and external formats returned HTTP 403 to automated fetch, so I relied on the archived Hyper docs (pre-May 2025).
- No public benchmark of Hyper `COPY TO` Parquet throughput for multi-GB extracts.
- How geography and collation-tagged text columns export to Parquet, and whether Tableau-created extracts use case-insensitive collations, is unknown.
- Exact zip folder layout inside `.twbx`/`.tdsx` (for example `Data/Extracts/…`) is not verified from a primary source in this session; inspect real downloads.

---

## Q4. Where is extract metadata stored (extract filters, aggregation, incremental keys, hidden fields), and how can it be replicated with Athena/Iceberg?

### Takeaway
The extract recipe sits in the datasource's `<extract>` element:
- **Attributes:** `enabled`, `units`/`count` (all rows, top N or sample), `object-id`, `type` (`normalized`) and `user-specific`.
- **Children:** a hyper `<connection>` with `<refresh increment-key=… incremental-updates=…>` and `<refresh-event>` history, extract `<filter>`s, and `<aggregation column=… derivation=…>` roll-ups.

Hidden fields are simply absent from the extract. On AWS, the extract becomes an Iceberg table materialised by CTAS/INSERT, with filters, aggregation and incremental keys replayed as SQL. TabFree must not re-apply the filters at query time because they are already baked into the data.

### Cited Findings
- XSD for `<extract>`: children are Connection-G, `Filter-G*` and `DataSource-Aggregation-G*` (the latter is `<aggregation column=QualifiedName derivation=AggType>`). Attributes: `enabled` (required), `units` (`SortUnits-ST`: `percent records sample-percent sample-records`), `count` (required), `object-id` (required), `type` (`normalized`), `user-specific`. `RefreshType-ST` values: `create increment add from data source add from file` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Observed incremental extract — [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb):
```xml
<extract …user-specific='false' count='-1' enabled='false' object-id='Migrated Data' units='records'>
  <connection access_mode='readonly' authentication='auth-none' class='hyper' dbname='…/TS Background Tasks.hyper' schema='Extract' …>
    <relation name='Extract' table='[Extract].[Extract]' type='table' />
    <cols><map key='[Backgrounder Id]' value='[Extract].[Backgrounder Id]' /> …</cols>
    <refresh increment-key='[Job Id]' incremental-updates='true'>
      <refresh-event add-from-file-path='TS Background Tasks' increment-value='%null%' refresh-type='create' rows-inserted='1494934' timestamp-start='2023-03-21 21:39:04.913' />
      <refresh-event add-from-file-path='TS Background Tasks' increment-value='103174165' refresh-type='increment' rows-inserted='364' timestamp-start='2023-03-21 21:58:57.473' />
    </refresh>
    <metadata-records> … </metadata-records>
```
- Extract option semantics — [Extract your data](https://help.tableau.com/current/pro/desktop/en-us/extracting_data.htm):
  - *Logical tables* (the default) apply joins at extract time. *Physical tables* keep joins at query time, need equality joins with identical join-column types, and disable extract filters, aggregation, Top N and sampling.
  - *Aggregate data for visible dimensions* uses each measure's default aggregation; *Roll up dates* rounds to a date level.
  - Aggregation is incompatible with incremental refresh.
  - Number of Rows is All, Sample or Top N, and "Tableau first applies any filters and aggregation".
  - Incremental refresh requires All rows. Since 2024.1 it supports non-unique keys (delete rows with the last recorded max value, then re-query rows ≥ it). Since 2024.2 a subrange can be re-extracted.
  - Extract filters are pervasive for a single base table and per-logical-table for multi-fact models.
  - "Any fields that you hide … will be excluded from the extract"; *Hide All Unused Fields* removes them.
  - Aggregation order can change floating-point results in `.hyper`.
- Datasource-level filters (data-source filters) appear inside `<datasource>` as `<filter class='categorical' column='[Calculation_993325236895637505]' filter-group='2'>` — [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb)
- Athena creates Iceberg v2 tables with `CREATE TABLE … PARTITIONED BY (…) LOCATION 's3://…' TBLPROPERTIES ('table_type'='ICEBERG', 'format'='parquet', 'write_compression'='snappy', …)`. Hidden-partition transforms `year/month/day/hour/bucket/truncate` are available; `CREATE EXTERNAL TABLE` is rejected for Iceberg; CTAS is supported — [Athena: Create Iceberg tables](https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg-creating-tables.html)

### Inferences
- **One-time migration.** Treat the `.hyper` contents as already filtered, aggregated and sampled, and copy them as-is (Q3). Persist the `<extract>` recipe in the JSON spec as `refreshRecipe`, so a future refresh pipeline can rebuild from the original source if one still exists.
- **Replaying the recipe on Athena (Trino SQL).**
  - The extract filter becomes a `WHERE`.
  - "Aggregate visible dims" becomes `GROUP BY` over the non-hidden dimensions, with the default aggregation per measure from `<column aggregation=…>` or `<metadata-record><aggregation>`.
  - Roll-up becomes `date_trunc('<level>', col)`.
  - Top N becomes `ORDER BY … LIMIT N`; Sample becomes `TABLESAMPLE BERNOULLI(p)`. These are approximate equivalents, not identical to Tableau's sampling.
- **Incremental replay.** `INSERT INTO lake.t SELECT … FROM src WHERE key > (SELECT max(key) FROM lake.t)`, or Iceberg `MERGE INTO` for the 2024.1+ non-unique-key semantics: delete rows where key equals the old max, then insert key ≥ old max.
- **Field mapping.** The extract's `<cols><map key='[Field]' value='[Extract].[Column]'>` is the authoritative field → physical-column map for the migrated table.
- **RLS caveat.** A `user-specific='true'` extract, or virtual-connection data policies, means the extract holds a per-user subset or needs user filtering. Make such sources live user-filtered views (Q5 user functions) rather than static tables.

### Gaps
- No observed example of the extract-level `<filter>` or `<aggregation>` children; their exact derivation strings for roll-up (for example `Month-Trunc` vs `TruncMonth`) are unverified.
- How multi-table (`normalized`) extracts name their Hyper tables and map `object-id`s is unverified.
- Athena `MERGE INTO` for Iceberg was not re-verified in this session.

---

## Q5. How does the Tableau calculation language translate to Trino/Athena and Amazon Redshift SQL?

### Takeaway
Tableau calcs are an expression language with four evaluation scopes: row-level, aggregate, LOD (Q6) and table-calc (Q7). They also carry Tableau-specific semantics that a naive textual translation gets wrong:
- `/` always returns a float, and dividing by zero gives NULL.
- `INT` truncates toward zero.
- A NULL test falls through to `ELSE`, but `IIF` returns NULL.
- Week start comes from the data source `date-options`, and fiscal years are set per field.
- Regex and `DATEPARSE` use ICU dialects.
- `ATTR`, user functions (RLS) and `RAWSQL` pass-through all need special handling.

Build a typed AST and per-dialect templates, the same way Tableau's own `.tdd` dialects map every function. Validate against Tableau's MIT-licensed TDVT expression suite, which has about 860 test files with expected results.

### Cited Findings
- Function catalogue by category — [Functions (by category)](https://help.tableau.com/current/pro/desktop/en-us/functions_all_categories.htm):
  - Number: ABS … DIV, HEXBINX/Y, ZN
  - String: ASCII … SPLIT, STARTSWITH, TRIM, UPPER
  - Date: DATE, DATEADD, DATEDIFF, DATENAME, DATEPARSE, DATEPART, DATETRUNC, ISO*, MAKEDATE/MAKEDATETIME/MAKETIME, NOW, TODAY, WEEK …
  - Logical: CASE, IF, IFNULL, IIF, IN, ISNULL, ZN …
  - Aggregate: ATTR, AVG, COLLECT, CORR, COUNT, COUNTD, COVAR(P), MAX, MEDIAN, MIN, PERCENTILE, STDEV(P), SUM, VAR(P)
  - User: FULLNAME, ISFULLNAME, ISMEMBEROF, ISUSERNAME, USERDOMAIN, USERNAME, USERATTRIBUTE, USERATTRIBUTEINCLUDES
  - Table calculation, including MODEL_* and SCRIPT_*
  - Pass-through: RAWSQL_BOOL/DATE/DATETIME/INT/REAL/STR and the RAWSQLAGG_* variants
- Operators — [Operators](https://help.tableau.com/current/pro/desktop/en-us/functions_operators.htm):
  - Precedence is negation, then `^`, then `* / %`, then `+ -`, then comparisons, NOT, AND, OR.
  - `+` concatenates strings.
  - "Unknown booleans are automatically converted to Null".
  - `%` only applies to integers.
  - date ± number means days, and date − date gives days.
- Logical semantics — [Logical functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_logical.htm):
  - IIF: "If the optional unknown isn't specified, `IIF` returns null."
  - IF: "any unknown (null) results are handled by the same ELSE clause as false results".
  - CASE treats NULL the same way. Its expression "cannot be a logical operation", and it supports `WHEN IN` (set, literal list or combined field).
  - The IN operator's right side can be a set, list or combined field.
  - AND/OR short-circuit; the documented AND rule says either NULL operand gives NULL.
  - ZN returns 0 for NULL; ISDATE is locale-dependent.
- Type conversion — [Type conversion](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_typeconversion.htm):
  - INT "truncates results to the closest integer toward zero" (`INT(8/3) = 2`, `INT(-9.7) = -9`).
  - A string → int conversion goes to float first and then rounds.
  - DATE() auto-recognises many string formats.
  - Booleans cast to 1/0/"1"/"0", and Unknown becomes Null.
- Date functions — [Date functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_date.htm):
  - DATEDIFF, DATENAME, DATEPART and DATETRUNC take an optional `start_of_week`; "If it is omitted, the start of week is determined by the data source".
  - date_part values: `year quarter month dayofyear day weekday week hour minute second iso-year iso-quarter iso-week iso-weekday`.
  - Week boundaries: `DATEDIFF('week', #2013-09-22#, #2013-09-24#, 'monday') = 1` but `… 'sunday') = 0`.
  - `DATETRUNC('iso-week', #9/22/2018#) = #9/17/2018#`.
  - MAKEDATETIME is only for MySQL-compatible connections.
- Fiscal years — [Fiscal dates](https://help.tableau.com/current/pro/desktop/en-us/dates_fiscal.htm):
  - Only Year, Quarter and Week Number change.
  - Fiscal year is labelled by its end year: June 1, 2004 is "FY 2005" with an April start.
  - The setting is per date dimension, applies to all instances of the field, and is relational-only.
  - The datasource-level `<date-options start-of-week=… fiscal-year-start=…>` is in [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- User functions — [User functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_user.htm):
  - ISMEMBEROF requires a literal, returns NULL when the user is not signed in, accepts AD groups as `'domain.lan\\Sales'`, and membership changes only apply to new sessions.
  - USERATTRIBUTE(INCLUDES) reads JWT, OIDC or SAML attributes: connected apps on Cloud and Server 2025.1+, unified access tokens on Cloud from Dec 2025, OIDC/SAML on Server 2026.2. They return NULL/FALSE while authoring.
  - The documented RLS pattern is `[Regional Manager] = USERNAME() OR ISMEMBEROF("National Managers")` used as a filter.
- The Metadata API `CalculatedField` exposes `formula`, `hasUserReference` ("True if the formula uses a user function"), `fields`, `parameters` and upstream lineage — [Metadata API CalculatedField](https://help.tableau.com/current/api/metadata_api/en-us/reference/calculatedfield.doc.html)
- Tableau's connector dialect template sample (MIT) — [FullFunctionMap.tdd](https://github.com/tableau/connector-plugin-sdk/blob/master/samples/components/dialects/FullFunctionMap.tdd), licence [connector-plugin-sdk](https://github.com/tableau/connector-plugin-sdk):
  - 305 `<function>` entries and 10 `<date-function>` entries.
  - `/` (int,int): `(CASE WHEN %2 = 0 THEN CAST(NULL AS FLOAT64) ELSE CAST(%1 AS FLOAT64) / %2 END)`.
  - `/` (real,real): `(CASE WHEN %2 <> 0 THEN %1 / %2 END)`.
  - `DIV`: `CASE WHEN %2 = 0 THEN NULL ELSE ( %1 / %2 ) END`.
  - `ZN`: `COALESCE(%1, 0)`.
  - `DATEPART(week)` and `DATETRUNC(week)` take a third `localstr` start-of-week argument.
- TDVT expression tests (MIT) — [TDVT exprtests](https://github.com/tableau/connector-plugin-sdk/tree/master/tdvt/tdvt/exprtests/standard), [example test](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/tdvt/exprtests/standard/setup.date.datepart.dateadd.defect603107.txt):
  - About 860 `exprtests` text files, covering for example `setup.date.datediff.sow.*` (start-of-week), `setup.date.dateadd.weekday`, casts, aggregates and logical tests.
  - Each test pairs a Tableau formula, such as `DATEPART('year',DATEADD('day',-DATEPART('weekday',[date0])+1,[date0]))`, with an `expected.*.txt` file holding `<results><test name=…><table><tuple><value>%null%</value>…`.
- Trino semantics — [Trino date/time functions](https://trino.io/docs/current/functions/datetime.html), [Trino conversion functions](https://trino.io/docs/current/functions/conversion.html):
  - `date_trunc('week')` truncates to Monday (example 2001-08-22 → 2001-08-20).
  - `day_of_week` runs from 1 (Monday) to 7 (Sunday) (ISO).
  - `week`/`week_of_year` are ISO weeks; `year_of_week` is the ISO year.
  - `date_diff(unit, t1, t2)` = "timestamp2 - timestamp1 expressed in terms of unit".
  - `date_format` uses MySQL-style specifiers, but `%D %U %u %V %w %X` are unsupported.
  - `format_datetime` and `parse_datetime` use Joda patterns.
  - `try_cast` returns NULL on failure.
- Redshift `DATE_TRUNC('week', …)` returns the Monday and the return type is TIMESTAMP — [Redshift DATE_TRUNC](https://docs.aws.amazon.com/redshift/latest/dg/r_DATE_TRUNC.html). An AWS re:Post thread says the Redshift docs list `IS DISTINCT FROM` as unsupported even though it appears to execute — [re:Post](https://repost.aws/questions/QUE_rlC6q9QO21RIsnz81pEw/is-the-is-distinct-from-operator-officially-supported-in-redshift)
- sqlglot (MIT) has `athena`, `trino`, `presto`, `redshift`, `tsql`, `snowflake`, `bigquery`, `postgres`, `mysql`, `oracle`, `teradata`, `databricks`, `spark`, `hive`, `dax` and `tableau` dialects — [sqlglot dialects](https://github.com/tobymao/sqlglot/tree/main/sqlglot/dialects)

### Inferences
**Translation table** (T = Trino/Athena engine v3, R = Redshift). These are proposed templates; validate each against TDVT.

| Tableau | Trino/Athena | Redshift | Semantic notes |
|---|---|---|---|
| `a / b` | `CASE WHEN b = 0 THEN NULL ELSE CAST(a AS double) / b END` | `CASE WHEN b = 0 THEN NULL ELSE a::float8 / b END` | Always float; zero divisor gives NULL (FullFunctionMap). SQL int/int would otherwise truncate. |
| `DIV(a,b)` | `CASE WHEN b=0 THEN NULL ELSE a / b END` (ints) | same | Integer division. |
| `a % b` | `mod(a,b)` | `a % b` | Ints only. |
| `'x' + 'y'` | `a \|\| b` | `a \|\| b` | NULL propagates. |
| `INT(x)` numeric | `CAST(truncate(x) AS bigint)` | `CAST(TRUNC(x) AS BIGINT)` | Toward zero; a plain CAST may round (verify). |
| `INT(str)` | `CAST(round(CAST(s AS double)) AS bigint)` | `ROUND(s::float8)::bigint` | Per the docs: float first, then round. |
| `FLOAT(x)`, `STR(x)` | `CAST(x AS double)`, `CAST(x AS varchar)` | `x::float8`, `x::varchar` | STR formatting of floats and dates differs (needs a test). |
| `DATE(str)` | `try(CAST(s AS date))` or `date_parse` via format guess | `TO_DATE(s, fmt)` | Tableau parses locale-dependent formats; resolve at conversion time using sample data. |
| `IIF(t,a,b[,u])` | `CASE WHEN t THEN a WHEN NOT t THEN b ELSE u_or_NULL END` | same | Not `ELSE b`: a NULL test returns NULL or u. |
| `IF…ELSEIF…ELSE…END`, `CASE` | `CASE WHEN …` | same | NULL test falls to ELSE, same as SQL. |
| `ZN(x)`, `IFNULL(a,b)`, `ISNULL(x)` | `coalesce(x,0)`, `coalesce(a,b)`, `x IS NULL` | same | |
| `x IN (…)` / `IN [Set]` | `x IN (…)` / semi-join to set CTE | same | Sets are computed (Q6 stage 4). |
| `LEN, LOWER, UPPER, TRIM/LTRIM/RTRIM, REPLACE` | `length, lower, upper, trim…, replace` | `LEN/LENGTH, …` | |
| `LEFT(s,n)`, `RIGHT(s,n)`, `MID(s,i,n)` | `substr(s,1,n)`, `substr(s,-n)` (guard n=0), `substr(s,i,n)` | `LEFT`, `RIGHT`, `SUBSTRING` | 1-based. |
| `FIND(s,t[,i])`, `FINDNTH(s,t,n)` | `strpos(s,t)` (offset via substr), `strpos(s,t,n)` | `STRPOS`/`POSITION`, regex-escaped `REGEXP_INSTR(s,t,1,n)` | 0 when not found. |
| `CONTAINS, STARTSWITH, ENDSWITH` | `strpos(s,t)>0`, `starts_with(s,t)`, `substr(s,-length(t))=t` | `POSITION(t IN s)>0`, `LEFT(...)`, `RIGHT(...)` | Avoid LIKE escape pitfalls. |
| `SPLIT(s,d,n)` | `n>0: split_part(s,d,n)`; `n<0: element_at(split(s,d), n)` | `SPLIT_PART(s,d,n)` (negative n: verify) | Tableau supports negative tokens. |
| `PROPER(s)` | lambda `regexp_replace` capitalisation | `INITCAP(s)` | No Trino builtin. |
| `REGEXP_MATCH/EXTRACT/EXTRACT_NTH/REPLACE` | `regexp_like/regexp_extract(s,p,1)/…(s,p,n)/regexp_replace` | `REGEXP_INSTR>0`, `REGEXP_SUBSTR(s,p,1,1,'e')`, `REGEXP_REPLACE` | Tableau ICU regex vs Trino Java/joni vs Redshift POSIX. Lint patterns for lookarounds and `\b` differences. |
| `DATEPART('year'/'quarter'/'month'/'day'/'dayofyear')` | `year/quarter/month/day/day_of_year` | `EXTRACT(… FROM d)` | Fiscal-aware fields use the fiscal formulas below. |
| `DATEPART('weekday', d)` with start S | `((day_of_week(d) - S_iso + 7) % 7) + 1` | `((DATE_PART(dow,d)::int - S_dow + 7) % 7) + 1` | Trino ISO Mon=1; Redshift dow Sun=0. |
| `DATEPART('week', d)` with start S (non-ISO) | `CAST(floor((day_of_year(d) - 1 + wd0(date_trunc('year',d),S)) / 7) AS int) + 1` | analogous | `wd0` is the 0-based weekday of Jan 1 relative to S. The week containing Jan 1 is week 1. |
| `DATETRUNC('week', d, S)` | `date_add('day', -((day_of_week(d) - S_iso + 7) % 7), date_trunc('day', d))` | `DATEADD(day, -((DATE_PART(dow,d)::int - S_dow + 7) % 7), DATE_TRUNC('day', d))` | Native week truncation is Monday-only on both engines. |
| `DATETRUNC(unit, d)` | `date_trunc(unit, d)` (cast to timestamp) | `DATE_TRUNC(unit, d)` | Tableau returns datetime. |
| ISO parts | `year_of_week`, `week`, `day_of_week` | `DATE_PART('isoyear'/'week'/…)` (verify) | |
| Fiscal year/quarter (start month M) | `year(date_add('month', 13-M, d))`, `quarter(date_add('month', 13-M, d))` | `DATE_PART(year, DATEADD(month, 13-M, d))` … | Labelled by end year, per the doc example. Fiscal DATETRUNC year: `date_add('month', M-13, date_trunc('year', date_add('month', 13-M, d)))`. |
| `DATEADD(part,n,d)` | `date_add(part,n,d)` | `DATEADD(part,n,d)` | Week and weekday parts: verify with TDVT `dateadd.weekday`. |
| `DATEDIFF(part,d1,d2[,S])` | Boundary counting: day `date_diff('day', CAST(d1 AS date), CAST(d2 AS date))`; month `(year(d2)*12+month(d2))-(year(d1)*12+month(d1))`; week `date_diff('day', wk(d1,S), wk(d2,S))/7` | `DATEDIFF(part, …)` (boundary-based, verify); week via truncated dates | Tableau counts boundaries per the doc example. Trino counts elapsed units. |
| `DATENAME('month', d)` | `date_format(d,'%M')` | `TO_CHAR(d,'FMMonth')` | Locale/language differences. |
| `DATEPARSE(icu_fmt, s)` | `parse_datetime(s, joda_fmt)` | `TO_TIMESTAMP(s, pg_fmt)` | Needs an ICU → Joda/Postgres format converter. |
| `NOW()`, `TODAY()` | `current_timestamp`, `current_date` | `GETDATE()`, `CURRENT_DATE` | Timezone of the Tableau Server vs the engine session. |
| `MAKEDATE(y,m,d)` | `date(format('%04d-%02d-%02d', y, m, d))` | `TO_DATE(…,'YYYY-MM-DD')` | |
| `COUNTD(x)` | `count(DISTINCT x)` (or `approx_distinct` only if flagged) | `COUNT(DISTINCT x)` | |
| `MEDIAN(x)`, `PERCENTILE(x,p)` | `approx_percentile(x, p)` (approximate) | `MEDIAN(x)`, `PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY x)` | Trino has no exact median; use array sort for exactness. |
| `STDEV/STDEVP/VAR/VARP/CORR/COVAR(P)` | `stddev_samp/…/corr/covar_samp/covar_pop` | `STDDEV_SAMP…` (CORR/COVAR availability: verify) | |
| `ATTR(x)` | `CASE WHEN min(x) = max(x) OR (count(x)=0) THEN min(x) END` plus an `attr_multi` flag (`min<>max`) for "*" display | same | Tableau shows `*` when values vary. |
| `MIN(a,b)`/`MAX(a,b)` | `least/greatest` | `LEAST/GREATEST` | Redshift and Postgres ignore NULLs in GREATEST; Trino returns NULL (verify). |
| `MAKEPOINT(lat,lon)` / `DISTANCE` | `ST_Point(lon,lat)` / `great_circle_distance` or SphericalGeography `ST_Distance` | `ST_Point(lon,lat)` / `ST_DistanceSphere` | Argument order flips; units differ. |
| `RAWSQL_*("sql %1", args)` | substitute args, then `sqlglot.transpile(sql, read=<source dialect>, write='trino')` | `write='redshift'` | Source dialect comes from the connection class (Q9). Always flag for review. |
| `USERNAME()`, `FULLNAME()`, `USERDOMAIN()`, `ISMEMBEROF('g')`, `USERATTRIBUTE('a')` | bind parameters injected by TabFree's query service from the viewer's session (`:username`, groups array, JWT claims) | same, or Redshift RLS policies | Never compile to the engine's `current_user`, which is the IAM principal. |

- **Week start precedence.** Use the explicit `start_of_week` argument if present, else the datasource `<date-options start-of-week>`, else the locale default (US Sunday). `seven-day-period` needs special handling.
- **Fiscal precedence.** Use the column-level `fiscal-year-start` if present, else the datasource `fiscal-year-start`.
- **RLS.** Use `hasUserReference` from the Metadata API, or scan the AST, to tag RLS calcs and datasource filters. Compile them into mandatory predicates that the query service always applies. Group membership comes from a TabFree identity table synced from Tableau groups.
- **Conformance suite.** Run each of about 860 TDVT expression files against TabFree's `Calcs`/`Staples` tables loaded in Athena and Redshift, and diff against `expected.*.txt`. This gives a measurable per-function accuracy score.

### Gaps
- Not verified in this session:
  - Trino `CAST(double AS integer)` rounding behaviour: search did not confirm it.
  - Redshift `DATEDIFF` boundary semantics, ISO date parts, negative `SPLIT_PART`, CORR/COVAR, GREATEST NULL handling, and TRY_CAST availability.
  - The exact Tableau `STR()` formatting of floats and dates.
- The Tableau AND/OR NULL truth table is ambiguous in the docs: the "either NULL gives NULL" text conflicts with the short-circuit text. Settle it with the TDVT logical tests.
- DATEPARSE support across connectors is self-contradictory on the Tableau page.

---

## Q6. How do LOD expressions (FIXED/INCLUDE/EXCLUDE) translate to SQL, given filter order of operations?

### Takeaway
An LOD is a sub-aggregation at a declared grain, joined back to the view on the shared dimensions:
- **FIXED** is computed after extract, data-source and context filters but before dimension filters.
- **INCLUDE/EXCLUDE** are computed after dimension filters.
- **Coarser than the view:** values are replicated and the shelf aggregation is ignored.
- **Finer than the view:** values are re-aggregated.
- **Table-scoped** `{AGG(x)}` is FIXED with no dimensions, joined with a cross join.

Generate staged CTEs that follow the order of operations, with null-safe joins.

### Cited Findings
- Order of operations, as a text list — [OSU: Tableau order of operations](https://it.osu.edu/data/tableau/tableau-order-of-operations), consistent with [Tableau help](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm), whose full list is only in a flowchart image:
  1. Extract filters
  2. Data source filters
  3. Context filters
  4. Sets, conditional filters, top N, and FIXED LOD
  5. Dimension filters
  6. INCLUDE/EXCLUDE LOD and data blending
  7. Measure filters
  8. Forecasts, table calculations, clusters, totals
  9. Table calculation filters ("modify the view but do not filter the underlying data")
  10. Trend and reference lines
- Statements on the Tableau help page itself — [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm):
  - A context filter is "executed before any other filter that you create in a worksheet".
  - Top N and general dimension filters "appear in the same place".
  - "FIXED level of detail expressions are computed before dimension filters are applied".
  - "a dimension filter is applied before a table calculation".
- LOD syntax and rules — [Create LOD expressions](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod.htm):
  - Syntax is `{[FIXED | INCLUDE | EXCLUDE] <dims> : <aggregate expression>}`, and dimensions may be expressions such as `YEAR([Order Date])`.
  - FIXED ignores view filters other than context, data-source and extract filters.
  - `{MIN([Order Date])}` ≡ `{FIXED : MIN([Order Date])}`.
  - The inner expression must be aggregated, ATTR is not supported, and "Table calculations aren't permitted".
  - Nested LODs are allowed.
  - EXCLUDE "can't be used in row-level expressions".
  - Saved calcs are not matched by content: `EXCLUDE YEAR([Order Date])` does not exclude a calc defined as `YEAR([Order Date])`.
- Coarser vs finer — [LOD expressions and aggregation](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod_replication.htm):
  - Coarser means a subset of the view dimensions: values are replicated, and "the aggregation you specified is still shown … but it is ignored".
  - Finer means a superset: values "must be aggregated".
  - FIXED can be finer, coarser or the same as the view; INCLUDE "values will never be replicated"; EXCLUDE "always cause[s] replicated values".
  - LODs are wrapped in an aggregate on shelves "unless they're used as dimensions".
- "Cross join occur when there is no explicit field to join on". LOD is unsupported on cubes, Access, Jet-based Excel and text, MongoDB, Splunk and DataStax — [LOD data source constraints](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod_constraints.htm)
- LODs are expressed as a nested select, so performance depends on the database — [Create efficient calculations](https://help.tableau.com/current/pro/desktop/en-us/perf_efficient_calcs.htm) (via search snippet). A FIXED customer-cohort LOD "creates a subquery" that is "joined together with our orders table on customer ID" — [The SQL that Tableau writes for LODs](https://expandmapping.substack.com/p/the-sql-that-tableau-writes-for-lods)
- "Context filters (context='true') ARE respected by FIXED LOD; regular filters are not". A FIXED LOD column-instance typically uses derivation `Sum`, as in `[sum:Calculation_LOD_Fixed:qk]` — [tableau-mcp lod-fixed.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/lod-fixed.json)

### Inferences
**Staged query skeleton.**
- `base` holds rows after extract, data-source, RLS and context filters.
- `dimf` holds `base` after dimension filters.
- FIXED LODs, sets, Top N and conditional filters are computed from `base`.
- INCLUDE/EXCLUDE LODs are computed from `dimf`.
- Measure filters become `HAVING` at view grain.
- Table calcs and table-calc filters go in outer layers.
- Join keys must be null-safe: Trino `a IS NOT DISTINCT FROM b`; Redshift `(a = b OR (a IS NULL AND b IS NULL))`.

Example 1 — FIXED, coarser than the view (replicated): percent of category, with a dimension filter Region='West' and a context filter on year.
```sql
-- View: Rows = Category, Sub-Category ; Measure = SUM([Sales]) / SUM({FIXED [Category] : SUM([Sales])})
WITH base AS (SELECT * FROM lake.orders WHERE year(order_date) = 2025),          -- context filter
     lod_cat AS (SELECT category, SUM(sales) AS v FROM base GROUP BY category), -- ignores Region filter
     viz AS (SELECT category, sub_category, SUM(sales) AS s
             FROM base WHERE region = 'West' GROUP BY category, sub_category)   -- dimension filter
SELECT viz.category, viz.sub_category,
       CASE WHEN l.v = 0 THEN NULL ELSE CAST(viz.s AS double) / l.v END AS pct_of_category
FROM viz LEFT JOIN lod_cat l ON viz.category IS NOT DISTINCT FROM l.category;
```
Example 2 — FIXED, finer than the view (re-aggregated): average customer lifetime sales per region.
```sql
-- View: Rows = Region ; Measure = AVG({FIXED [Customer ID] : SUM([Sales])})
WITH base AS (...), lod AS (SELECT customer_id, SUM(sales) AS v FROM base GROUP BY customer_id),
     grain AS (SELECT DISTINCT region, customer_id FROM base WHERE <dimension filters>) -- view dims ∪ LOD dims
SELECT g.region, AVG(l.v) FROM grain g JOIN lod l ON g.customer_id IS NOT DISTINCT FROM l.customer_id
GROUP BY g.region;
```
Example 3 — INCLUDE (computed after dimension filters; never replicated).
```sql
-- AVG({INCLUDE [Customer ID] : SUM([Sales])}) by Region
WITH dimf AS (SELECT * FROM base WHERE <dimension filters>),
     inc AS (SELECT region, customer_id, SUM(sales) AS v FROM dimf GROUP BY region, customer_id)
SELECT region, AVG(v) FROM inc GROUP BY region;
```
Example 4 — EXCLUDE (replicated). The window shortcut is valid only for decomposable aggregates (SUM, MIN, MAX, COUNT), not COUNTD, AVG or MEDIAN.
```sql
-- View: Region x MONTH(Order Date); SUM([Sales]) - SUM({EXCLUDE [Region] : SUM([Sales])})
WITH dimf AS (...),
     viz AS (SELECT region, date_trunc('month', order_date) AS m, SUM(sales) AS s FROM dimf GROUP BY 1, 2),
     exc AS (SELECT date_trunc('month', order_date) AS m, SUM(sales) AS s FROM dimf GROUP BY 1)
SELECT viz.region, viz.m, viz.s - exc.s FROM viz JOIN exc ON viz.m = exc.m;
-- equivalent for SUM only:  s - SUM(s) OVER (PARTITION BY m)
```
Example 5 — FIXED used row-level or as a dimension (cohorts). Join at row grain before grouping.
```sql
WITH first_order AS (SELECT customer_id, MIN(order_date) AS f FROM base GROUP BY customer_id)
SELECT year(fo.f) AS cohort, year(b.order_date) AS yr, SUM(b.sales)
FROM base b JOIN first_order fo ON b.customer_id IS NOT DISTINCT FROM fo.customer_id
WHERE <dimension filters> GROUP BY 1, 2;
```
Example 6 — table-scoped: `{SUM([Sales])}` becomes `CROSS JOIN (SELECT SUM(sales) AS total FROM base) t`.

**Stage-4 filters.**
- Top N: `dim IN (SELECT dim FROM base GROUP BY dim ORDER BY SUM(m) DESC LIMIT N)`. N can be a parameter value. Ties must be resolved (see Gaps).
- Condition filter: `dim IN (SELECT dim FROM base GROUP BY dim HAVING <cond>)`.
- Sets: materialise as `set_<name>(member)` CTEs from `<groupfilter>` trees.
- Set and Top-N subqueries read `base` (post-context), never `dimf`.

**Nested LODs and dimension expressions.** Compile bottom-up into separate CTEs. A FIXED whose dimension is an expression, such as `{FIXED YEAR([d]) : …}`, joins on the computed expression.

### Gaps
- No primary-source SQL text was available for Tableau's actual LOD join plans: whether finer FIXED values are joined at the view∪LOD grain (my Example 2) or otherwise, the join types, and null-safety. Verify empirically with Performance Recorder, or by comparing Server CSV output (Q11) on test workbooks.
- Top-N tie handling and evaluation relative to FIXED are not documented in the sources found; both sit at the same stage.
- How FIXED interacts with data-blending secondaries is not covered by the docs found.

---

## Q7. How do table calculations translate: SQL window functions or client-side computation?

### Takeaway
Table calcs run locally over the view's aggregated "virtual table". Translate them in two layers:
- **SQL push-down.** Window functions over the view-level query: partitioning dimensions become `PARTITION BY`, addressing dimensions in sort order become `ORDER BY`.
- **Client-side engine.** Needed for densification, `PREVIOUS_VALUE`, non-additive `TOTAL`, "at the level/restarting every", and secondary calcs. A JS port or DuckDB-WASM over the result set both work.

The TWB's `<table-calc>` attributes encode compute-using fully.

### Cited Findings
- Model — [Table calculations](https://help.tableau.com/current/pro/desktop/en-us/calculations_tablecalculations.htm):
  - Computed "on the local data in Tableau", using only what is in the view.
  - The virtual table is defined by level-of-detail dimensions.
  - Every LOD dimension is either partitioning (scope) or addressing (direction).
  - Compute Using options: Table (across, down, across then down, down then across), Pane (down, across then down, down then across), Cell, and Specific Dimensions.
  - "At the level" (Deepest by default) restarts "by position rather than by value".
- Functions — [Table calculation functions](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_tablecalculation.htm):
  - FIRST is the offset to the first row (-2 at row 3); INDEX is 1-based; LAST is the offset to the last row.
  - `LOOKUP(expr, offset)` returns NULL when there is no row there.
  - `PREVIOUS_VALUE(seed)`: `SUM([Profit]) * PREVIOUS_VALUE(1)` gives a running product.
  - SIZE is the number of rows in the partition.
  - Ranking ignores NULLs. On the set (6, 9, 9, 14):

    | Function | Result | Default order |
    |---|---|---|
    | RANK | (4,2,2,1) | desc |
    | RANK_DENSE | (3,2,2,1) | desc |
    | RANK_MODIFIED | (4,3,3,1) | desc |
    | RANK_PERCENTILE | (0, .67, .67, 1) | **asc** |
    | RANK_UNIQUE | (4,2,3,1) | desc |

  - RUNNING_AVG/COUNT/MAX/MIN/SUM; `WINDOW_*(expr, [start, end])` takes offsets relative to the current row, `FIRST()+n`/`LAST()-n` anchor them, and omitting both means the whole partition.
  - `TOTAL(expr)` follows Compute Using.
  - SCRIPT_* and MODEL_* call external services.
- TWB encoding, from [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd) and [tableau-mcp table-calc.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/table-calc.json):
  - `<table-calc type=… ordering-type=… ordering-field=… level-address=… level-break=… from=… to=… window-options=…>`
  - Types: `CumTotal, WindowTotal, Difference, PctDiff, PctValue, PctTotal, Rank, PctRank, Custom`
  - Ordering types: `Table, TableCol, Rows, Columns, Pane, PaneCol, RowInPane, ColumnInPane, CellInPane, Field`
  - Example: `[usr:Calculation_X:qk:2]` with `ordering-type='Field'`
- Observed quick calc: `[pcto:ctd:query-hash:qk:2]` with `<table-calc ordering-type='Columns' type='PctTotal' />` — [QueryInfo.twb](https://github.com/tableau/tableau-log-viewer/blob/master/resources/workbooks/QueryInfo.twb)
- Relationship views show empty rows/columns via Table Layout "Show Empty Rows/Columns", which "also densifies date and numeric bin fields" — [Relationships: how analysis works](https://help.tableau.com/current/pro/desktop/en-us/datasource_multitable_analysis_overview.htm)
- VizQL Data Service accepts custom table calcs written in Tableau syntax, for example `"calculation": "WINDOW_AVG(SUM([Sales]), -2, 0)"` — [VDS custom table calcs](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_table_calcs_custom.html) (via search snippet)

### Inferences
**Window templates.** Let `viz` be the view-level aggregate CTE, `P` the partitioning dimensions, and `A` the addressing dimensions in Tableau's ordering. "Table (down)" with Rows = Region, Month and Cols = none gives `A` = row dimensions and `P` = column dimensions.
```sql
-- RUNNING_SUM(SUM([Sales]))                      (CumTotal)
SUM(s) OVER (PARTITION BY P ORDER BY A ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
-- WINDOW_AVG(SUM([Sales]), -2, 0)  [+ NullIfIncomplete]
AVG(s) OVER (PARTITION BY P ORDER BY A ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
  -- NullIfIncomplete: CASE WHEN COUNT(*) OVER (same frame) < 3 THEN NULL ELSE … END
-- WINDOW_SUM(SUM(x))  (whole partition)          SUM(s) OVER (PARTITION BY P)
-- LOOKUP(SUM(x), -1)                             LAG(s, 1) OVER (PARTITION BY P ORDER BY A)
-- LOOKUP(SUM(x), FIRST())                        FIRST_VALUE(s) OVER (PARTITION BY P ORDER BY A)
-- INDEX() / SIZE()                               ROW_NUMBER() OVER (…) / COUNT(*) OVER (PARTITION BY P)
-- FIRST() / LAST()                               1 - ROW_NUMBER() OVER(…) / COUNT(*) OVER (PARTITION BY P) - ROW_NUMBER() OVER(…)
-- Percent of total (PctTotal)                    CAST(s AS double) / NULLIF(SUM(s) OVER (PARTITION BY P), 0)
-- Difference / Percent difference (from previous) s - LAG(s) OVER(…)  /  (s - LAG(s) OVER(…)) / ABS(LAG(s) OVER(…))
-- RANK / RANK_DENSE / RANK_UNIQUE (desc)         RANK() / DENSE_RANK() / ROW_NUMBER() OVER (PARTITION BY P ORDER BY s DESC)
-- RANK_MODIFIED (desc)                           COUNT(*) OVER (PARTITION BY P ORDER BY s DESC RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
-- RANK_PERCENTILE (asc)                          (COUNT(*) OVER (PARTITION BY P ORDER BY s RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1)
--                                                 / NULLIF(COUNT(s) OVER (PARTITION BY P) - 1, 0)   -- reproduces (0,.67,.67,1)
-- Ranking ignores NULLs: add (s IS NULL) to PARTITION BY and return NULL for NULL s.
```
**Cases that need special handling.**
- `TOTAL(AVG(x))` is the average of underlying rows in the partition, not the average of averages. Compute it as an extra GROUP BY over `dimf` at the partition grain and join it back, like an EXCLUDE of the addressing dimensions.
- `PREVIOUS_VALUE` is recursive. Use a recursive CTE or the client side; special-case running products as `exp(sum(ln(x))) over (...)` when all values are positive.
- Secondary calcs (for example running sum, then percent of total) become successive CTE layers.
- Table-calc filters become an outer `WHERE` on the windowed result, so hidden marks still feed the calcs.
- Densification: Tableau inserts missing addressing members, such as months with no sales. Generate the domain with Trino `UNNEST(sequence(min, max, INTERVAL '1' MONTH))`, or a calendar table in Redshift, and cross join it with the partitions before windowing.
- "Specific Dimensions" sort can be by a measure (`ordering-field` plus sort). "At the level" and "Restarting every" (`level-address`, `level-break`) partition by the prefix of the addressing list up to the break.

**Recommendation.** Emit both a SQL push-down (for big partitions) and a declarative `tableCalcs[]` spec (type, P, A, frame, options) that the HTML runtime can recompute on the client. The client path is the source of truth when densification or exotic options are present.

### Gaps
- Default compute-using when `ordering-type` is absent, the exact ordering of addressing members (data order vs sort order), and densification triggers (when Tableau pads domains) are not documented in the sources found. Validate them empirically.
- Tableau's percent-difference formula details (absolute denominator) are not verified.

---

## Q8. Which grammars, parsers or translators already exist for Tableau formulas, and how accurate are they?

### Takeaway
I found no mature open-source grammar (ANTLR, Lark or PEG) or full translator for the Tableau calculation language, and no TWB-to-Vega-Lite converter.
- **Reusable pieces:** sqlglot's minimal `tableau` dialect; Tableau's own FullFunctionMap and TDVT (MIT); the Document API and tableau_tools for XML; Tableau's official MCP server (examples, validators, a Desktop calc validator).
- **Accuracy claims:** the only numbers are vendor-reported for Tableau → Power BI DAX tools (75–90% automation), with no independent benchmark.

### Cited Findings
- sqlglot's Tableau dialect (MIT) is narrow. It tokenizes `[ ]` identifiers and parses only `COUNTD` (→ `COUNT(DISTINCT)`), `FIND` and `FINDNTH` (→ `StrPosition`). Its generator emits `IF … THEN … ELSE … END`, `IFNULL` (for COALESCE) and `COUNTD`. There is no LOD, table-calc or date-semantics support — [tableau.py](https://github.com/tobymao/sqlglot/blob/main/sqlglot/dialects/tableau.py), [parsers/tableau.py](https://github.com/tobymao/sqlglot/blob/main/sqlglot/parsers/tableau.py), [generators/tableau.py](https://github.com/tobymao/sqlglot/blob/main/sqlglot/generators/tableau.py)
- Small community converters (0–1 stars):
  - Tableau→DAX: [ankitrathorelv/tableau-to-dax](https://github.com/ankitrathorelv/tableau-to-dax), [sparshmaheshwari11/Tableau_to_DAX_Convertor](https://github.com/sparshmaheshwari11/Tableau_to_DAX_Convertor)
  - Tableau→Looker: [SquareShiftTech/tableau-to-looker-accelerator](https://github.com/SquareShiftTech/tableau-to-looker-accelerator), [carinlotan/tableau-to-looker](https://github.com/carinlotan/tableau-to-looker) ("TWB Scanner")
  - Tableau→Superset: [ParityBI](https://github.com/Vishalsugur/ParityBI-Validated-Tableau-to-Superset-Migration)
- Other relevant projects:
  - Graphic Walker: Apache-2.0, about 3.3k stars, "An open source alternative to Tableau", with a Vega-Lite-like spec — [Kanaries/graphic-walker](https://github.com/Kanaries/graphic-walker)
  - TableauDesktopPy: "Tools for extracting metadata from Tableau Desktop workbook files" — [bpewyllie/TableauDesktopPy](https://github.com/bpewyllie/TableauDesktopPy)
- Tableau's official MCP server (Apache-2.0, updated 2026-11-20) — [tableau-mcp src/desktop](https://github.com/tableau/tableau-mcp/tree/main/src/desktop):
  - TWB XML example files for calcs, LOD, table calcs, filters, parameters, dual axis, dashboards and sorts.
  - `corpus.json` with 62 "workbook transformation examples with XML diffs".
  - Validation rules such as `selfReferentialFixedLod`.
- Vendor accuracy claims:
  - Pulse Convert claims 75–90% automated accuracy, with LOD-heavy dashboards at the low end — [AppSource listing](https://appsource.microsoft.com/en-sg/product/saas/officesolution1640276900203.pulseconvert1)
  - Kanerika FLIP claims 80% automation — [Kanerika](https://kanerika.com/services/migration/tableau-to-power-bi/)
  - MAQ Software uses Azure OpenAI GPT-4.1 to convert LODs to DAX, with no accuracy figure — [AppSource MAQ](https://appsource.microsoft.com/fr-be/marketplace/consulting-services/maqsoftware.tableaupbipromigration)
  - A secondhand claim says Microsoft guidance rules out complete automated conversion — [Cygnet blog](https://www.cygnet.one/feeds/blog/tableau-power-bi-migration)
- Tableau's 2016 "conversion tool" only changes workbook versions and is unsupported — [Tableau blog](https://www.tableau.com/blog/converting-tableau-files-new-conversion-tool-55326)

### Inferences
- **Write the parser in-house.** The grammar is small: literals including `#date#`, `[field]` and `[ds].[field]` references, operators, function calls, `IF/ELSEIF/CASE/WHEN/IN`, `{FIXED|INCLUDE|EXCLUDE dims : agg}`, and `//` and `/* */` comments. A Lark or ANTLR grammar of about 150 lines plus type inference is realistic.
- **Use sqlglot for the SQL side.** The TabFree AST should emit sqlglot expression trees, which sqlglot then renders for the `athena`/`trino` and `redshift` dialects. This also gives transpilation of RAWSQL and custom SQL from the source dialect.
- **Measure accuracy, don't trust it.** Use TDVT pass rates and per-worksheet data diffs against Tableau Server outputs (Q11) instead of vendor-style percentages.
- **Use an LLM as a fallback only.** Claude can translate leftover formulas (RAWSQL, regex edge cases), but outputs must pass the same conformance and data-diff gates.

### Gaps
- GitHub searches turned up no Tableau-calc ANTLR/Lark/PEG grammar: repository searches ("tableau calculation parser", "tableau formula grammar", "twb parser") and code searches (`.g4` files containing WINDOW_SUM, `.lark`/`.peggy` containing COUNTD). Private or commercial parsers may exist.
- No independent accuracy benchmark exists for any Tableau translator.

---

## Q9. How does VizQL build queries, and how can TabFree regenerate equivalent SQL per worksheet?

### Takeaway
A worksheet compiles deterministically:
- The Polaris table algebra on rows and columns plus the Marks-card encodings defines the visual level of detail: all discrete dimensions on rows, columns, color, size, shape, label, detail, tooltip, path and pages.
- Measures become aggregates.
- Filters are staged by the order of operations.
- With relationships, only the tables touched are queried, each at its native LOD, with inner joins for dimension-only views and left joins plus extra queries to keep unmatched measure values.

Table calcs, totals and densification are client-side post-processing. TabFree can therefore emit per-worksheet `queries[]` plus `postProcessing[]`.

### Cited Findings
- Polaris algebra (concatenation, cross, nest) defines the table structure, and VizQL reuses it — [Polaris paper](https://datavis.cs.columbia.edu/files/papers/polaris.pdf), [MSC arXiv](https://arxiv.org/pdf/2408.04798). The observed shelf `/` nesting is in [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)
- Relationship query semantics — [How analysis works for relationships](https://help.tableau.com/current/pro/desktop/en-us/datasource_multitable_analysis_overview.htm):
  - "Only data that is relevant to a viz is queried", "at its natural level of detail".
  - Dimension-only vizzes use inner joins.
  - Measures keep unmatched values via "left joins and extra queries".
  - Every table has a `TableName(Count)` field.
  - Referential integrity "All Values Match" reduces the number of queries.
  - Declared cardinality skips deduplication subqueries.
- Order of operations — [OSU list](https://it.osu.edu/data/tableau/tableau-order-of-operations), [Tableau help](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm); table calcs are local — [Table calculations](https://help.tableau.com/current/pro/desktop/en-us/calculations_tablecalculations.htm)
- Pane-level `<encodings>` (color, lod, text, size …) and `<rows>`/`<cols>` carry the full field set of a sheet, and `<slices>` lists the filter fields — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)
- Tableau Server's Performance Recorder exposes the executed SQL — [Create efficient calculations](https://help.tableau.com/current/pro/desktop/en-us/perf_efficient_calcs.htm) (via search snippet)

### Inferences
**Compiler outline.**
1. **Resolve the source relation.**
   - Physical layer: the `<relation>` tree becomes FROM/JOIN/UNION; custom SQL (`type='text'`) is transpiled; `pivot` becomes UNNEST/UNPIVOT.
   - Logical layer: choose the minimal connected set of objects touched by the sheet's fields. Use left joins from the measure-owning table and inner joins for dimension-only views. Aggregate each measure at its own object grain before joining, to avoid fan-out; this is the "native LOD" behaviour. Skip deduplication when `unique-key` and `guaranteed-value` permit.
2. **Collect column instances** from `datasource-dependencies` and classify them:
   - Dimension: derivation `None`, a date part or a Trunc.
   - Measure: an aggregate derivation.
   - User calc: `usr`. Check whether its AST is row-level, aggregate, LOD or table-calc.
   - Quick table calc: has a `<table-calc>`.
3. **Grain** = all discrete dimension instances on rows, columns, pages and every pane's encodings, plus continuous dimensions such as `tdy:` on an axis. Build one `GROUP BY` per distinct grain/filter set; concatenated measures with the same grain share a query.
4. **Filters by stage** (Q6). Categorical dimension filters (member lists, ranges, wildcard conditions) become `WHERE`. Quantitative measure filters (`<min>/<max>` on `sum:` instances) become `HAVING`. Relative-date filters become `WHERE d >= date_trunc(period, anchor) + …`, anchored on `now()` or a stored anchor. Top N, conditions and sets become semi-joins on `base`.
5. **Totals and subtotals.** `<subtotals>` and grand totals become extra queries at coarser grains, or `GROUPING SETS`/`ROLLUP` (Trino supports these; Redshift support unverified). Totals of non-additive measures must be re-aggregated from rows, not summed.
6. **Measure Names/Values.** Compute all measures in one query and unpivot in post-processing, using the `[:Measure Names]` filter members as the ordered measure list.
7. **Sorts.** Computed sorts become `ORDER BY agg(measure)` per dimension within its parent nest; manual sorts use a dictionary order list.
8. **Post-processing.** Densification, table calcs, table-calc filters, reference and trend lines, and forecasts.
9. **Data blending.** Run a second query on the secondary source grouped by the link fields (from `datasource-relationship` column-mappings) intersected with the view grain, then left-join it to the primary result. ATTR semantics apply to non-link dimensions.

Persist the generated SQL in the JSON spec so the AI-generated HTML calls the TabFree query service with a stable `queryId` plus parameter and filter bindings. The AI should never write SQL itself.

### Gaps
- VizQL internals (query fusion and batching, caching, exact densification rules, how blending ATTR stars appear in SQL) are not publicly documented in the sources found.
- Redshift GROUPING SETS/ROLLUP support is not re-verified.

---

## Q10. How do Tableau visual specs map to Vega-Lite, ECharts and Observable Plot, and what cannot be mapped?

### Takeaway
Most single-pane Tableau charts map cleanly to Vega-Lite, whose grammar descends from Polaris:
- Marks, faceting via rows/columns, layering and independent scales for dual axes.
- Window and joinaggregate transforms for table-calc-like logic.
- Regression and loess for trend lines.

ECharts covers treemaps, geo maps and large data better. Observable Plot is concise but has no dual axis.

What does not map: "Automatic" mark selection, nested crosstabs with subtotals, densification semantics, forecasting and clustering, Tableau geocoding and basemaps, viz-in-tooltip, and the dashboard layout and action semantics. These need TabFree runtime code. I found no existing TWB→Vega-Lite converter.

### Cited Findings
- Tableau mark classes: `Automatic Text Icon Shape Rectangle Bar GanttBar Square Circle Heatmap PolyLine Line Polygon Area Pie Multipolygon VizExtension`. Trend fits: `linear polynomial log exp power`. Reference-line formulas include `average median percentile quantiles stdev confidence` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Vega-Lite primitive marks are `area bar circle line point rect rule square text tick geoshape`, and composites are `boxplot errorband errorbar`. Docs exist for layer, facet, repeat, resolve (independent scales), window, joinaggregate, regression, loess and density — [Vega-Lite mark](https://vega.github.io/vega-lite/docs/mark.html), [resolve](https://vega.github.io/vega-lite/docs/resolve.html), [window](https://vega.github.io/vega-lite/docs/window.html), [regression](https://vega.github.io/vega-lite/docs/regression.html)
- Vega-Lite describes itself as "a higher-level grammar for visual analysis, akin to ggplot or Tableau" — [vega-lite README (fork copy)](https://github.com/hickeye/vega-lite)
- Dual axis in TWB, per tableau-mcp: concatenated measures with per-pane mark classes and an auto `__combined_axes` pane — [tableau-mcp dual-axis.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/dual-axis.json). The XSD has axis `fold`/`synchronized` attributes — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Web search found no TWB→Vega-Lite converter. The nearest work is the MSC paper, which maps VizQL nesting onto repeat/divide/densify operations — [MSC arXiv](https://arxiv.org/pdf/2408.04798)

### Inferences
**Mapping table** (VL = Vega-Lite, EC = ECharts, OP = Observable Plot).

| Tableau | VL | EC | OP | Notes |
|---|---|---|---|---|
| Bar (incl. stacked, side-by-side via nested dims) | `bar` + `stack` / `xOffset` | `bar` (stack) | `barX/barY` (+`fx`) | Tableau stacks by default when color is a dimension. |
| Line, Area | `line`, `area` | `line` (+`areaStyle`) | `line`, `areaY` | Path order: VL `order`, OP `sort`. |
| Circle / Square / Shape | `circle`/`square`/`point`+`shape` | `scatter` (symbol) | `dot` (symbol) | Custom shapes from the twbx `Shapes/` folder become images. |
| Text table / highlight table | HTML `<table>` (VL `text`+`rect` only for small tables) | — | `text`/`cell` | Crosstabs with nested headers and totals are better as HTML grids. |
| Square + Size, no axes (treemap) | none in VL (Vega treemap transform) | `treemap` | (d3 treemap) | |
| Pie | `arc` (theta) | `pie` | — | |
| GanttBar | `bar` with `x`/`x2` | custom or stacked-bar trick | `barX` x1/x2 | |
| Polygon / PolyLine | `line`/`area` with `order`, or `geoshape` | `custom` | `line`/`area` | |
| Multipolygon / filled map | `geoshape` + `lookup` | `map` series (registered GeoJSON) | `geo` | Needs open boundary data in place of Tableau geocoding. |
| Heatmap (density) | approximate with binned `rect`/hexbin | `heatmap` | `density` | |
| Dual axis | `layer` + `resolve: {scale: {y: "independent"}}` | multiple `yAxis` | — (manual) | Honour `synchronized`. |
| Rows/Cols small multiples | `facet` row/column | multiple `grid`s | `fx`/`fy` | |
| Measure Names/Values | `fold` transform + color by key | dataset transform | long-format data | |
| Reference line/band | `rule`/`rect` layer with aggregate | `markLine`/`markArea` | `ruleY`/`rect` | Per-cell, pane or table scope matters. |
| Trend line | `regression` (`linear/log/exp/pow/poly`) / `loess` | ecStat plugin | `linearRegressionY` | Tableau's p-values and R² tooltips need custom code. |
| Box plot (reference-line boxplot) | `boxplot` | `boxplot` | `boxY` | Whisker rules (`boxplot-whisker-type`). |
| Forecast, clusters, Explain Data | — | — | — | Precompute server-side or drop. |

**Recommended rendering strategy.**
1. Default to Vega-Lite for standard sheets: declarative, easy to diff, and Claude Code generates it reliably.
2. Use ECharts for maps, treemaps, Sankey-like extension visuals and large mark counts.
3. Use HTML tables for crosstabs.
4. The dashboard zone tree becomes CSS grid/flex: `layout-flow` `horz`/`vert` maps to flex-row/column, `distribute-evenly` to equal flex, and `is-fixed`/`fixed-size` to fixed px. Root coordinates scale from 0–100000 to the `<size>` px.
5. Floating zones become absolute positioning.
6. Actions become JS event bindings in a small TabFree runtime: filter is a cross-filter via the query service, highlight is local selection, URL is `window.open` with field substitution, parameter and set actions mutate parameters or sets and then re-query.

### Gaps
- "Automatic" mark-type resolution (Show Me rules) is undocumented. Infer it from the shelves (date on columns gives a line, and so on) or read the mark from the Tableau-rendered image.
- No evaluation of AI-generated Vega-Lite fidelity against Tableau renders was found.
- Palettes and formatting (`style-rule` encodings, `default-format` strings such as `c"$"#,##0.00`) need a separate mapping table; not researched in depth here.

---

## Q11. How can Tableau Server-rendered images, PDFs and CSVs serve as ground truth for AI recreation and regression testing?

### Takeaway
Before decommissioning Tableau Server, capture per view:
- a PNG (Query View Image, `resolution=high`, fixed `vizWidth`/`vizHeight`), a PDF, the summary CSV (Query View Data) and the crosstab Excel;
- the same views re-captured under filter and parameter permutations, using `vf_<field>=` and `vf_Parameters.<name>=` (exact-match only);
- calc formulas, lineage and RLS flags from the Metadata API;
- calc-level oracle results from VizQL Data Service (published data sources, Server 2025.1+).

Then run three automated diffs: data diff (TabFree SQL vs CSV), visual diff (SSIM or perceptual), and formula diff (TDVT and VDS).

### Cited Findings
- View-filter syntax — [REST filtering and sorting](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_concepts_filtering_and_sorting.htm):
  - Works for Query View Image, PDF, Data and Download View Crosstab Excel (also custom views and workbook PDF).
  - `?vf_<field>=value`, commas for OR-lists, and positional pairing across keys.
  - Datetime format `yyyy-mm-dd hh:mm:ss`.
  - No wildcards, ranges or comparisons; names and values must match exactly.
  - Filtering on fields outside the view can mislead.
- Tableau Server Client semantics — [TSC request_options.py](https://github.com/tableau/server-client-python/blob/development/tableauserverclient/server/request_options.py):
  - `vf()` serialises to `vf_<name>=<value>`; parameters serialise as `vf_Parameters.<name>`.
  - Escaping applies to `,` and `\` only (`\,`).
  - An empty value "is observed to override any workbook-embedded filter on that column".
  - `*` and `%` are literal; these behaviours were empirically verified against REST API 3.30 in August 2026.
  - Image and PDF requests send `vizHeight`/`vizWidth`, which must be set together.
  - `maxAge` is at least 1 minute.
  - CSV/Excel options take the same filters.
  - Image `imageresolution` is "high" or None.
- Query View Image takes `resolution` (`standard`/`high`) and `maxAge`; Query View Data `GET …/views/{id}/data` returns CSV — [OpenAPI mirror (qodex)](https://developers.qodex.ai/salesforce-developers/tableau-apis/tableau-rest-api-workbooks-and-views-1/query-view-image) (secondary source)
- VizQL Data Service — [VDS overview](https://help.tableau.com/current/api/vizql-data-service/en-us/index.html), [VDS create queries](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_create_queries.html), [VDS limitations](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_limitations.html):
  - Endpoints `POST …/api/v1/vizql-data-service/query-datasource` and `…/read-metadata`; Server 2025.1+ and Cloud.
  - Fields can carry a `calculation` in Tableau syntax, for example `"SUM([Profit])/SUM([Sales])"`.
  - Not supported: spatial, SCRIPT_*, Analytics Extensions, RAWSQL, fiscal-date calcs, `COUNT(table)`, sets and combined fields, cube sources, HOUR/MINUTE date aggregations; only one filter per field.
  - The 1 GB response cap was removed in 2026.1; queries time out after 30 minutes.
  - The quota is 100 queries per hour per Creator licence.
- The Metadata API `CalculatedField.formula`, `hasUserReference`, `fields` and `upstreamTables` provide formula and lineage ground truth independent of XML parsing — [Metadata API CalculatedField](https://help.tableau.com/current/api/metadata_api/en-us/reference/calculatedfield.doc.html)
- Tableau Desktop's local External Client API, via the official MCP server, offers *Get Summary Data* (aggregated rows of a worksheet, `maxRows` 1–1000, Sept 2026+). Tool docs also exist for `export-dashboard-image` and `validate-calculated-field` (Nov 2026+) — [get-summary-data](https://github.com/tableau/tableau-mcp/blob/main/docs/docs/desktop-tools/data-source/get-summary-data.md), [validate-calculated-field](https://github.com/tableau/tableau-mcp/blob/main/docs/docs/desktop-tools/data-source/validate-calculated-field.md)
- TWBs embed `<thumbnails>` (XSD `Workbook-Thumbnails-G`) — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)

### Inferences
**Harvest plan per view.**
1. Capture `PNG@high` at a fixed `vizWidth`/`vizHeight` that matches the dashboard `<size>`, plus the CSV.
2. Repeat for N filter and parameter permutations generated from the TWB's filter domains, using `vf_` (exact values only) and `vf_Parameters.`.
3. Store the results in S3 next to the JSON spec as `groundTruth[]`.

**AI recreation.** Give Claude the JSON spec, the PNG and the CSV. Have it generate HTML against the TabFree query service, render headless (Playwright), and iterate until:
- data diff = 0, after normalising number formats, sort order, NULL and blank, and Measure Names long and wide forms;
- image SSIM ≥ threshold, with a perceptual check of layout, legend and axis titles.

**Oracles.**
- VDS formula oracle: for each calc, query VDS with the calc as a field at a few grains and compare with TabFree SQL on the migrated data. This catches semantic translation errors before any visual work. Its limits (no fiscal calcs, RAWSQL or sets) mean those categories fall back to Query View Data comparisons.
- RLS: capture views as several representative users. Note that REST captures run as the signed-in identity (see Gaps).

### Gaps
- Not verified: exact CSV column naming and formatting conventions of Query View Data (raw vs formatted values; how table calcs and Measure Values appear), and whether `maxAge` caching can be fully bypassed.
- User impersonation on REST sign-in (for RLS ground truth) was not researched here; see the Tableau Server APIs notes.
- Image determinism (fonts, anti-aliasing across Server versions) is untested.

---

## Q12. How do connection classes appear in the XML, and how can they be remapped to Athena/Redshift?

### Takeaway
Connections nest as `datasource` → `<connection class='federated'>` → `<named-connections>` → `<connection class='postgres|sqlserver|snowflake|redshift|excel-direct|textscan|…'>`. Each `<relation connection='<class>.<id>' table='[schema].[table]'>` points at one named connection, and the extract is a separate `<connection class='hyper'>`.
- **Published data sources** appear in workbooks as `class='sqlproxy'` plus `<repository-location>`, and must be resolved to the downloaded `.tdsx`.
- **Remapping** means rewriting each relation (table, custom SQL, union, pivot) to a Glue/Athena or Redshift object through a mapping registry, while preserving the `<cols><map>` field→column bindings and transpiling custom SQL and RAWSQL from the source dialect.

### Cited Findings
- Observed classes:
  - `federated` plus a named `postgres` connection — [ts_users_02.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_users/ts_users_02.02.twb)
  - `excel-direct.<id>` with a sheet table `['Sheet$']` and `gridOrigin` — [hr.twb](https://github.com/shreyamalogi/Predictive-HR-Intelligence-Analytics/blob/main/hr.twb)
  - `textscan.<id>` as the CSV datasource id — [TDVT Results.twb](https://github.com/tableau/connector-plugin-sdk/blob/master/tdvt/TDVT%20Results.twb)
  - `hyper` for extracts — [ts_background_tasks_04.02.twb](https://github.com/tableau/community-tableau-server-insights/blob/master/datasources/ts_background_tasks/ts_background_tasks_04.02.twb)
- The Document API's `KNOWN_DB_CLASSES` lists `msaccess, msolap, bigquery, asterncluster, bigsql, composite, aurora, awshadoophive, dataengine, DataStax, db2, essbase, exasolution, excel, excel-direct, excel-reader, firebird, powerpivot, genericodbc, google-analytics, googlecloudsql, google-sheets, greenplum, saphana, hadoophive, hortonworkshadoophive, maprhadoophive, marklogic, memsql, mysql, netezza, oracle, paraccel, postgres, progressopenedge, redshift, snowflake, spark, splunk, kognitio, sqlserver, salesforce, sapbw, sybasease, sybaseiq, tbio, teradata, vectorwise, vertica, denormalized-cube, csv, textscan, webdata, webdata-direct, cubeextract`. This list is from an older library and is incomplete for 2026 connectors (for example Athena or Databricks) — [dbclass.py](https://github.com/tableau/document-api-python/blob/master/tableaudocumentapi/dbclass.py)
- Published data source reference, observed: `<repository-location id='xyTestV1' path='/datasources' revision='1.0' />` and `<connection channel='http' class='sqlproxy' dbname='xyTestV1' directory='/dataserver' port='80' server='awesomeserver' username=''>` — [multiple_connections.twb](https://github.com/tableau/document-api-python/blob/master/test/assets/multiple_connections.twb)
- Connection attributes are not schema-validated (`xs:anyAttribute … processContents="skip"`) — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)
- Packaged workbooks exclude live DB data and published data sources — [Packaged Workbooks](https://help.tableau.com/current/pro/desktop/en-us/save_savework_packagedworkbooks.htm)
- The Document API can update server, username and database name on connections — [document-api-python README](https://github.com/tableau/document-api-python)
- Relation types include `text` (custom SQL), `union`, `batch-union`, `pivot`, `stored-proc`, `subquery`, `project` and `collection` — [twb_2026.3.0.xsd](https://github.com/tableau/tableau-document-schemas/blob/main/schemas/2026_3/twb_2026.3.0.xsd)

### Inferences
**Remapping registry.** Key each relation by (connection class, server, dbname, schema, table), or by a hash of the custom-SQL text, and map it to `(engine: athena|redshift, catalog.db.table)`, filled from the lake ingestion step.
- `table`: rewrite to the Glue table, preserving column names, or emit view aliases when column names change.
- `text` (custom SQL): `sqlglot.transpile(sql, read=dialect(class), write='athena'|'redshift')`. Dialect map: sqlserver→tsql, postgres→postgres, redshift→redshift, snowflake→snowflake, bigquery→bigquery, oracle→oracle, mysql/aurora→mysql, teradata→teradata, databricks/spark→databricks/spark, hive variants→hive, athena→athena. Flag parameters embedded in custom SQL (`<[Parameters].[X]>` substitution) for runtime binding.
- `union`/`batch-union` (wildcard unions of files): one Athena table over an S3 prefix, plus a synthetic `Path`/`Sheet` column, which Tableau adds for unions.
- `pivot`: `CROSS JOIN UNNEST(ARRAY[...], ARRAY[...])` in Trino; `UNPIVOT` in Redshift (verify).
- `excel-direct`/`textscan`: ingest the packaged files from the twbx/tdsx into Parquet, respecting the `gridOrigin` range and header flags, and Data Interpreter output if used (see Gaps).
- `sqlproxy`: resolve `repository-location id` (the published datasource contentUrl) to the downloaded `.tdsx`, then inline that datasource's model into the workbook spec (relations, calcs, RLS filters), with workbook-local calcs layered on top.
- `hyper` (embedded extract): point at the migrated Parquet/Iceberg tables (Q3/Q4) and discard the live connection only if the source is retired.
- Federated cross-database joins: co-locate both sides in the lake (preferred), or use Athena federated connectors as an interim.

Record per datasource `freshness` (extract-only snapshot vs live source re-pointed) so dashboards can show data age.

### Gaps
- An exhaustive 2026 list of connection class strings (Athena, Databricks, Salesforce Data Cloud, Google Drive, etc.) was not found; build it from TabFree's own corpus.
- Data Interpreter and Excel "cleaned" ranges, and `stored-proc` relations, have no straightforward lake equivalent.
- `UNPIVOT` support in Redshift was not verified here.

---

## Q13. What are the hard or unsolved parts, and how can they be mitigated?

### Takeaway
The XML and the data are tractable: there is an official XSD, a Hyper API that is Apache-2.0 with direct S3 Parquet export, and good Tableau-authored samples. The risk sits in semantics Tableau does not document:
- VizQL join and LOD plans
- Table-calc defaults and densification
- Week, fiscal and DATEDIFF boundary rules
- Regex and date-format dialects
- Blending
- Automatic marks
- Dashboard and action behaviour
- RLS

Mitigate with conformance tests (TDVT), oracles (VDS, Query View Data/Image, the Desktop External Client API) and explicit fallbacks.

### Cited Findings
- The XSD does not validate calc contents, connection attributes or cross-references; there is no TWBX support — [tableau-document-schemas](https://github.com/tableau/tableau-document-schemas)
- Real files use undocumented `_.fcp.*` element names, and `version='18.1'` persists across releases — see the Q1 sources ([Superstore Data App.twb](https://github.com/tableau/extensions-api/blob/main/Samples/DataApp/Tableau%20Superstore%20Data%20App/Tableau%20Superstore%20Data%20App.twb), [hr.twb](https://github.com/shreyamalogi/Predictive-HR-Intelligence-Analytics/blob/main/hr.twb))
- The tableau-mcp authoring notes show Tableau's loader normalises and strips some XML. "Worksheet-level table-calculations section gets STRIPPED by loadMetadataFromXml"; "datasource-level sorts get stripped" — [tableau-mcp table-calc.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/table-calc.json), [sort.json](https://github.com/tableau/tableau-mcp/blob/main/src/desktop/data/examples/sort.json)
- The order of operations is published only as a flowchart, and the LOD join plan is not documented — [Order of operations](https://help.tableau.com/current/pro/desktop/en-us/order_of_operations.htm), [LOD constraints](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_lod_constraints.htm)
- `.tde` is unreadable by current products after 2024.2 — [Extract Upgrade](https://help.tableau.com/current/pro/desktop/en-us/extracting_upgrade.htm)
- The Hyper engine is a closed binary, though Apache-2.0 licensed — [installation.md](https://github.com/tableau/hyper-db/blob/main/website/docs/installation.md), [pantab](https://github.com/innobi/pantab)
- VDS has gaps: fiscal, RAWSQL, sets, spatial — [VDS limitations](https://help.tableau.com/current/api/vizql-data-service/en-us/docs/vds_limitations.html)
- The only translator accuracy numbers are vendor claims (75–90%) — [Pulse Convert](https://appsource.microsoft.com/en-sg/product/saas/officesolution1640276900203.pulseconvert1)

### Inferences
**Ranked hard parts and mitigations.**
1. **LOD and filter semantics.** Covers finer vs coarser joins, context vs dimension filters, nested LODs, null keys, and Redshift's lack of `IS NOT DISTINCT FROM`.
   - Mitigation: a staged-CTE compiler (Q6), a golden corpus of LOD workbooks with Server CSV ground truth, and a VDS oracle for each LOD calc.
2. **Table calcs.** Covers compute-using defaults, densification, `PREVIOUS_VALUE`, `TOTAL` re-aggregation, secondary calcs and table-calc filters.
   - Mitigation: a client-side reference implementation as source of truth plus SQL push-down when provably equivalent. Diff both against Query View Data.
3. **Date semantics.** Covers week start (datasource, function argument, locale), fiscal per field, ISO parts, DATEDIFF boundary counting, DATENAME localisation, DATEPARSE ICU formats, and timezone of NOW/TODAY.
   - Mitigation: generated templates plus the TDVT `date.*` suites (including `datediff.sow.*`) run on Athena and Redshift in CI.
4. **Type and NULL semantics.** Covers float `/` with NULL on zero, `INT` truncation, `IIF` vs `IF` NULL routing, ATTR `*`, AND/OR NULL ambiguity, string collation and case sensitivity.
   - Mitigation: the TDVT cast and logical suites, and explicit CASE templates (no reliance on engine defaults).
5. **Regex and format dialects.** ICU vs Java vs POSIX regex, ICU vs Joda vs Postgres date patterns.
   - Mitigation: a pattern linter and converter, with flagged review for unsupported constructs.
6. **RAWSQL and custom SQL.** Source-dialect SQL.
   - Mitigation: sqlglot transpile, then validate (Athena EXPLAIN or Redshift prepare), then a human or Claude review queue.
7. **RLS and user functions.**
   - Mitigation: never translate them to engine `current_user`. Compile to query-service-injected predicates with a synced group table. For `user-specific` extracts or virtual-connection policies, use live filtered views. Test with per-user ground-truth captures.
8. **Relationships and blending.** Native-LOD aggregation, outer-join retention of unmatched measures, performance options, and blending ATTR/link semantics.
   - Mitigation: per-object pre-aggregation before joins, and blending implemented as a post-join of two result sets.
9. **Extract fidelity.** `.tde` leftovers, 128-bit numerics, 32-bit floats, geography, collations, multi-table extracts.
   - Mitigation: a refresh-on-server-first policy, pinned Hyper versions in containers, schema and row-count and checksum validation after `COPY TO`, and aggregate-level checksums vs Query View Data.
10. **Visual semantics.** Automatic marks, crosstab headers and totals, dual and synchronized axes, maps and geocoding, forecasts and clusters, formatting, custom palettes and shapes, dashboard layout and device layouts, actions.
    - Mitigation: an intermediate JSON viz spec with explicit "unsupported" flags. Use Tableau PNGs as the AI target, an open boundary dataset for geocoding, and a small runtime library for actions and parameters.
11. **XML robustness.** `_.fcp` names, version drift, stripped or relocated elements, unknown elements.
    - Mitigation: normalise fcp names, version-detect by `source-build`, keep lossless raw-XML passthrough, and run XSD validation as a warning only.
12. **Long tail.** Hyper engine closed source, analytics extensions (`SCRIPT_*`, `MODEL_*`), dashboard extensions, Explain Data, Pulse.
    - Mitigation: convert-once policy; mark the rest out of scope with a manual-rebuild checklist.

### Gaps
- No public, quantitative study exists of how often these hard features (LOD, table calcs, blending, RAWSQL, RLS) occur in real Tableau estates. TabFree should run a corpus census early, for example parsing all downloaded TWBs and counting features, to size each mitigation.
