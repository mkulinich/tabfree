"""Load TabFree JSON Schemas and validate documents against them."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


class SchemaKind(StrEnum):
    """Document types that TabFree defines schemas for."""

    TABLEAU_IR = "tableau-ir"
    SEMANTIC_MODEL = "semantic-model"
    DASHBOARD_MANIFEST = "dashboard-manifest"


#: Value of each document's top-level ``schema`` property.
SCHEMA_IDS: dict[SchemaKind, str] = {
    SchemaKind.TABLEAU_IR: "tabfree/tableau-ir@1",
    SchemaKind.SEMANTIC_MODEL: "tabfree/model@0",
    SchemaKind.DASHBOARD_MANIFEST: "tabfree/dashboard@0",
}


class SchemaValidationError(ValueError):
    """A document failed its JSON Schema or the manifest reference checks."""

    def __init__(self, kind: SchemaKind, errors: Sequence[str]) -> None:
        self.kind = kind
        self.errors = list(errors)
        shown = "; ".join(self.errors[:5])
        more = f" (+{len(self.errors) - 5} more)" if len(self.errors) > 5 else ""
        super().__init__(f"invalid {kind.value}: {shown}{more}")


@cache
def load_schema(kind: SchemaKind) -> dict[str, Any]:
    """Return the JSON Schema for ``kind``, checked against the 2020-12 metaschema."""
    resource = resources.files("tabfree_schemas").joinpath("json", f"{kind.value}.schema.json")
    schema: dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def _validator(kind: SchemaKind) -> Draft202012Validator:
    return Draft202012Validator(load_schema(kind), format_checker=Draft202012Validator.FORMAT_CHECKER)


def _location(error: ValidationError) -> str:
    parts = (f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
    return "$" + "".join(parts)


def schema_errors(kind: SchemaKind, document: Any) -> list[str]:
    """Return readable schema violations sorted by location; empty when valid."""
    found = sorted(
        _validator(kind).iter_errors(document),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    return [f"{_location(e)}: {e.message}" for e in found]


def detect_kind(document: Any) -> SchemaKind:
    """Infer the document type from its top-level ``schema`` property."""
    if isinstance(document, Mapping):
        marker = document.get("schema")
        for kind, schema_id in SCHEMA_IDS.items():
            if marker == schema_id:
                return kind
    expected = ", ".join(repr(v) for v in SCHEMA_IDS.values())
    raise ValueError(f'cannot tell the document type: expected a "schema" property of {expected}')


def validate(document: Any, kind: SchemaKind | None = None) -> SchemaKind:
    """Validate ``document`` and return its kind; raise :class:`SchemaValidationError`."""
    resolved = kind or detect_kind(document)
    errors = schema_errors(resolved, document)
    if not errors and resolved is SchemaKind.DASHBOARD_MANIFEST:
        errors = check_manifest_references(document)
    if errors:
        raise SchemaValidationError(resolved, errors)
    return resolved


# --- YAML ----------------------------------------------------------------------------------------

_BOOL_TAG = "tag:yaml.org,2002:bool"
_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


class _Yaml12Loader(yaml.SafeLoader):
    """Safe loader with YAML 1.2 booleans and string timestamps.

    PyYAML follows YAML 1.1, which reads ``on``/``off``/``yes``/``no`` as booleans (so a
    relationship's ``on:`` key would become ``True``) and turns dates into ``datetime``
    objects. Models are authored by people and by Claude, so we use JSON-like semantics.
    """


_Yaml12Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag not in (_BOOL_TAG, _TIMESTAMP_TAG)]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(
    _BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def load_document(path: Path) -> Any:
    """Read a JSON or YAML document. YAML is parsed safely with YAML 1.2 booleans."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.load(text, Loader=_Yaml12Loader)  # noqa: S506 - subclass of SafeLoader
    return json.loads(text)


# --- Manifest reference checks -------------------------------------------------------------------


def _output_name(item: Any) -> str:
    if isinstance(item, str):
        return item
    name = item.get("as") or item.get("field")
    return str(name)


def _query_outputs(where: str, query: Mapping[str, Any], errors: list[str]) -> set[str]:
    outputs: set[str] = set()
    for group in ("dimensions", "measures"):
        for item in query.get(group, []):
            name = _output_name(item)
            if name in outputs:
                errors.append(f'{where}: duplicate output name {name!r}; give one of them "as"')
            outputs.add(name)
    return outputs


def _param_ref(value: Any) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("param"), str):
        return str(value["param"])
    return None


def check_manifest_references(manifest: Mapping[str, Any]) -> list[str]:
    """Cross-reference checks a JSON Schema cannot express.

    Run after schema validation passes. Returns readable errors; empty when consistent.
    """
    errors: list[str] = []
    models = set(manifest.get("models", {}))
    queries: Mapping[str, Any] = manifest.get("queries", {})
    filters: Mapping[str, Any] = manifest.get("filters", {})
    parameters: Mapping[str, Any] = manifest.get("parameters", {})

    def check_model(where: str, alias: str | None) -> None:
        if alias is None:
            if len(models) > 1:
                errors.append(f'{where}: "model" is required when the dashboard uses several models')
        elif alias not in models:
            errors.append(f"{where}: unknown model alias {alias!r}")

    def check_param(where: str, name: str | None) -> None:
        if name is not None and name not in parameters:
            errors.append(f"{where}: unknown parameter {name!r}")

    for qname, query in queries.items():
        where = f"$.queries.{qname}"
        check_model(where, query.get("model"))
        outputs = _query_outputs(where, query, errors)
        for i, calc in enumerate(query.get("tableCalcs", [])):
            of = calc.get("of")
            if of is not None and of not in outputs:
                errors.append(f'{where}.tableCalcs[{i}]: "of" refers to unknown output {of!r}')
            for key in ("addressing", "partitioning"):
                for dim in calc.get(key, []):
                    if dim not in outputs:
                        errors.append(f"{where}.tableCalcs[{i}].{key}: unknown output {dim!r}")
            outputs.add(calc["name"])
        for i, sort in enumerate(query.get("sort", [])):
            if sort["by"] not in outputs:
                errors.append(f"{where}.sort[{i}]: unknown output {sort['by']!r}")
        for dim in query.get("totals", {}).get("subtotals", []):
            if dim not in outputs:
                errors.append(f"{where}.totals.subtotals: unknown output {dim!r}")
        for i, flt in enumerate(query.get("where", [])):
            check_param(f"{where}.where[{i}].topN.n", _param_ref(flt.get("topN", {}).get("n")))

    for fname, flt in filters.items():
        where = f"$.filters.{fname}"
        check_model(where, flt.get("model"))
        applies = flt.get("appliesTo")
        if isinstance(applies, list):
            for qname in applies:
                if qname not in queries:
                    errors.append(f"{where}.appliesTo: unknown query {qname!r}")

    for pname, param in parameters.items():
        allowed = param.get("allowed")
        if isinstance(allowed, Mapping) and "query" in allowed and allowed["query"] not in queries:
            errors.append(f"$.parameters.{pname}.allowed: unknown query {allowed['query']!r}")

    for i, action in enumerate(manifest.get("actions", [])):
        where = f"$.actions[{i}]"
        for qname in action.get("source", []):
            if qname not in queries:
                errors.append(f"{where}.source: unknown query {qname!r}")
        targets = action.get("targets")
        if isinstance(targets, list):
            for qname in targets:
                if qname not in queries:
                    errors.append(f"{where}.targets: unknown query {qname!r}")
        if action.get("type") == "parameter":
            check_param(where, action.get("parameter"))

    return errors
