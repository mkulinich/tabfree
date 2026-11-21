"""P1.12: the dashboards recreated from corpus workbooks, checked against Superstore's known figures.

Tableau's Sample - Superstore totals are published widely (sales $2,297,201, profit $286,397; Technology
$836,154, Furniture $741,999, Office Supplies $719,047), so they serve as ground truth here until P1.5
captures ground truth from a live Tableau Server.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from tabfree_api.main import create_app
from tabfree_api.sample import SUPERSTORE
from tabfree_api.settings import Settings
from tabfree_compiler import Session

pytest.importorskip("duckdb")
pytestmark = pytest.mark.skipif(
    not (SUPERSTORE / "orders.parquet").is_file(), reason="Superstore data absent"
)

EXEC = Session(username="erin", groups=frozenset({"Executives"}), today=dt.date(2019, 12, 30))
EMPTY: dict[str, Any] = {"filters": {}, "parameters": {}, "highlight": None}


@pytest.fixture(scope="module")
def ctx() -> Any:
    return create_app(Settings(environment="test", session_db=":memory:")).state.context


def run(
    ctx: Any, dashboard: str, query: str, state: dict[str, Any] = EMPTY, **selections: Any
) -> dict[str, Any]:
    manifest = ctx.site.bundle(dashboard).manifest
    result: dict[str, Any] = ctx.service.query(manifest, query, state, selections, EXEC)
    return result


def column(result: dict[str, Any], name: str) -> list[Any]:
    i = [c["name"] for c in result["columns"]].index(name)
    return [row[i] for row in result["rows"]]


def test_dimensional_hierarchy_matches_superstore_totals(ctx: Any) -> None:
    [[sales, total, share]] = run(ctx, "dimensional-hierarchy", "kpis")["rows"]
    assert sales == pytest.approx(2_297_200.86, abs=0.01)
    assert total == pytest.approx(sales)
    assert share == pytest.approx(1.0)
    by_category = run(ctx, "dimensional-hierarchy", "by_category")
    sales_by = dict(zip(column(by_category, "Category"), column(by_category, "Sales"), strict=True))
    assert sales_by == pytest.approx(
        {"Technology": 836_154.03, "Furniture": 741_999.80, "Office Supplies": 719_047.03}, abs=0.01
    )
    ratio = dict(zip(column(by_category, "Category"), column(by_category, "Profit Ratio"), strict=True))
    assert ratio["Furniture"] == pytest.approx(0.0249, abs=0.0001)


def test_fixed_total_ignores_filters_and_drill_actions(ctx: Any) -> None:
    state = {**EMPTY, "filters": {"region": {"members": ["West"]}}}
    result = run(ctx, "dimensional-hierarchy", "kpis", state, by_category=[{"Category": "Furniture"}])
    [[sales, total, share]] = result["rows"]
    assert total == pytest.approx(2_297_200.86, abs=0.01)  # FIXED: ignores the region filter and the drill
    assert sales < total
    assert share == pytest.approx(sales / total)


def test_drill_down_and_share_of_category(ctx: Any) -> None:
    products = run(ctx, "dimensional-hierarchy", "products", by_subcategory=[{"Sub-Category": "Copiers"}])
    assert set(column(products, "Sub-Category")) == {"Copiers"}
    assert sum(column(products, "Share of Category")) == pytest.approx(
        sum(column(products, "Sales")) / 836_154.03, rel=1e-6
    )
    assert len(run(ctx, "dimensional-hierarchy", "products")["rows"]) == 200  # the query's own limit


def test_sales_analysis_compares_prorated_months(ctx: Any) -> None:
    """Nov 1-15 2019 against Oct 1-15 2019, computed independently from the raw data."""
    import duckdb

    con = duckdb.connect()
    orders = str(SUPERSTORE / "orders.parquet")
    current, previous = con.execute(
        "SELECT SUM(sales) FILTER (WHERE order_date BETWEEN '2019-11-01' AND '2019-11-15'),"
        " SUM(sales) FILTER (WHERE order_date BETWEEN '2019-10-01' AND '2019-10-15') FROM read_parquet(?)",
        [orders],
    ).fetchone()
    bans = run(ctx, "sales-analysis", "bans")
    assert column(bans, "Sales (Current)")[0] == pytest.approx(current)
    assert column(bans, "Sales (% Change)")[0] == pytest.approx(current / previous - 1)

    moved = run(ctx, "sales-analysis", "bans", {**EMPTY, "parameters": {"anchor_date": "2019-03-31"}})
    march, february = con.execute(
        "SELECT SUM(sales) FILTER (WHERE order_date BETWEEN '2019-03-01' AND '2019-03-31'),"
        " SUM(sales) FILTER (WHERE order_date BETWEEN '2019-02-01' AND '2019-02-28') FROM read_parquet(?)",
        [orders],
    ).fetchone()
    assert column(moved, "Sales (Current)")[0] == pytest.approx(march)
    # Feb has 28 days; prorating keeps every February day, as in the eBike workbook.
    assert column(moved, "Sales (% Change)")[0] == pytest.approx(march / february - 1)


def test_sales_analysis_map_action_and_view_by(ctx: Any) -> None:
    all_states = column(run(ctx, "sales-analysis", "bans"), "Sales (Current)")[0]
    california = column(
        run(ctx, "sales-analysis", "bans", by_state=[{"State": "California"}]), "Sales (Current)"
    )[0]
    assert 0 < california < all_states
    trend = run(ctx, "sales-analysis", "trend")
    assert len(trend["rows"]) == 12
    # Table calculations run in the browser runtime (ADR-004), so the server returns Month and Sales only.
    assert [c["name"] for c in trend["columns"]] == ["Month", "Sales"]
    by_segment = run(ctx, "sales-analysis", "by_view", {**EMPTY, "parameters": {"view_by": "Segment"}})
    assert set(column(by_segment, "Group")) == {"Consumer", "Corporate", "Home Office"}
    assert len(run(ctx, "sales-analysis", "top_products")["rows"]) == 10
