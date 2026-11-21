"""Semantic-model generator (BUILD_PLAN P2.4): a Tableau data source's IR → ``tabfree/model@0``.

What is generated:

- **tables** from the relation tree, the object graph and ``<metadata-records>`` (custom SQL is
  translated to the target engine with SQLGlot; the original is kept as ``originalSql``),
- **relationships** from join clauses and object-graph relationships. Their cardinality is assumed to be
  many-to-one, from the first table, and every assumption is listed in the report,
- **fields** for physical columns (types, default aggregation, formats, folders, aliases, geographic
  roles) and for calculated fields, whose formulas are rewritten from Tableau's internal names
  (``[Calculation_123]``, ``[Parameters].[Parameter 1]``) to captions,
- **parameters**, **groups**, **bins**, **sets** and **hierarchies**,
- **RLS slots**: data source filters that use user functions become policies; other calculations
  that use them are reported for review.

The report says what could not be translated, so a person or Claude can finish the model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_BRACKETS = re.compile(r"\[((?:[^\]]|\]\])+)\]")
_REF = re.compile(r"\[(?:[^\]]|\]\])+\](?:\.\[(?:[^\]]|\]\])+\])?")
_USER_FUNCTIONS = re.compile(
    r"\b(USERNAME|FULLNAME|USERDOMAIN|ISMEMBEROF|ISUSERNAME|ISFULLNAME|USERATTRIBUTE\w*)\s*\(", re.I
)

#: Tableau connection classes → SQLGlot dialects for custom SQL.
SOURCE_DIALECTS = {
    "postgres": "postgres",
    "sqlserver": "tsql",
    "mysql": "mysql",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "oracle": "oracle",
    "teradata": "teradata",
    "databricks": "databricks",
    "spark": "spark",
    "athena": "athena",
    "awsathena": "athena",
    "presto": "presto",
    "trino": "trino",
    "hive": "hive",
    "hyper": "postgres",
}
_TYPES = {
    "string": "string",
    "integer": "integer",
    "real": "float",
    "float": "float",
    "date": "date",
    "datetime": "datetime",
    "boolean": "boolean",
    "spatial": "geography",
}
_AGGREGATIONS = {
    "sum": "sum",
    "avg": "avg",
    "count": "count",
    "countd": "countd",
    "min": "min",
    "max": "max",
    "median": "median",
}
_SEMANTIC_ROLES = {
    "country": "country",
    "state": "state_province",
    "county": "county",
    "city": "city",
    "zipcode": "postal_code",
    "latitude": "latitude",
    "longitude": "longitude",
    "airport": "airport",
    "areacode": "area_code",
    "cbsamsa": "cbsa_msa",
    "congressionaldistrict": "congressional_district",
    "nuts": "nuts_europe",
}


@dataclass
class GenerationReport:
    assumptions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    invalid_calcs: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.schema_errors or self.invalid_calcs)


def snake(text: str, prefix: str = "f") -> str:
    """A model id: lower snake case, starting with a letter."""
    value = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or prefix
    if not value[0].isalpha():
        value = f"{prefix}_{value}"
    return value[:100]


def _identifier(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "col"
    return value if value[0].isalpha() or value[0] == "_" else f"c_{value}"


def unbracket(name: str) -> str:
    match = _BRACKETS.fullmatch(name.strip())
    return match.group(1).replace("]]", "]") if match else name.strip()


def tableau_literal(text: Any) -> Any:
    """Decode a Tableau literal ("\\"East\\"", "#2024-01-01#", "10", "true") to a JSON scalar."""
    if not isinstance(text, str):
        return text
    raw = text.strip()
    if raw in ("%null%", ""):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1].replace(raw[0] * 2, raw[0])
    if len(raw) >= 2 and raw[0] == raw[-1] == "#":
        return raw[1:-1].strip()
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


class _Ids:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, text: str, prefix: str = "f") -> str:
        base = snake(text, prefix)
        candidate, n = base, 2
        while candidate in self.used:
            candidate = f"{base[:95]}_{n}"
            n += 1
        self.used.add(candidate)
        return candidate


def _leaves(relation: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not relation:
        return []
    if relation.get("type") in ("table", "text"):
        return [relation]
    out: list[Mapping[str, Any]] = []
    for child in relation.get("children", []):
        out += _leaves(child)
    return out


def _joins(relation: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not relation:
        return []
    out = [relation] if relation.get("type") == "join" else []
    for child in relation.get("children", []):
        out += _joins(child)
    return out


def _connection_class(ds: Mapping[str, Any], leaf: Mapping[str, Any]) -> str:
    named = {n.get("name"): n for n in ds.get("namedConnections", [])}
    conn = named.get(leaf.get("connection")) or ds.get("connection") or {}
    return str(conn.get("class", ""))


def _translate_sql(sql: str, source_class: str, engine: str, report: GenerationReport, where: str) -> str:
    import sqlglot

    read = SOURCE_DIALECTS.get(source_class)
    write = "trino" if engine == "athena" else engine
    if read is None:
        report.review.append(
            f"{where}: custom SQL from {source_class or 'an unknown source'} kept as written"
        )
        return sql
    try:
        return ";\n".join(sqlglot.transpile(sql, read=read, write=write))
    except sqlglot.errors.ParseError as exc:
        report.review.append(f"{where}: custom SQL could not be translated ({str(exc).splitlines()[0]})")
        return sql


def generate_model(
    ir: Mapping[str, Any],
    *,
    datasource: str | None = None,
    model_id: str | None = None,
    engine: str = "athena",
    database: str = "tabfree_lake",
    table_map: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], GenerationReport]:
    """Generate a semantic model for one data source of a workbook or data source IR."""
    report = GenerationReport()
    sources = [d for d in ir.get("datasources", []) if d.get("kind") != "parameters"]
    if datasource is not None:
        sources = [d for d in sources if datasource in (d.get("name"), d.get("caption"))]
    if not sources:
        raise ValueError(f"no data source {datasource!r} in the IR")
    ds = sources[0]
    title = str(ds.get("caption") or ds.get("name") or "model")
    ids = _Ids()
    table_ids = _Ids()

    # -- tables ----------------------------------------------------------------------------------
    tables: dict[str, dict[str, Any]] = {}  # relation name -> table doc
    relation_leaves = _leaves(ds.get("relation"))
    for obj in (ds.get("objectGraph") or {}).get("objects", []):
        relation_leaves += _leaves(obj.get("relation"))
    for leaf in relation_leaves:
        name = str(leaf.get("name") or unbracket(str(leaf.get("table", "table"))))
        if name in tables:
            continue
        tid = table_ids.take(name, "t")
        source: dict[str, Any]
        if leaf.get("type") == "text":
            original = str(leaf.get("sql") or leaf.get("text") or "")
            source = {
                "sql": _translate_sql(
                    original, _connection_class(ds, leaf), engine, report, f"table {name!r}"
                )
            }
            source["originalSql"] = original
        else:
            physical = unbracket(str(leaf.get("table", name)).split("].[")[-1])
            mapped = (table_map or {}).get(name) or (table_map or {}).get(physical)
            source = {"table": mapped or _identifier(physical).lower()}
            if not mapped:
                report.assumptions.append(
                    f"table {name!r} is expected as {database}.{source['table']} (pass table_map to change)"
                )
        tables[name] = {"id": tid, "source": source, "columns": []}
    if not tables:
        tid = table_ids.take(title, "t")
        tables[title] = {"id": tid, "source": {"table": _identifier(title).lower()}, "columns": []}
        report.assumptions.append(
            f"no relation found; using a single table {tables[title]['source']['table']!r}"
        )
    default_table = next(iter(tables.values()))

    # -- physical columns ---------------------------------------------------------------------------
    info = {c["name"]: c for c in ds.get("columns", [])}
    placed: dict[str, tuple[str, str, str]] = {}  # local name -> (table id, column, type)
    for record in ds.get("metadataRecords", []):
        local = record["localName"]
        parent = unbracket(record.get("parentName", "")) if record.get("parentName") else None
        table = tables.get(parent) if parent else default_table
        if table is None:
            table = default_table
            report.assumptions.append(
                f"column {local} has unknown parent {parent!r}; placed in {table['id']}"
            )
        column = _identifier(str(record.get("remoteName") or unbracket(local)))
        type_ = _TYPES.get(str(record.get("localType", "string")).lower(), "string")
        if column not in {c["name"] for c in table["columns"]}:
            table["columns"].append({"name": column, "type": type_})
        placed[local] = (table["id"], column, type_)
    if not placed:
        for col in ds.get("columns", []):
            if "calculation" in col or col.get("param"):
                continue
            column = _identifier(unbracket(col["name"]))
            type_ = _TYPES.get(str(col.get("datatype", "string")), "string")
            default_table["columns"].append({"name": column, "type": type_})
            placed[col["name"]] = (default_table["id"], column, type_)
        if placed:
            report.assumptions.append(
                "no metadata records: columns taken from <column> elements, all in one table"
            )

    # -- names used in formulas ---------------------------------------------------------------------
    local_to_name: dict[str, str] = {}
    for local in placed:
        col = info.get(local, {})
        local_to_name[local] = str(col.get("caption") or unbracket(local))
    for col in ds.get("columns", []):
        if "calculation" in col and col["name"] not in local_to_name:
            local_to_name[col["name"]] = str(col.get("caption") or unbracket(col["name"]))
    params_ds: Mapping[str, Any] = next(
        (d for d in ir.get("datasources", []) if d.get("kind") == "parameters"), {"columns": []}
    )
    param_names = {
        c["name"]: str(c.get("caption") or unbracket(c["name"])) for c in params_ds.get("columns", [])
    }
    for s in ds.get("sets", []):
        if s.get("caption"):
            local_to_name.setdefault(s["name"], str(s["caption"]))

    def rewrite(formula: str) -> str:
        def replace(match: re.Match[str]) -> str:
            ref = match.group(0)
            if ref.lower().startswith("[parameters]."):
                inner = ref[len("[Parameters].") :]
                return "[Parameters].[" + param_names.get(inner, unbracket(inner)).replace("]", "]]") + "]"
            if "].[" in ref:  # [datasource].[field] from another source (a blend): keep the field
                ref = "[" + ref.split("].[", 1)[1]
            name = local_to_name.get(ref)
            return "[" + name.replace("]", "]]") + "]" if name else ref

        # Leave string literals and comments alone.
        out, i = [], 0
        for literal in re.finditer(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|//[^\n]*', formula):
            out.append(_REF.sub(replace, formula[i : literal.start()]))
            out.append(literal.group(0))
            i = literal.end()
        out.append(_REF.sub(replace, formula[i:]))
        return "".join(out)

    # -- fields ---------------------------------------------------------------------------------------
    fields: list[dict[str, Any]] = []
    names_taken: set[str] = set()
    for local, (tid, column, type_) in placed.items():
        col = info.get(local, {})
        name = local_to_name[local]
        names_taken.add(name.casefold())
        role = col.get("role") or ("measure" if type_ in ("integer", "float") else "dimension")
        entry: dict[str, Any] = {
            "id": ids.take(name),
            "name": name,
            "role": role,
            "type": type_,
            "table": tid,
            "column": column,
        }
        aggregation = _AGGREGATIONS.get(str(col.get("aggregation", "")).lower())
        if role == "measure":
            entry["defaultAggregation"] = aggregation or "sum"
        _decorate(entry, col)
        fields.append(entry)
    groups: list[dict[str, Any]] = []
    bins: list[dict[str, Any]] = []
    for col in ds.get("columns", []):
        calc = col.get("calculation")
        if not calc:
            continue
        name = local_to_name.get(col["name"], unbracket(col["name"]))
        if col.get("group"):
            g = col["group"]
            mapping: dict[str, list[Any]] = {}
            for grp in g.get("groups", []):
                members = [tableau_literal(m) for m in grp.get("members", [])]
                mapping[str(tableau_literal(grp.get("name")))] = members or [
                    str(tableau_literal(grp.get("name")))
                ]
            if mapping:
                entry = {
                    "id": ids.take(name),
                    "name": name,
                    "field": local_to_name.get(g.get("source", ""), unbracket(g.get("source", ""))),
                    "groups": mapping,
                }
                if g.get("other"):
                    entry["otherLabel"] = str(tableau_literal(g["other"]))
                groups.append(entry)
            continue
        if col.get("bin"):
            b = col["bin"]
            size = tableau_literal(b.get("size"))
            bin_source = local_to_name.get(b.get("source", ""), unbracket(b.get("source", "")))
            if isinstance(size, int | float) and size > 0:
                bins.append({"id": ids.take(name), "name": name, "field": bin_source, "size": size})
            else:
                report.unsupported.append(
                    f"bin {name!r}: size {b.get('size')!r} (parameter-sized bins need review)"
                )
            continue
        formula = calc.get("formula")
        if calc.get("class") != "tableau" or not formula:
            report.unsupported.append(f"calculated field {name!r}: class {calc.get('class')!r}")
            continue
        type_ = _TYPES.get(str(col.get("datatype", "string")), "string")
        entry = {
            "id": ids.take(name),
            "name": name,
            "role": col.get("role") or ("measure" if type_ in ("integer", "float") else "dimension"),
            "type": type_,
            "formula": rewrite(str(formula)),
        }
        if entry["role"] == "measure" and not _is_aggregate_formula(str(formula)):
            entry["defaultAggregation"] = _AGGREGATIONS.get(str(col.get("aggregation", "")).lower(), "sum")
        _decorate(entry, col)
        if _USER_FUNCTIONS.search(str(formula)):
            report.review.append(f"calculated field {name!r} uses user functions: consider an RLS policy")
        fields.append(entry)

    # -- parameters -----------------------------------------------------------------------------------
    parameters = []
    for col in params_ds.get("columns", []):
        p = col.get("param") or {}
        name = param_names[col["name"]]
        type_ = _TYPES.get(str(col.get("datatype", "string")), "string")
        if type_ == "geography":
            type_ = "string"
        entry = {
            "id": ids.take(name, "p"),
            "name": name,
            "type": type_,
            "default": tableau_literal(p.get("value") or (col.get("calculation") or {}).get("formula")),
        }
        if p.get("domain") == "list" and p.get("members"):
            entry["allowed"] = {
                "list": [
                    tableau_literal(m.get("value") if isinstance(m, Mapping) else m) for m in p["members"]
                ]
            }
        elif p.get("domain") == "range" and isinstance(p.get("range"), Mapping):
            rng = {
                k: tableau_literal(v) for k, v in p["range"].items() if k in ("min", "max") and v is not None
            }
            if isinstance(tableau_literal(p["range"].get("granularity")), int | float):
                rng["step"] = tableau_literal(p["range"]["granularity"])
            entry["allowed"] = {"range": rng}
        parameters.append(entry)

    # -- sets and hierarchies ---------------------------------------------------------------------------
    sets: list[dict[str, Any]] = []
    for s in ds.get("sets", []):
        if s.get("kind") != "set":
            continue
        name = str(s.get("caption") or unbracket(str(s.get("name", ""))))
        level = (s.get("tree") or {}).get("level") or (s.get("topN") or {}).get("level")
        base = local_to_name.get(str(level), unbracket(str(level))) if level else None
        if s.get("members") and base:
            sets.append(
                {
                    "id": ids.take(name),
                    "name": name,
                    "field": base,
                    "members": [tableau_literal(m) for m in s["members"]],
                }
            )
        elif s.get("topN") and base:
            top = s["topN"]
            count = top.get("countParameter")
            n: Any = tableau_literal(top.get("count")) or 10
            if count:
                # "[Parameters].[Parameter 1]" names a parameter by its internal name.
                inner = str(count).split("].[", 1)[-1]
                pname = param_names.get(inner if inner.startswith("[") else f"[{inner}")
                pid = next((p["id"] for p in parameters if p["name"] == pname), None)
                if pid:
                    n = {"param": pid}
                else:
                    report.review.append(f"set {name!r}: Top N parameter {count} not found; using {n}")
            sets.append(
                {
                    "id": ids.take(name),
                    "name": name,
                    "field": base,
                    "top": {
                        "n": n,
                        "by": rewrite(str(top.get("by", "COUNT([Number of Records])"))),
                        "direction": "bottom" if str(top.get("end")).lower() == "bottom" else "top",
                    },
                }
            )
        elif s.get("all") and base:
            # An interactive set that starts with every member: in for every member present.
            sets.append({"id": ids.take(name), "name": name, "field": base, "condition": "COUNT(1) > 0"})
            report.review.append(
                f"set {name!r} is interactive (starts with all members): modelled as all members"
            )
        else:
            report.unsupported.append(f"set {name!r}: unsupported definition")
    hierarchies = []
    for path in ds.get("drillPaths", []):
        levels = [local_to_name.get(f, unbracket(f)) for f in path.get("fields", [])]
        if len(levels) >= 2 and len(set(levels)) == len(levels):
            hierarchies.append({"name": str(path["name"]), "levels": levels})

    # -- relationships ----------------------------------------------------------------------------------
    relationships = []
    by_name = {name: t["id"] for name, t in tables.items()}

    def table_of(ref: str) -> tuple[str | None, str]:
        parts = [unbracket("[" + p.strip("[]") + "]") for p in ref.split("].[")]
        if len(parts) == 2:
            return by_name.get(parts[0]), _identifier(parts[1])
        local = placed.get("[" + parts[-1] + "]")
        return (local[0], local[1]) if local else (None, _identifier(parts[-1]))

    for join in _joins(ds.get("relation")):
        for clause in join.get("clauses", []):
            if clause.get("op", "=") != "=":
                report.unsupported.append(f"join clause {clause}: only equality joins are supported")
                continue
            lt, lc = table_of(str(clause.get("left", "")))
            rt, rc = table_of(str(clause.get("right", "")))
            if lt and rt and lt != rt:
                relationships.append(
                    {"left": lt, "right": rt, "on": [{"left": lc, "right": rc}], "cardinality": "many_to_one"}
                )
                kind = join.get("join", "inner")
                report.assumptions.append(f"{kind} join {lt} → {rt} modelled as a many-to-one relationship")
    for rel in (ds.get("objectGraph") or {}).get("relationships", []):
        for clause in rel.get("clauses", []):
            lt, lc = table_of(str(clause.get("left", "")))
            rt, rc = table_of(str(clause.get("right", "")))
            if lt and rt and lt != rt:
                relationships.append(
                    {"left": lt, "right": rt, "on": [{"left": lc, "right": rc}], "cardinality": "many_to_one"}
                )
                report.assumptions.append(
                    f"relationship {lt} → {rt} assumed many-to-one (Tableau infers cardinality)"
                )

    # -- RLS slots from data source filters -------------------------------------------------------------
    rls = []
    for i, flt in enumerate(ds.get("filters", [])):
        formula = flt.get("formula") or flt.get("condition") or ""
        target = local_to_name.get(str(flt.get("field", "")))
        if formula and _USER_FUNCTIONS.search(formula):
            rls.append(
                {
                    "id": f"rls_{i + 1}",
                    "description": "Migrated from a Tableau data source filter.",
                    "filter": rewrite(formula),
                }
            )
        elif target and _USER_FUNCTIONS.search(str(flt)):
            report.review.append(
                f"data source filter on {target!r} looks like row-level security: add a policy"
            )

    if not fields:
        raise ValueError(
            f"data source {title!r} has no columns in the workbook (a published source: generate the model "
            "from its .tds/.tdsx instead)"
        )
    doc: dict[str, Any] = {
        "schema": "tabfree/model@0",
        "id": model_id or snake(title, "m"),
        "title": title,
        "connection": {"engine": engine, "database": database},
        "tables": [t for t in tables.values() if t["columns"] or "sql" in t["source"]],
        "fields": fields,
    }
    for key, value in (
        ("relationships", relationships),
        ("parameters", parameters),
        ("groups", groups),
        ("bins", bins),
        ("sets", sets),
        ("hierarchies", hierarchies),
        ("rls", rls),
    ):
        if value:
            doc[key] = value
    source_info: dict[str, Any] = {"system": "tableau", "datasource": title}
    if ds.get("connection", {}).get("class"):
        source_info["connectionClass"] = ds["connection"]["class"]
    if ds.get("extract"):
        source_info["extract"] = {"converted": False}
    doc["source"] = source_info
    _check(doc, report)
    return doc, report


def _decorate(entry: dict[str, Any], col: Mapping[str, Any]) -> None:
    if col.get("defaultFormat") and len(str(col["defaultFormat"])) <= 128:
        entry["format"] = str(col["defaultFormat"]).lstrip("*")
    if col.get("folder"):
        entry["folder"] = str(col["folder"])
    if col.get("hidden"):
        entry["hidden"] = True
    if col.get("description"):
        entry["description"] = str(col["description"])
    role = str(col.get("semanticRole", "")).split("].[")[0].strip("[").lower()
    if role in _SEMANTIC_ROLES:
        entry["semanticRole"] = _SEMANTIC_ROLES[role]
    aliases = col.get("aliases")
    if isinstance(aliases, Mapping) and aliases:
        entry["aliases"] = {str(tableau_literal(k)): str(v) for k, v in aliases.items()}
    elif isinstance(aliases, Sequence) and not isinstance(aliases, str) and aliases:
        entry["aliases"] = {
            str(tableau_literal(a.get("key"))): str(a.get("value")) for a in aliases if isinstance(a, Mapping)
        }


def _is_aggregate_formula(formula: str) -> bool:
    try:
        from tabfree_compiler.calc import classify as kind
        from tabfree_compiler.calc import parse

        return kind(parse(formula)) in ("aggregate", "table_calc")
    except Exception:  # unparseable: let the compiler report it later
        return False


def _check(doc: dict[str, Any], report: GenerationReport) -> None:
    from tabfree_compiler.model import CompileError, SemanticModel, analyze_calcs
    from tabfree_schemas import SchemaKind, schema_errors

    report.schema_errors = schema_errors(SchemaKind.SEMANTIC_MODEL, doc)
    if report.schema_errors:
        return
    try:
        model = SemanticModel.from_dict(doc)
    except CompileError as exc:
        report.schema_errors.append(str(exc))
        return
    report.invalid_calcs = [f"{c.name}: {c.error}" for c in analyze_calcs(model) if c.error]
