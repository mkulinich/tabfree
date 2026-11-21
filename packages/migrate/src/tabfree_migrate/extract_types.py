"""Hyper → Parquet → Athena/Iceberg type mapping (BUILD_PLAN P2.8).

One rule per Hyper type tag (as ``tableauhyperapi.TypeTag`` names it). Each rule says what Athena
type the Iceberg column gets, whether Hyper must cast the value to text before ``COPY ... TO``
Parquet, and which parity rule (:mod:`tabfree_migrate.parity`) checks the column afterwards.

:func:`mapping_table_markdown` renders the table that the package README embeds; a test keeps the
two in sync, so the README is the documented mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Tableau stores geography as WGS 84 longitude/latitude; WKT carries no SRID, so it is recorded
#: in the table definition (``tabfree.geo.<column>.srid``) instead.
GEOGRAPHY_SRID = 4326

#: Hyper and Iceberg both cap decimals at 38 digits of precision.
MAX_DECIMAL_PRECISION = 38


class ParityKind(StrEnum):
    """How :mod:`tabfree_migrate.parity` compares a column (see the README for the rules)."""

    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    FLOAT32 = "float32"
    BOOLEAN = "boolean"
    TEXT = "text"
    DATE = "date"
    TIMESTAMP = "timestamp"
    TIMESTAMP_TZ = "timestamp_tz"
    WKT = "wkt"
    BINARY = "binary"


@dataclass(frozen=True)
class TypeRule:
    hyper: str
    athena: str
    export_as_text: bool
    parity: ParityKind
    note: str


#: The mapping. ``athena`` for NUMERIC is a template filled by :func:`map_type`.
TYPE_RULES: dict[str, TypeRule] = {
    rule.hyper: rule
    for rule in [
        TypeRule("BOOL", "boolean", False, ParityKind.BOOLEAN, "Parity also counts true values."),
        TypeRule(
            "SMALL_INT",
            "integer",
            False,
            ParityKind.INTEGER,
            "Widened: Iceberg has no 16-bit integer type. Lossless.",
        ),
        TypeRule("INT", "integer", False, ParityKind.INTEGER, ""),
        TypeRule("BIG_INT", "bigint", False, ParityKind.INTEGER, ""),
        TypeRule("OID", "bigint", False, ParityKind.INTEGER, "Unsigned 32-bit; fits in bigint."),
        TypeRule(
            "NUMERIC",
            "decimal(p,s)",
            False,
            ParityKind.DECIMAL,
            "Precision and scale kept up to 38 digits; wider values become double (lossy, flagged).",
        ),
        TypeRule("DOUBLE", "double", False, ParityKind.FLOAT, "Sums compared with a relative tolerance."),
        TypeRule(
            "FLOAT",
            "float",
            False,
            ParityKind.FLOAT32,
            "32-bit REAL; compared with a looser float32 tolerance.",
        ),
        TypeRule(
            "TEXT",
            "string",
            False,
            ParityKind.TEXT,
            "Collation is recorded; Athena compares code points (case-sensitive).",
        ),
        TypeRule("VARCHAR", "string", False, ParityKind.TEXT, "Length limit is not enforced by Iceberg."),
        TypeRule("CHAR", "string", False, ParityKind.TEXT, "Fixed length is not kept."),
        TypeRule("JSON", "string", False, ParityKind.TEXT, "Kept as JSON text; parse with json_extract."),
        TypeRule("DATE", "date", False, ParityKind.DATE, ""),
        TypeRule(
            "TIMESTAMP",
            "timestamp",
            False,
            ParityKind.TIMESTAMP,
            "Wall-clock time without zone; Iceberg timestamp (microseconds).",
        ),
        TypeRule(
            "TIMESTAMP_TZ",
            "timestamp",
            False,
            ParityKind.TIMESTAMP_TZ,
            "Stored as the UTC instant (Parquet isAdjustedToUTC); zone recorded in tabfree.tz.* parameters.",
        ),
        TypeRule(
            "TIME",
            "string",
            True,
            ParityKind.TEXT,
            "Athena tables have no TIME type: HH:MM:SS[.ffffff] text.",
        ),
        TypeRule("INTERVAL", "string", True, ParityKind.TEXT, "Hyper's interval text form."),
        TypeRule(
            "GEOGRAPHY",
            "string",
            True,
            ParityKind.WKT,
            f"WKT text, SRID {GEOGRAPHY_SRID} (WGS 84) recorded; use ST_GeometryFromText in Athena.",
        ),
        TypeRule("BYTES", "binary", False, ParityKind.BINARY, "Parity checks counts only."),
    ]
}

#: Unknown or new Hyper types: cast to text so the export never fails, and say so in the report.
FALLBACK_RULE = TypeRule(
    "(other)",
    "string",
    True,
    ParityKind.TEXT,
    "Unknown Hyper type: exported as text and flagged in the report.",
)

#: SQL spellings of the same types (Hyper's catalog reports tags, but older releases vary).
_ALIASES = {
    "REAL": "FLOAT",
    "BOOLEAN": "BOOL",
    "BIGINT": "BIG_INT",
    "SMALLINT": "SMALL_INT",
    "INTEGER": "INT",
}

#: Collations that order and compare like Athena (by code point, case-sensitive).
_BINARY_COLLATIONS = {"", "binary", "ucs_basic", "c", "posix"}


def is_binary_collation(collation: str | None) -> bool:
    return collation is None or collation.strip().lower() in _BINARY_COLLATIONS


@dataclass(frozen=True)
class MappedType:
    athena: str
    export_as_text: bool
    parity: ParityKind
    notes: list[str] = field(default_factory=list)
    scale: int | None = None


def map_type(
    hyper_type: str,
    precision: int | None = None,
    scale: int | None = None,
    collation: str | None = None,
) -> MappedType:
    """Map one Hyper column type to its Athena/Iceberg type, export form and parity rule."""
    name = hyper_type.upper().split(".")[-1]
    name = _ALIASES.get(name, name)
    rule = TYPE_RULES.get(name)
    notes: list[str] = []
    if rule is None:
        notes.append(f"{FALLBACK_RULE.note} ({name})")
        return MappedType(FALLBACK_RULE.athena, True, FALLBACK_RULE.parity, notes)
    if rule.note:
        notes.append(rule.note)
    if name == "NUMERIC":
        if precision and precision <= MAX_DECIMAL_PRECISION:
            return MappedType(
                f"decimal({precision},{scale or 0})", False, ParityKind.DECIMAL, notes, scale or 0
            )
        notes.append(
            f"NUMERIC({precision},{scale}) exceeds {MAX_DECIMAL_PRECISION} digits: stored as double."
        )
        return MappedType("double", False, ParityKind.FLOAT, notes)
    if rule.parity is ParityKind.TEXT and not is_binary_collation(collation):
        notes.append(
            f"Hyper collation {collation!r} differs from Athena's code-point order: text min/max and "
            "distinct counts are advisory, and case-insensitive filters need lower() in Athena."
        )
    return MappedType(rule.athena, rule.export_as_text, rule.parity, notes)


def mapping_table_markdown() -> str:
    """The mapping as a Markdown table (embedded verbatim in packages/migrate/README.md)."""
    lines = [
        "| Hyper type | Athena / Iceberg type | Export | Parity rule | Notes |",
        "|---|---|---|---|---|",
    ]
    for rule in [*TYPE_RULES.values(), FALLBACK_RULE]:
        export = "`CAST(... AS TEXT)`" if rule.export_as_text else "as is"
        lines.append(f"| `{rule.hyper}` | `{rule.athena}` | {export} | {rule.parity.value} | {rule.note} |")
    return "\n".join(lines)
