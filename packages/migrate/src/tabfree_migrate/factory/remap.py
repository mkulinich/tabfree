"""Connection remap rules (BUILD_PLAN P5.1): each Tableau connection → its Athena or Redshift table.

A rules file (``tabfree/remap@0``, YAML or JSON) says where each Tableau source now lives::

    schema: tabfree/remap@0
    defaults: { engine: athena, database: tabfree_lake }
    rules:
      # First match wins. Match keys are glob patterns, compared case-insensitively.
      - match: { class: postgres, server: "pg.prod.*", dbname: sales }
        target: { engine: athena, database: sales_lake, table: "{schema}_{table}" }
      - match: { class: redshift }
        target: { engine: redshift, database: "{dbname}", schema: "{schema}", table: "{table}" }
      - match: { class: hyper }          # file extracts: the tables convert-batch wrote
        target: { extract: true, database: tabfree_extracts }
    decisions:                            # answers from the decision queue, keyed by source key
      "sqlserver://erp.corp:1433/erp": { choice: federated, catalog: erp_federated, database: dbo }

Templates may use ``{class}``, ``{server}``, ``{dbname}``, ``{schema}``, ``{table}`` and ``{caption}``.

A source no rule covers goes to the **decision queue** with the options that apply to it:

- ``federated``: Athena federated query through a connector (the data stays where it is);
- ``replicate``: copy it into AWS (DMS, Glue or zero-ETL), then map it with a rule;
- ``snapshot``: keep a static copy (files such as Excel and CSV, or sources due for retirement);
- ``extract``: use the converted extract, when the data source has one.

Connection details are matched but never copied into models beyond the table names: credentials,
usernames and OAuth settings are dropped (CLAUDE.md rule 4).
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from tabfree_migrate.hyper import glue_identifier

SCHEMA = "tabfree/remap@0"
#: Connection classes that already are AWS engines TabFree queries natively.
NATIVE = {"athena": "athena", "awsathena": "athena", "redshift": "redshift"}
#: File-based sources: there is no server to federate to.
FILES = {"excel-direct", "excel", "textscan", "csv", "json", "msaccess", "pdf", "spatial"}
#: Extracts and Tableau-internal engines.
EXTRACTS = {"hyper", "dataengine"}
CHOICES = ("federated", "replicate", "snapshot", "extract")
_MATCH_KEYS = ("class", "server", "dbname", "schema", "table", "caption", "datasource")


class RemapError(ValueError):
    pass


def unbracket(text: str) -> str:
    return text[1:-1] if text.startswith("[") and text.endswith("]") else text


def _split_table(table: str) -> tuple[str, str]:
    """``[schema].[table]`` → (schema, table); a bare ``[table]`` has no schema."""
    parts = [unbracket(p) for p in re.findall(r"\[[^\]]*\]|[^.\[\]]+", table)]
    if not parts:
        return "", ""
    return (parts[-2] if len(parts) >= 2 else ""), parts[-1]


@dataclass(frozen=True)
class Leaf:
    """One physical table of a data source, with the connection it is read through."""

    datasource: str
    caption: str
    name: str
    cls: str
    server: str
    port: str
    dbname: str
    schema: str
    table: str
    custom_sql: bool = False

    @property
    def source_key(self) -> str:
        """Identifies the database (never a credential): ``class://server:port/dbname``."""
        host = self.server + (f":{self.port}" if self.port else "")
        return f"{self.cls}://{host}/{self.dbname}".lower()

    def values(self) -> dict[str, str]:
        return {
            "class": self.cls,
            "server": self.server,
            "dbname": self.dbname,
            "schema": self.schema,
            "table": self.table,
            "caption": self.caption,
            "datasource": self.datasource,
        }


