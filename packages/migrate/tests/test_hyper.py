from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tabfree_migrate.hyper import (
    ColumnInfo,
    TableResult,
    athena_ddl,
    athena_type,
    glue_identifier,
    unique_identifiers,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPERSTORE = (
    REPO_ROOT
    / "corpus/extracts/tableau__hyper-api-samples/Tableau-Supported/Python/data/superstore_sample.hyper"
)


@pytest.mark.parametrize(
    ("hyper", "precision", "scale", "expected"),
    [
        ("TEXT", None, None, "string"),
        ("BIG_INT", None, None, "bigint"),
        ("NUMERIC", 18, 2, "decimal(18,2)"),
        ("NUMERIC", None, None, "double"),
        ("GEOGRAPHY", None, None, "string"),
        ("TIMESTAMP_TZ", None, None, "timestamp"),
        ("SOMETHING_NEW", None, None, "string"),
    ],
)
def test_athena_type(hyper: str, precision: int | None, scale: int | None, expected: str) -> None:
    assert athena_type(hyper, precision, scale) == expected


def test_glue_identifiers_are_safe_and_unique() -> None:
    assert glue_identifier("Order Date") == "order_date"
    assert glue_identifier("2024 Sales ($)") == "c_2024_sales"
    assert glue_identifier("'; DROP TABLE x; --") == "drop_table_x"
    assert unique_identifiers(["Sales", "sales", "SALES!"]) == ["sales", "sales_2", "sales_3"]


def test_athena_ddl_stages_parquet_then_ctas_iceberg() -> None:
    result = TableResult(
        schema="Extract",
        table="Extract",
        glue_table="orders",
        rows=3,
        columns=[ColumnInfo("Order Date", "order_date", "DATE", "date", True)],
        parquet="/tmp/orders.parquet",  # noqa: S108 - illustrative path only
    )
    staging, ctas, drop = athena_ddl(
        result,
        database="tabfree_lake",
        staging_location="s3://raw/orders/",
        iceberg_location="s3://lake/orders/",
    )
    assert "CREATE EXTERNAL TABLE IF NOT EXISTS `tabfree_lake`.`orders__staging`" in staging
    assert "`order_date` date" in staging
    assert "LOCATION 's3://raw/orders/'" in staging
    assert "table_type = 'ICEBERG'" in ctas
    assert "location = 's3://lake/orders/'" in ctas
    assert drop == "DROP TABLE `tabfree_lake`.`orders__staging`"


@pytest.mark.skipif(
    importlib.util.find_spec("tableauhyperapi") is None,
    reason="tableauhyperapi not installed (Linux x86-64/Windows only); runs in the CI hyper job",
)
def test_convert_corpus_superstore(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    from tabfree_migrate.hyper import convert_hyper

    results = convert_hyper(SUPERSTORE, tmp_path)
    assert results
    for result in results:
        table = pq.read_table(result.parquet)
        assert table.num_rows == result.rows > 0
        assert table.column_names == [c.glue_name for c in result.columns]
        for column in result.columns:
            assert table.column(column.glue_name).null_count == result.rows - int(
                result.parity[column.glue_name]["count"] or 0
            )
