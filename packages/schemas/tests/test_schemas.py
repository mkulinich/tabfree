from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tabfree_schemas import (
    SchemaKind,
    SchemaValidationError,
    check_manifest_references,
    detect_kind,
    load_document,
    load_schema,
    schema_errors,
    validate,
)
from tabfree_schemas.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
MANIFEST_PATH = EXAMPLES / "dashboards" / "sales-overview" / "tabfree.json"
MODEL_PATH = EXAMPLES / "models" / "superstore_sales.model.yaml"
IR_PATH = EXAMPLES / "ir" / "superstore-overview.ir.json"


def manifest() -> dict[str, Any]:
    return copy.deepcopy(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))


def model() -> dict[str, Any]:
    document = load_document(MODEL_PATH)
    assert isinstance(document, dict)
    return document


@pytest.mark.parametrize("kind", list(SchemaKind))
def test_schemas_are_valid_draft_2020_12(kind: SchemaKind) -> None:
    schema = load_schema(kind)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (MANIFEST_PATH, SchemaKind.DASHBOARD_MANIFEST),
        (MODEL_PATH, SchemaKind.SEMANTIC_MODEL),
        (IR_PATH, SchemaKind.TABLEAU_IR),
    ],
)
def test_examples_are_valid(path: Path, kind: SchemaKind) -> None:
    assert validate(load_document(path)) is kind


def test_detect_kind_rejects_unknown_documents() -> None:
    with pytest.raises(ValueError, match="cannot tell the document type"):
        detect_kind({"schema": "something-else"})
    with pytest.raises(ValueError, match="cannot tell the document type"):
        detect_kind(["not", "a", "mapping"])


# --- YAML loading --------------------------------------------------------------------------------


def test_yaml_keeps_on_yes_no_and_dates_as_strings(tmp_path: Path) -> None:
    path = tmp_path / "doc.yaml"
    path.write_text("on: yes\nflag: true\nwhen: 2026-11-20\nno: off\n", encoding="utf-8")
    assert load_document(path) == {"on": "yes", "flag": True, "when": "2026-11-20", "no": "off"}


def test_model_relationship_on_key_survives_yaml() -> None:
    assert model()["relationships"][0]["on"] == [{"left": "region", "right": "region"}]


# --- Dashboard manifest: schema ------------------------------------------------------------------


def test_manifest_requires_queries() -> None:
    doc = manifest()
    del doc["queries"]
    assert any(
        "'queries' is a required property" in e for e in schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)
    )


def test_manifest_rejects_unknown_top_level_property() -> None:
    doc = manifest()
    doc["sql"] = "SELECT * FROM orders"
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


def test_filter_needs_exactly_one_of_field_or_expr() -> None:
    doc = manifest()
    doc["filters"]["region"]["expr"] = "[Region]"
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


def test_categorical_query_filter_requires_members() -> None:
    doc = manifest()
    doc["queries"]["by_region"]["where"] = [{"field": "Segment", "kind": "categorical"}]
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)
    doc["queries"]["by_region"]["where"][0]["members"] = ["Consumer"]
    assert not schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


def test_unknown_action_type_is_rejected() -> None:
    doc = manifest()
    doc["actions"].append({"type": "teleport", "source": ["by_region"]})
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


def test_action_rejects_properties_of_other_action_types() -> None:
    doc = manifest()
    doc["actions"][0]["url"] = "https://example.com/<Region>"
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


def test_custom_table_calc_requires_expr_and_others_require_of() -> None:
    doc = manifest()
    calcs = doc["queries"]["trend"]["tableCalcs"]
    calcs.append({"name": "Moving", "type": "custom"})
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)
    calcs[-1]["expr"] = "WINDOW_AVG(SUM([Sales]), -2, 0)"
    calcs.append({"name": "Rank", "type": "rank"})
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)
    calcs[-1]["of"] = "Sales"
    assert not schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


def test_query_needs_a_dimension_or_measure() -> None:
    doc = manifest()
    doc["queries"]["empty"] = {"dimensions": [], "measures": []}
    assert schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)


# --- Dashboard manifest: reference checks --------------------------------------------------------