def _leaves(relation: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not relation:
        return []
    if relation.get("type") in ("table", "text"):
        return [relation]
    out: list[Mapping[str, Any]] = []
    for child in relation.get("children", []):
        out += _leaves(child)
    return out


def leaves(ds: Mapping[str, Any]) -> list[Leaf]:
    """The physical tables of a data source's IR, each with its own connection."""
    named = {n.get("name"): n for n in ds.get("namedConnections", []) or []}
    relations = _leaves(ds.get("relation"))
    for obj in (ds.get("objectGraph") or {}).get("objects", []):
        relations += _leaves(obj.get("relation"))
    out: list[Leaf] = []
    seen: set[str] = set()
    for rel in relations:
        name = str(rel.get("name") or unbracket(str(rel.get("table", "table"))))
        if name in seen:
            continue
        seen.add(name)
        conn = named.get(rel.get("connection")) or ds.get("connection") or {}
        schema, table = _split_table(str(rel.get("table", name)))
        out.append(
            Leaf(
                datasource=str(ds.get("name", "")),
                caption=str(ds.get("caption") or ds.get("name") or ""),
                name=name,
                cls=str(conn.get("class", "")).lower(),
                server=str(conn.get("server", "")),
                port=str(conn.get("port", "")),
                dbname=str(conn.get("dbname", "")),
                schema=schema or str(conn.get("schema", "")),
                table=table or name,
                custom_sql=rel.get("type") == "text",
            )
        )
    return out


@dataclass(frozen=True)
class Rule:
    match: Mapping[str, str]
    target: Mapping[str, Any] = field(default_factory=dict)
    decision: str | None = None

    def matches(self, leaf: Leaf) -> bool:
        values = leaf.values()
        return all(
            fnmatch.fnmatchcase(values.get(key, "").lower(), str(pattern).lower())
            for key, pattern in self.match.items()
        )


@dataclass
class Rules:
    rules: list[Rule] = field(default_factory=list)
    defaults: Mapping[str, Any] = field(
        default_factory=lambda: {"engine": "athena", "database": "tabfree_lake"}
    )
    decisions: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_document(cls, doc: Mapping[str, Any] | None) -> Rules:
        doc = doc or {}
        if doc and doc.get("schema", SCHEMA) != SCHEMA:
            raise RemapError(f"not a {SCHEMA} document: {doc.get('schema')!r}")
        rules = []
        for i, entry in enumerate(doc.get("rules", []) or []):
            match = dict(entry.get("match") or {})
            unknown = set(match) - set(_MATCH_KEYS)
            if unknown:
                raise RemapError(f"rule {i}: unknown match keys {sorted(unknown)}")
            decision = entry.get("decision")
            if decision is not None and decision not in CHOICES:
                raise RemapError(f"rule {i}: decision must be one of {CHOICES}")
            if not entry.get("target") and decision is None:
                raise RemapError(f"rule {i}: needs a target or a decision")
            rules.append(Rule(match, dict(entry.get("target") or {}), decision))
        decisions: dict[str, Mapping[str, Any]] = {
            str(k).lower(): dict(v) for k, v in (doc.get("decisions") or {}).items()
        }
        for key, answer in decisions.items():
            if answer.get("choice") not in CHOICES:
                raise RemapError(f"decision {key!r}: choice must be one of {CHOICES}")
        defaults = dict(doc.get("defaults") or {"engine": "athena", "database": "tabfree_lake"})
        return cls(rules, defaults, decisions)


@dataclass(frozen=True)
class ExtractTable:
    database: str
    table: str


@dataclass
class ExtractCatalogue:
    """The tables convert-batch wrote, found by package file, extract member and Hyper table name."""

    by_member: dict[tuple[str, str], ExtractTable] = field(default_factory=dict)
    database: str = "tabfree_extracts"

    @classmethod
    def from_reports(
        cls, reports: Sequence[Mapping[str, Any]], database: str = "tabfree_extracts"
    ) -> ExtractCatalogue:
        out = cls(database=database)
        for report in reports:
            if report.get("status") != "converted":
                continue
            member = PurePosixPath(str(report.get("member") or report.get("name") or "")).name.lower()
            for table in report.get("tables", []):
                key = (member, str(table.get("table", "")).lower())
                out.by_member[key] = ExtractTable(database, str(table["glue_table"]))
                # A single-table extract also answers for "Extract" / any table name.
                out.by_member.setdefault((member, "*"), ExtractTable(database, str(table["glue_table"])))
        return out

    def find(self, dbname: str, table: str) -> ExtractTable | None:
        member = PurePosixPath(dbname.replace("\\", "/")).name.lower()
        return self.by_member.get((member, table.lower())) or self.by_member.get((member, "*"))


@dataclass
class Resolution:
    """Where one data source's tables now live, or why a person has to decide."""

    datasource: str
    caption: str
    status: str  # resolved | decision | unsupported
    connection: dict[str, Any] = field(default_factory=dict)
    tables: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: For status "decision": the source key and the choices that apply.
    source_key: str = ""
    source_class: str = ""
    options: list[str] = field(default_factory=list)
    rule: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "datasource": self.datasource,
            "caption": self.caption,
            "status": self.status,
        }
        if self.connection:
            out["connection"] = self.connection
        if self.tables:
            out["tables"] = self.tables
        if self.notes:
            out["notes"] = self.notes
        if self.status == "decision":
            out.update(sourceKey=self.source_key, sourceClass=self.source_class, options=self.options)
        if self.rule is not None:
            out["rule"] = self.rule
        return out


def _fill(template: Any, values: Mapping[str, str]) -> str:
    text = str(template)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    if "{" in text:
        raise RemapError(f"unknown placeholder in {template!r}")
    return text


def _ident(text: str) -> str:
    return glue_identifier(text) if text else text


