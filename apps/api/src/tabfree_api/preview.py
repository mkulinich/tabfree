"""Local dashboard preview: the example dashboards over generated sample data, on DuckDB.

Development only. It is mounted when ``TABFREE_PREVIEW=1``, and it has **no authentication**: the
viewer picks a sample user in the page so row-level security can be tried out. The real governed
query API (P1.13) replaces this with OIDC sign-in, permissions and Athena or Redshift. Even so, the
data path is the production one: the browser names a query and sends filter values, the server
binds them to the manifest and compiles with the viewer's session.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from tabfree_compiler import CompileError, SemanticModel, Session, compile_query
from tabfree_compiler.dashboard import BoundQuery, bind_domain, bind_query

REPO_ROOT = Path(__file__).resolve().parents[4]
ROW_LIMIT = 10_000

#: Sample viewers. Managers see their own region (the model's RLS policy); executives see all.
USERS: dict[str, dict[str, Any]] = {
    "erin": {"fullname": "Erin (executive)", "groups": ["Executives"]},
    "alice": {"fullname": "Alice (East manager)", "groups": ["Managers"]},
    "bob": {"fullname": "Bob (West manager)", "groups": ["Managers"]},
    "carol": {"fullname": "Carol (Central manager)", "groups": ["Managers"]},
    "dave": {"fullname": "Dave (South manager)", "groups": ["Managers"]},
}
MANAGERS = {"East": "alice", "West": "bob", "Central": "carol", "South": "dave"}
_COLUMN_TYPES = {"string", "integer", "float", "decimal", "boolean", "date", "datetime", "geography"}


@dataclass(frozen=True)
class PreviewPaths:
    dashboards: Path
    models: Path
    runtime: Path

    @classmethod
    def default(cls) -> PreviewPaths:
        examples = REPO_ROOT / "packages" / "schemas" / "examples"
        return cls(
            examples / "dashboards",
            examples / "models",
            REPO_ROOT / "packages" / "runtime" / "dist" / "bundle",
        )


# ---- Sample data ------------------------------------------------------------------------------

_STATES = {
    "East": ["New York", "Pennsylvania", "Massachusetts", "Ohio"],
    "West": ["California", "Washington", "Oregon", "Colorado"],
    "Central": ["Texas", "Illinois", "Michigan", "Minnesota"],
    "South": ["Florida", "Georgia", "Virginia", "Tennessee"],
}
_PRODUCTS = {
    "Furniture": ["Chairs", "Tables", "Bookcases", "Furnishings"],
    "Office Supplies": ["Paper", "Binders", "Storage", "Art"],
    "Technology": ["Phones", "Accessories", "Machines", "Copiers"],
}
_FIRST = ["Ana", "Ben", "Cleo", "Dev", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jun", "Kai", "Lena"]
_LAST = ["Park", "Ruiz", "Stone", "Okafor", "Nguyen", "Weber"]


def sample_rows(seed: int = 7, count: int = 2400) -> list[tuple[Any, ...]]:
    """Deterministic Superstore-shaped orders for 2023 and 2024."""
    rng = random.Random(seed)  # noqa: S311 - sample data, not security
    customers = [
        (f"{first} {last}", rng.choice(["Consumer", "Corporate", "Home Office"]))
        for first in _FIRST
        for last in _LAST
    ]
    start = dt.date(2023, 1, 1)
    rows = []
    for i in range(count):
        name, segment = rng.choice(customers)
        region = rng.choices(list(_STATES), weights=[3, 3, 2, 2])[0]
        category = rng.choices(list(_PRODUCTS), weights=[2, 5, 3])[0]
        sales = round(rng.lognormvariate(4.5 if category != "Technology" else 5.2, 0.9), 2)
        discount = rng.choice([0, 0, 0, 0.1, 0.2, 0.3])
        margin = rng.uniform(-0.05, 0.4) - discount / 2
        rows.append(
            (
                i + 1,
                f"ORD-{10000 + i // 2}",
                start + dt.timedelta(days=rng.randrange(730)),
                rng.choice(["Standard", "Second Class", "First Class", "Same Day"]),
                name,
                segment,
                region,
                rng.choice(_STATES[region]),
                category,
                rng.choice(_PRODUCTS[category]),
                sales,
                rng.randint(1, 9),
                discount,
                round(sales * margin, 2),
            )
        )
    return rows


def load_sample_data(con: Any) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS tabfree_lake")
    con.execute(
        "CREATE OR REPLACE TABLE tabfree_lake.orders (row_id INTEGER, order_id VARCHAR, order_date DATE, "
        "ship_mode VARCHAR, customer_name VARCHAR, segment VARCHAR, region VARCHAR, state VARCHAR, "
        "category VARCHAR, sub_category VARCHAR, sales DOUBLE, quantity INTEGER, discount DOUBLE, "
        "profit DOUBLE)"
    )
    con.executemany(
        "INSERT INTO tabfree_lake.orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sample_rows()
    )
    con.execute("CREATE OR REPLACE TABLE tabfree_lake.people (region VARCHAR, regional_manager VARCHAR)")
    con.executemany("INSERT INTO tabfree_lake.people VALUES (?, ?)", list(MANAGERS.items()))


# ---- Engine -----------------------------------------------------------------------------------


class PreviewEngine:
    """Loads the example bundles and models and runs bound queries on an in-memory DuckDB."""

    def __init__(self, paths: PreviewPaths, today: dt.date | None = None) -> None:
        import duckdb  # optional dependency: tabfree-api[preview]

        self.paths = paths
        self.today = today or dt.date(2024, 12, 31)  # the sample data ends in 2024
        self._con = duckdb.connect()
        self._lock = threading.Lock()  # one DuckDB connection, shared by request threads
        load_sample_data(self._con)
        self.models: dict[str, Mapping[str, Any]] = {}
        from tabfree_schemas import load_document

        for path in sorted(paths.models.glob("*.model.yaml")):
            doc = load_document(path)
            self.models[str(doc["id"])] = doc

    def dashboards(self) -> list[str]:
        return sorted(p.parent.name for p in self.paths.dashboards.glob("*/tabfree.json"))

    def bundle(self, dashboard: str) -> tuple[Mapping[str, Any], str]:
        if dashboard not in self.dashboards():
            raise KeyError(dashboard)
        folder = self.paths.dashboards / dashboard
        manifest = json.loads((folder / "tabfree.json").read_text(encoding="utf-8"))
        return manifest, (folder / "index.html").read_text(encoding="utf-8")

    def session(self, user: str) -> Session:
        info = USERS.get(user)
        if info is None:
            raise CompileError(f"unknown preview user {user!r}")
        return Session(
            username=user, fullname=info["fullname"], groups=frozenset(info["groups"]), today=self.today
        )

    def run(self, bound: BoundQuery, user: str) -> dict[str, Any]:
        doc = self.models.get(bound.model)
        if doc is None:
            raise CompileError(f"unknown model {bound.model!r}")
        model = SemanticModel.from_dict(doc)
        query = dict(bound.query)
        query["limit"] = min(int(query.get("limit", ROW_LIMIT)), ROW_LIMIT) + 1
        compiled = compile_query(
            model,
            query,
            dialect="duckdb",
            session=self.session(user),
            parameters=bound.parameters,
            filters=bound.filters,
        )
        with self._lock:
            rows = self._con.execute(compiled.sql, list(compiled.parameters)).fetchall()
        truncated = len(rows) > ROW_LIMIT
        formats = _formats(doc)
        columns = []
        for c in compiled.columns:
            column: dict[str, Any] = {
                "name": c.name,
                "type": c.type if c.type in _COLUMN_TYPES else "string",
                "role": c.role,
            }
            if c.name in formats:
                column["format"] = formats[c.name]
            columns.append(column)
        return {
            "columns": columns,
            "rows": [[_json(v) for v in r] for r in rows[:ROW_LIMIT]],
            "truncated": truncated,
        }


def _formats(doc: Mapping[str, Any]) -> dict[str, str]:
    return {f["name"]: f["format"] for f in doc.get("fields", []) if f.get("format")}


def _json(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value != value:  # NaN
        return None
    if hasattr(value, "as_integer_ratio") and not isinstance(value, int | float):  # Decimal
        return float(value)
    return value


# ---- HTTP -------------------------------------------------------------------------------------


class QueryBody(BaseModel):
    dashboard: str = Field(max_length=100)
    query: str = Field(max_length=64)
    state: dict[str, Any]
    selections: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    user: str = Field(max_length=64)


class DomainBody(BaseModel):
    dashboard: str = Field(max_length=100)
    filter: str = Field(max_length=64)
    state: dict[str, Any]
    user: str = Field(max_length=64)


def create_router(engine: PreviewEngine) -> APIRouter:
    router = APIRouter(prefix="/preview", tags=["preview"])

    def manifest_of(dashboard: str) -> Mapping[str, Any]:
        try:
            return engine.bundle(dashboard)[0]
        except KeyError:
            raise HTTPException(404, f"no dashboard {dashboard!r}") from None

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    def page() -> HTMLResponse:
        return HTMLResponse(
            _PAGE.replace("__USERS__", json.dumps({k: v["fullname"] for k, v in USERS.items()}))
        )

    @router.get("/assets/{name}", include_in_schema=False)
    def asset(name: str) -> FileResponse:
        if name not in ("host.js", "frame.js"):
            raise HTTPException(404)
        path = engine.paths.runtime / name
        if not path.is_file():
            raise HTTPException(503, "build the runtime first: npm run build -w @tabfree/runtime")
        return FileResponse(path, media_type="text/javascript")

    @router.get("/api/dashboards")
    def dashboards() -> list[str]:
        return engine.dashboards()

    @router.get("/api/dashboards/{dashboard}")
    def bundle(dashboard: str) -> dict[str, Any]:
        try:
            manifest, html = engine.bundle(dashboard)
        except KeyError:
            raise HTTPException(404, f"no dashboard {dashboard!r}") from None
        return {"manifest": manifest, "html": html}

    @router.post("/api/query")
    def query(body: QueryBody) -> dict[str, Any]:
        try:
            bound = bind_query(manifest_of(body.dashboard), body.query, body.state, body.selections)
            return engine.run(bound, body.user)
        except CompileError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/domain")
    def domain(body: DomainBody) -> dict[str, Any]:
        try:
            bound = bind_domain(manifest_of(body.dashboard), body.filter, body.state)
            return engine.run(bound, body.user)
        except CompileError as exc:
            raise HTTPException(400, str(exc)) from None

    return router


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TabFree preview</title>
<style>
  :root { color-scheme: light dark; font: 14px/1.4 system-ui, sans-serif; }
  body { margin: 0; background: Canvas; color: CanvasText; }
  header { display: flex; gap: 16px; align-items: center; padding: 10px 16px;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }
  header h1 { font-size: 15px; margin: 0 auto 0 0; }
  label { display: flex; gap: 6px; align-items: center; }
  #frame { padding: 8px; }
  #log { font: 12px ui-monospace, monospace; padding: 0 16px 16px; opacity: .7; white-space: pre-wrap; }
</style>
</head>
<body>
<header>
  <h1>TabFree preview <small>(local sample data, no sign-in)</small></h1>
  <label>Dashboard <select id="dashboard"></select></label>
  <label>View as <select id="user"></select></label>
  <label>Theme <select id="theme"><option>light</option><option>dark</option></select></label>
</header>
<div id="frame"></div>
<div id="log"></div>
<script type="module">
  import { mountDashboard, HostQueryError } from "/preview/assets/host.js";
  const users = __USERS__;
  const $ = (id) => document.getElementById(id);
  for (const [id, name] of Object.entries(users)) $("user").append(new Option(name, id));
  const runtime = await (await fetch("/preview/assets/frame.js")).text();
  const names = await (await fetch("/preview/api/dashboards")).json();
  for (const n of names) $("dashboard").append(new Option(n, n));
  if (matchMedia("(prefers-color-scheme: dark)").matches) $("theme").value = "dark";
  let host = null;
  const log = (line) => { $("log").textContent = line; };
  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...body, user: $("user").value }) });
    const json = await response.json();
    if (!response.ok) throw new HostQueryError("query_failed", json.detail ?? "query failed");
    return json;
  }
  async function mount() {
    const state = host?.state;
    host?.destroy();
    const { manifest, html } = await (await fetch(`/preview/api/dashboards/${$("dashboard").value}`)).json();
    host = mountDashboard({
      container: $("frame"), manifest, html, runtime,
      theme: { mode: $("theme").value },
      initialState: state && state.filters ? state : undefined,
      runQuery: (r) => post("/preview/api/query", r),
      runDomain: (r) => post("/preview/api/domain", r),
      onStateChange: (s, sel) =>
        log(JSON.stringify({ filters: s.filters, parameters: s.parameters, selections: sel })),
      onDashboardError: (m) => log(`dashboard error: ${m}`),
    });
  }
  for (const id of ["dashboard", "user", "theme"]) $(id).addEventListener("change", mount);
  await mount();
</script>
</body>
</html>
"""
