from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
import sqlglot

from tabfree_migrate.hyper import (
    ColumnInfo,
    TableResult,
    athena_ddl,
    athena_parity_query,
    athena_type,
    glue_identifier,
    iceberg_table_ddl,
    unique_identifiers,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTRACTS = REPO_ROOT / "corpus/extracts"
SUPERSTORE = EXTRACTS / "tableau__hyper-api-samples/Tableau-Supported/Python/data/superstore_sample.hyper"
#: One extract per interesting shape: plain sample, geography, many types (TDVT Calcs), older file.
PARITY_CORPUS = [
    SUPERSTORE,
    EXTRACTS
    / "tableau__hyper-api-samples/Community-Supported/adjust-vertex-order/europe_interior_right.hyper",
    EXTRACTS / "tableau__connector-plugin-sdk/tdvt/test/tool_test/tds/Calcs (TestV1).hyper",
    EXTRACTS / "tableau__server-client-python/test/assets/World Indicators.hyper",
]

needs_hyper = pytest.mark.skipif(
    importlib.util.find_spec("tableauhyperapi") is None,
    reason="tableauhyperapi not installed (Linux x86-64/Windows only); runs in the CI hyper job",
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
        ("SMALL_INT", None, None, "integer"),
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


def _result() -> TableResult:
    return TableResult(
        schema="Extract",
        table="Extract",
        glue_table="orders",
        rows=3,
        columns=[
            ColumnInfo("Order Date", "order_date", "DATE", "date", True),
            ColumnInfo("Where", "where", "GEOGRAPHY", "string", True),
        ],
        parquet="/tmp/orders.parquet",  # noqa: S108 - illustrative path only
    )


def test_column_info_derives_parity_and_export() -> None:
    geo = _result().columns[1]
    assert (geo.parity, geo.export_as_text) == ("wkt", True)
    assert ColumnInfo.from_dict({**geo.__dict__, "unknown": 1}) == geo


def test_athena_ddl_stages_parquet_then_ctas_iceberg() -> None:
    staging, ctas, drop = athena_ddl(
        _result(),
        database="tabfree_lake",
        staging_location="s3://raw/orders/",
        iceberg_location="s3://lake/orders/",
        table_properties={"tabfree.extract.incremental_key": "order_date", "tabfree.x": "it's"},
    )
    assert "CREATE EXTERNAL TABLE IF NOT EXISTS `tabfree_lake`.`orders__staging`" in staging
    assert "`order_date` date" in staging
    assert "LOCATION 's3://raw/orders/'" in staging
    assert "'tabfree.extract.incremental_key' = 'order_date'" in staging
    assert "'tabfree.x' = 'it''s'" in staging
    assert "table_type = 'ICEBERG'" in ctas
    assert "location = 's3://lake/orders/'" in ctas
    assert drop == "DROP TABLE `tabfree_lake`.`orders__staging`"
    plain, _, _ = athena_ddl(_result(), database="d", staging_location="s3://a/", iceberg_location="s3://b/")
    assert "TBLPROPERTIES" not in plain


def test_rebuild_ddl_and_parity_query() -> None:
    ddl = iceberg_table_ddl(_result(), database="db", location="s3://lake/orders/")
    assert ddl.startswith("CREATE TABLE `db`.`orders` (")
    assert "'table_type' = 'ICEBERG'" in ddl
    query = athena_parity_query(_result(), database="db")
    assert query.sql.endswith('FROM "db"."orders"')
    assert sqlglot.parse_one(query.sql, read="trino") is not None


# --- CI hyper job only ---------------------------------------------------------------------------


@needs_hyper
def test_convert_corpus_superstore(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    from tabfree_migrate.hyper import convert_hyper

    results = convert_hyper(SUPERSTORE, tmp_path, table_prefix="superstore")
    assert results
    for result in results:
        assert result.glue_table.startswith("superstore__")
        table = pq.read_table(result.parquet)
        assert table.num_rows == result.rows > 0
        assert table.column_names == [c.glue_name for c in result.columns]
        for column in result.columns:
            stats = result.stats["columns"][column.glue_name]
            assert table.column(column.glue_name).null_count == stats["nulls"]


@needs_hyper
@pytest.mark.parametrize("extract", PARITY_CORPUS, ids=lambda p: p.name)
def test_hyper_stats_match_duckdb_over_the_exported_parquet(extract: Path, tmp_path: Path) -> None:
    """Hyper-side statistics vs the same statistics on the Parquet (DuckDB stands in for Athena)."""
    from tabfree_migrate.hyper import convert_hyper
    from tabfree_migrate.parity import compare_stats, duckdb_parquet_runner, measure

    source = tmp_path / extract.name
    shutil.copyfile(extract, source)  # Hyper may touch the file; never modify the corpus
    results = convert_hyper(source, tmp_path / "out")
    assert results
    for result in results:
        runner = duckdb_parquet_runner({result.glue_table: result.parquet})
        actual = measure(runner, f'"{result.glue_table}"', result.columns, "duckdb")
        parity = compare_stats(result.hyper_stats(), actual, result.columns)
        assert parity.ok, parity.to_dict()
        if extract.name.startswith("europe"):
            geo = [c for c in result.columns if "GEOGRAPHY" in c.hyper_type.upper()]
            types = [(c.name, c.hyper_type) for c in result.columns]
            assert geo, f"sample should hold a geography column: {types}"
            first = str(result.stats["columns"][geo[0].glue_name]["min"])
            assert first.startswith(("POLYGON", "MULTIPOLYGON", "POINT", "LINESTRING"))


@needs_hyper
def test_convert_batch_with_real_hyper(tmp_path: Path) -> None:
    from tabfree_migrate.blobstore import LocalDirSink, load_json
    from tabfree_migrate.convert_batch import BatchOptions, convert_batch, report_key, sources_from_directory

    inputs = tmp_path / "in"
    inputs.mkdir()
    shutil.copyfile(SUPERSTORE, inputs / SUPERSTORE.name)
    shutil.copyfile(EXTRACTS / "tableau__VizAlerts/demo/VizAlerts.tde", inputs / "VizAlerts.tde")
    out = tmp_path / "out"
    options = BatchOptions(out=str(out), work_dir=str(tmp_path / "work"))
    stats = convert_batch(sources_from_directory(inputs), options, workers=2)
    assert (stats.converted, stats.flagged, stats.failed) == (1, 1, 0), [
        r.get("error") for r in stats.reports
    ]
    report = next(r for r in stats.reports if r["status"] == "converted")
    assert load_json(LocalDirSink(out), report_key(report["sha256"]))["tables"]
    rerun = convert_batch(sources_from_directory(inputs), options, workers=2)
    assert (rerun.converted, rerun.skipped) == (0, 2)