def _options(leaf: Leaf, has_extract: bool) -> list[str]:
    if leaf.cls in FILES:
        opts = ["snapshot", "replicate"]
    elif leaf.cls in EXTRACTS:
        opts = ["extract"]
    elif leaf.cls in ("sqlproxy", ""):
        opts = []
    else:
        opts = ["federated", "replicate", "snapshot"]
    if has_extract and "extract" not in opts:
        opts.insert(0, "extract")
    return opts


def resolve(
    ds: Mapping[str, Any],
    rules: Rules,
    *,
    extracts: ExtractCatalogue | None = None,
) -> Resolution:
    """Apply the rules to every table of one data source (embedded or published)."""
    caption = str(ds.get("caption") or ds.get("name") or "")
    res = Resolution(str(ds.get("name", "")), caption, "resolved")
    cls = str((ds.get("connection") or {}).get("class", "")).lower()
    if cls == "sqlproxy":
        # An embedded reference to a published data source: its model comes from that data source.
        res.status = "published"
        res.notes.append("uses a published data source; its model is generated from the data source itself")
        return res
    found = leaves(ds)
    if not found:
        res.status = "unsupported"
        res.notes.append("no tables found in the data source")
        return res
    has_extract = bool(ds.get("extract"))
    engines: set[tuple[str, str, str]] = set()
    for leaf in found:
        values = leaf.values()
        rule_index, target, decision = None, None, None
        answer = rules.decisions.get(leaf.source_key)
        for i, rule in enumerate(rules.rules):
            if rule.matches(leaf):
                rule_index, target, decision = i, rule.target, rule.decision
                break
        if answer is not None:
            decision = str(answer["choice"])
            target = answer
        native = NATIVE.get(leaf.cls)
        if target is None and decision is None and native:
            target = {"engine": native, "database": "{dbname}" if native == "redshift" else "{schema}"}
            res.notes.append(f"{leaf.name}: already on {native}; mapped as-is")
        if target is None and decision is None:
            res.status = "decision"
            res.source_key = res.source_key or leaf.source_key
            res.source_class = res.source_class or leaf.cls
            res.options = _options(leaf, has_extract)
            res.notes.append(f"{leaf.name}: no rule for {leaf.source_key}")
            continue
        res.rule = rule_index if res.rule is None else res.rule
        merged: dict[str, Any] = {**rules.defaults, **(target or {})}
        if decision == "extract" or merged.get("extract"):
            catalogue = extracts or ExtractCatalogue()
            hit = catalogue.find(leaf.dbname, leaf.table) if leaf.cls in EXTRACTS else None
            if hit is None and has_extract:
                hit = catalogue.find(f"{caption}.hyper", leaf.table)
            database = str(merged.get("database") or catalogue.database)
            if hit is None:
                table = _ident(f"{PurePosixPath(leaf.dbname or caption).stem}_{leaf.table}").lower()
                res.notes.append(
                    f"{leaf.name}: converted extract not found; expected as {database}.{table} "
                    "(run convert-batch)"
                )
            else:
                database, table = hit.database, hit.table
            engine = "athena"
            res.tables[leaf.name] = {"table": table, "database": database}
            engines.add((engine, database, ""))
            continue
        if decision in ("snapshot", "replicate") and not (target or {}).get("table"):
            # Answered in the queue but not yet placed: default naming in the default database.
            merged.setdefault("table", "{dbname}_{table}" if leaf.dbname else "{table}")
            res.notes.append(f"{leaf.name}: {decision}; expected under the default naming")
        engine = str(merged.get("engine", "athena"))
        if engine not in ("athena", "redshift"):
            raise RemapError(f"{leaf.name}: engine must be athena or redshift, not {engine!r}")
        table = _ident(_fill(merged.get("table", "{table}"), values)).lower()
        database = _ident(_fill(merged.get("database", "tabfree_lake"), values)).lower()
        entry = {"table": table, "database": database}
        if merged.get("schema"):
            entry["schema"] = _ident(_fill(merged["schema"], values)).lower()
        res.tables[leaf.name] = entry
        catalog = str(merged.get("catalog", "")) if decision == "federated" or merged.get("catalog") else ""
        engines.add((engine, database, catalog))
        if leaf.custom_sql:
            res.notes.append(f"{leaf.name}: custom SQL is translated, not remapped; check its table names")
    if res.status == "decision":
        res.tables = {}
        return res
    first = sorted(engines)[0]
    if len({e[0] for e in engines}) > 1:
        res.status = "unsupported"
        res.notes.append("tables resolve to both Athena and Redshift; a model has one engine")
        return res
    res.connection = {"engine": first[0], "database": first[1]}
    if first[2]:
        res.connection["catalog"] = first[2]
    if first[0] == "redshift":
        schema = next((t.get("schema") for t in res.tables.values() if t.get("schema")), None)
        if schema:
            res.connection["schema"] = schema
    for entry in res.tables.values():
        # The model's connection carries the common database; tables keep theirs only when different.
        if entry.get("database") == first[1]:
            entry.pop("database")
    return res