def test_example_manifest_references_are_consistent() -> None:
    assert check_manifest_references(manifest()) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["filters"]["region"].update(appliesTo=["nope"]), "unknown query 'nope'"),
        (lambda d: d["queries"]["kpis"].update(model="other"), "unknown model alias 'other'"),
        (
            lambda d: d["queries"]["by_region"]["sort"].append({"by": "Margin"}),
            "unknown output 'Margin'",
        ),
        (
            lambda d: d["queries"]["trend"]["tableCalcs"][0].update(of="Profit"),
            "unknown output 'Profit'",
        ),
        (lambda d: d["actions"][0].update(targets=["missing"]), "unknown query 'missing'"),
        (
            lambda d: d["actions"].append(
                {
                    "type": "parameter",
                    "source": ["by_region"],
                    "parameter": "nope",
                    "field": "Region",
                }
            ),
            "unknown parameter 'nope'",
        ),
        (
            lambda d: d["queries"]["top_customers"]["where"][0]["topN"].update(n={"param": "absent"}),
            "unknown parameter 'absent'",
        ),
        (
            lambda d: d["queries"]["by_region"]["measures"].append({"field": "Sales", "agg": "avg"}),
            "duplicate output name 'Sales'",
        ),
    ],
)
def test_reference_errors_are_reported(mutate: Any, expected: str) -> None:
    doc = manifest()
    mutate(doc)
    assert not schema_errors(SchemaKind.DASHBOARD_MANIFEST, doc)
    errors = check_manifest_references(doc)
    assert any(expected in e for e in errors), errors


def test_multiple_models_require_explicit_model_on_queries() -> None:
    doc = manifest()
    doc["models"]["finance"] = "finance_ledger"
    errors = check_manifest_references(doc)
    assert any('"model" is required' in e for e in errors)


def test_validate_raises_with_all_errors() -> None:
    doc = manifest()
    doc["filters"]["region"]["appliesTo"] = ["nope"]
    with pytest.raises(SchemaValidationError) as excinfo:
        validate(doc)
    assert excinfo.value.kind is SchemaKind.DASHBOARD_MANIFEST
    assert any("unknown query 'nope'" in e for e in excinfo.value.errors)


# --- Semantic model ------------------------------------------------------------------------------


def test_field_cannot_be_both_physical_and_calculated() -> None:
    doc = model()
    doc["fields"][0]["formula"] = "[Order Date]"
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, doc)


def test_connection_engine_must_be_supported() -> None:
    doc = model()
    doc["connection"] = {"engine": "snowflake", "database": "x"}
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, doc)


def test_identifiers_reject_sql_injection_attempts() -> None:
    doc = model()
    doc["tables"][0]["source"] = {"table": "orders; DROP TABLE people"}
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, doc)


def test_rls_policy_needs_filter_or_entitlement() -> None:
    doc = model()
    doc["rls"][0].pop("filter")
    assert schema_errors(SchemaKind.SEMANTIC_MODEL, doc)
    doc["rls"][0]["entitlement"] = {
        "table": "people",
        "userColumn": "regional_manager",
        "keys": [{"field": "Region", "column": "region"}],
    }
    assert not schema_errors(SchemaKind.SEMANTIC_MODEL, doc)


# --- Tableau IR ----------------------------------------------------------------------------------


def test_ir_rejects_captured_passwords() -> None:
    doc = load_document(IR_PATH)
    doc["datasources"][0]["namedConnections"][0]["attributes"] = {"password": "hunter2"}
    assert schema_errors(SchemaKind.TABLEAU_IR, doc)


def test_ir_requires_sha256_of_source_file() -> None:
    doc = load_document(IR_PATH)
    doc["source"]["sha256"] = "not-a-hash"
    assert schema_errors(SchemaKind.TABLEAU_IR, doc)


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_accepts_examples(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(MANIFEST_PATH), str(MODEL_PATH), str(IR_PATH)]) == 0
    out = capsys.readouterr().out
    assert out.count("ok ") == 3


def test_cli_reports_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "tabfree.json"
    doc = manifest()
    doc["filters"]["region"]["appliesTo"] = ["nope"]
    bad.write_text(json.dumps(doc), encoding="utf-8")
    unreadable = tmp_path / "broken.json"
    unreadable.write_text("{not json", encoding="utf-8")
    assert main([str(bad), str(unreadable)]) == 1
    out = capsys.readouterr().out
    assert "unknown query 'nope'" in out
    assert f"FAIL {unreadable}" in out
