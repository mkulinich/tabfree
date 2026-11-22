"""Validate Tableau files against Tableau's official XSDs (BUILD_PLAN P2.1).

The XSDs are vendored unmodified under ``third_party/tableau-document-schemas`` and exist only
for 2026.1-2026.3. Files are matched by their ``version`` attribute (``26.2`` uses the 2026.2 XSD);
anything older uses the oldest XSD, so pre-2026 workbooks are expected to deviate. Validation runs
on the tree the parser sees, after feature-flag normalisation (:func:`tabfree_migrate.twb.normalize`).
A ``.tds`` is checked by wrapping its ``<datasource>`` in a ``<workbook>`` and keeping only errors
inside it, since the XSD declares no global ``datasource`` element (its document manifest is left
out: a workbook's embedded data sources carry none).

Every error is classified into a :data:`DEVIATIONS` code with a reason, so a corpus test can keep
an explicit list of known deviations per file and fail on any new one.

Security: documents are parsed with defusedxml before they reach xmlschema (no entities, no
external references). The XSDs are loaded with ``defuse="always"`` and ``allow="local"``, so no
resource is fetched over the network; the ``user`` namespace the XSDs import without a location is
served by a local stand-in. Requires the optional ``xmlschema`` package (a dev dependency).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element

from tabfree_migrate.twb import load_root

if TYPE_CHECKING:
    from xmlschema import XMLSchema

USER_NAMESPACE = "http://www.tableausoftware.com/xml/user"
USER_STUB = Path(__file__).parent / "data" / "tableau-user-namespace.xsd"
XSD_VERSIONS = {"26.1": "2026_1", "26.2": "2026_2", "26.3": "2026_3"}
_OLDEST = "2026_1"


@dataclass(frozen=True)
class Deviation:
    code: str
    reason: str
    pattern: re.Pattern[str]


def _d(code: str, pattern: str, reason: str) -> Deviation:
    return Deviation(code, reason, re.compile(pattern))


#: Known classes of XSD errors, matched against "<element>: <xmlschema reason>".
DEVIATIONS: tuple[Deviation, ...] = (
    _d("missing-explain-data", r"^workbook: .*Tag 'explain-data' expected",
       "The 2026 XSDs require <explain-data>, which older files (and some 2026 ones) never write."),
    _d("missing-simple-id", r"^(worksheet|dashboard): .*Tag 'simple-id' expected",
       "<simple-id> (SheetIdentifierTracking) is missing from files written before that feature."),
    _d("legacy-folder", r"^datasource: Unexpected child with tag 'folder'",
       "Pre-2020 field folders are <folder> children of the data source; 2026 expects <folders-common>."),
    _d("legacy-schema-layout", r"^layout: '(dim-percentage|measure-percentage|user-set-layout)' attribute",
       "Data pane layout attributes from before the 2020.2 schema viewer."),
    _d("legacy-sort", r"^(view|default-sorts): Unexpected child with tag 'sort'",
       "Pre-2019 generic <sort class=...> elements; 2026 has computed-/manual-/alphabetic-/natural-sort."),
    _d("legacy-window", r"^window: ",
       "Window (tab and card) state the 2026 XSD does not describe: highlight, selection-collection, "
       "class='hidden-worksheet', zones..., written by older releases."),
    _d("legacy-period-type", r"^filter: 'period-type' attribute",
       "Relative-date filters with period-type; 2026 writes period-type-v2."),
    _d("tab-color", r"^(worksheet|dashboard): 'tab-color' attribute",
       "Sheet tab colours, written by Tableau but missing from the XSD."),
    _d("missing-source-build", r"^workbook: missing required attribute 'source-build'",
       "Hand-written or very old files without source-build."),
    _d("extract-without-object-id", r"^extract: missing required attribute 'object-id'",
       "Extracts written before the 2020.2 object model have no object-id."),
    _d("workbook-fingerprint", r"^workbook: '(creator-)?(author-id|license-type|machine-id|site-id)'",
       "WorkbookFingerprinting attributes, written by Tableau but missing from the XSD."),
    _d("trendline-exclude-panes", r"^trendline: 'exclude-panes' attribute",
       "Trend line pane exclusions, missing from the XSD."),
    _d("placeholder-extension-id", r"^worksheet-extension: attribute id=",
       "Template workbook with a placeholder extension id (TODO-MANIFEST-ID)."),
    _d("empty-workbook", r"^(worksheets|windows): .*Tag '(worksheet|window)' expected",
       "Starter workbook with no sheets; the XSD needs at least one."),
    _d("partial-repository-location", r"^repository-location: missing required attribute",
       "Test fixture whose <repository-location> lacks id, path and revision."),
    _d("data-source-tree", r"^datasource: Unexpected child with tag 'data-source-tree'",
       "Published data source tree (ExtendedPublishedDataSourceUI), missing from the XSD."),
)  # fmt: skip


@dataclass(frozen=True)
class XsdIssue:
    path: str
    reason: str
    code: str


def xsd_path(schema_dir: Path, version: str | None) -> Path:
    """The vendored XSD for a document ``version`` (older versions use the oldest XSD)."""
    folder = XSD_VERSIONS.get(version or "", _OLDEST)
    return schema_dir / folder / f"twb_{folder.replace('_', '.')}.0.xsd"


@cache
def load_xsd(path: Path) -> XMLSchema:
    import xmlschema  # optional dependency, imported only when validating

    return xmlschema.XMLSchema(
        str(path.resolve()),
        allow="local",
        defuse="always",
        locations=[(USER_NAMESPACE, str(USER_STUB.resolve()))],
    )


def classify(element: str, reason: str) -> str:
    signature = f"{element}: {reason}"
    for deviation in DEVIATIONS:
        if deviation.pattern.search(signature):
            return deviation.code
    return "unclassified"


def _wrap(datasource: Element) -> Element:
    """``<workbook><datasources>`` around a .tds root, minus the manifest a workbook puts elsewhere."""
    workbook = Element("workbook")
    for key in ("version", "source-build", "source-platform", "original-version"):
        if (value := datasource.get(key)) is not None:
            workbook.set(key, value)
    inner = Element(datasource.tag, datasource.attrib)
    inner.extend(c for c in datasource if c.tag != "document-format-change-manifest")
    holder = Element("datasources")
    holder.append(inner)
    workbook.append(holder)
    return workbook


def validate_root(root: Element, schema_dir: Path) -> list[XsdIssue]:
    """XSD errors for a normalised ``<workbook>`` or ``<datasource>`` root."""
    schema = load_xsd(xsd_path(schema_dir, root.get("version")))
    is_datasource = root.tag == "datasource"
    document = _wrap(root) if is_datasource else root
    issues = []
    for error in schema.iter_errors(document):
        path = error.path or ""
        if is_datasource and not path.startswith("/workbook/datasources/"):
            continue
        element = re.sub(r"\[\d+\]$", "", path.rsplit("/", 1)[-1])
        reason = re.sub(r"\s+at position \d+", "", error.reason or str(error.message))
        issues.append(XsdIssue(path, reason, classify(element, reason)))
    return issues


def validate_file(path: Path, schema_dir: Path) -> list[XsdIssue]:
    """XSD errors for a .twb/.twbx/.tds/.tdsx file (parsed safely, then normalised)."""
    root, _data, _packaged = load_root(path)
    return validate_root(root, schema_dir)
