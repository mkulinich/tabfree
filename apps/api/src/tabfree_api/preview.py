"""Local dashboard preview: the example dashboards over generated sample data, on DuckDB.

Development only. It is mounted when ``TABFREE_PREVIEW=1``, and it has **no authentication**: the
viewer picks a sample user in the page so row-level security can be tried out. The signed-in portal
(``/``, P1.13) uses the same query service with real sessions and permissions.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from tabfree_api.content import ContentError, SiteContent
from tabfree_api.engines import EngineError
from tabfree_api.service import QueryService
from tabfree_compiler import CompileError, Session

#: Sample viewers. Managers see their own region (the model's RLS policy); executives see all.
USERS: dict[str, dict[str, Any]] = {
    "erin": {"fullname": "Erin (executive)", "groups": ["Executives"]},
    "alice": {"fullname": "Alice (East manager)", "groups": ["Managers"]},
    "bob": {"fullname": "Bob (West manager)", "groups": ["Managers"]},
    "carol": {"fullname": "Carol (Central manager)", "groups": ["Managers"]},
    "dave": {"fullname": "Dave (South manager)", "groups": ["Managers"]},
}


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


def create_router(
    site: SiteContent, service: QueryService, runtime_dir: Any, today: dt.date | None
) -> APIRouter:
    router = APIRouter(prefix="/preview", tags=["preview"])

    def manifest_of(dashboard: str) -> Mapping[str, Any]:
        try:
            return site.bundle(dashboard).manifest
        except ContentError:
            raise HTTPException(404, f"no dashboard {dashboard!r}") from None

    def session(user: str) -> Session:
        info = USERS.get(user)
        if info is None:
            raise HTTPException(400, f"unknown preview user {user!r}")
        return Session(
            username=user, fullname=info["fullname"], groups=frozenset(info["groups"]), today=today
        )

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    def page() -> HTMLResponse:
        return HTMLResponse(
            _PAGE.replace("__USERS__", json.dumps({k: v["fullname"] for k, v in USERS.items()}))
        )

    @router.get("/assets/{name}", include_in_schema=False)
    def asset(name: str) -> FileResponse:
        if name not in ("host.js", "frame.js"):
            raise HTTPException(404)
        path = runtime_dir / name
        if not path.is_file():
            raise HTTPException(503, "build the runtime first: npm run build -w @tabfree/runtime")
        return FileResponse(path, media_type="text/javascript")

    @router.get("/api/dashboards")
    def dashboards() -> list[str]:
        return sorted(site.dashboards)

    @router.get("/api/dashboards/{dashboard}")
    def bundle(dashboard: str) -> dict[str, Any]:
        try:
            b = site.bundle(dashboard)
        except ContentError:
            raise HTTPException(404, f"no dashboard {dashboard!r}") from None
        return {"manifest": b.manifest, "html": b.html}

    @router.post("/api/query")
    def query(body: QueryBody) -> dict[str, Any]:
        try:
            return service.query(
                manifest_of(body.dashboard), body.query, body.state, body.selections, session(body.user)
            )
        except (CompileError, EngineError) as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/domain")
    def domain(body: DomainBody) -> dict[str, Any]:
        try:
            return service.domain(manifest_of(body.dashboard), body.filter, body.state, session(body.user))
        except (CompileError, EngineError) as exc:
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
