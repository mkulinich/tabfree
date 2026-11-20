"""JSON Schemas and validators for TabFree documents.

Three document types are the contracts between TabFree's components:

* ``dashboard-manifest``: ``tabfree.json``, the governed data contract of a dashboard bundle.
* ``semantic-model``: tables, relationships, fields, calculations and row-level security.
* ``tableau-ir``: the parsed form of a Tableau workbook or data source.
"""

from tabfree_schemas.validate import (
    SCHEMA_IDS,
    SchemaKind,
    SchemaValidationError,
    check_manifest_references,
    detect_kind,
    load_document,
    load_schema,
    schema_errors,
    validate,
)

__version__ = "0.0.1"

__all__ = [
    "SCHEMA_IDS",
    "SchemaKind",
    "SchemaValidationError",
    "__version__",
    "check_manifest_references",
    "detect_kind",
    "load_document",
    "load_schema",
    "schema_errors",
    "validate",
]
