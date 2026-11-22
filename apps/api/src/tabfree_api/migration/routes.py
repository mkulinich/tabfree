"""The migration console API (BUILD_PLAN P5.6). Site administrators only; needs the database.

No ``from __future__ import annotations``: FastAPI evaluates the ``Depends`` annotations.
"""

import threading
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import SiteRole
from tabfree_api.migration import verify
from tabfree_api.migration.importer import approve
from tabfree_api.migration.store import MigrationError, MigrationStore

RECORD_KINDS = ("job", "alert", "custom_view", "decision", "verification", "governance_report")


class ActionBody(BaseModel):
    action: Literal["approve", "request_changes", "rebuild", "retire", "reopen"]
    note: str = Field(default="", max_length=4000)


class RepairBody(BaseModel):
    instructions: str = Field(default="", max_length=4000)


def add_routes(router: APIRouter, ctx: Any, current: Any, csrf: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806
    running: set[str] = set()
    lock = threading.Lock()

    def store(info: SessionInfo) -> MigrationStore:
        if info.site_role is not SiteRole.SITE_ADMINISTRATOR:
            raise HTTPException(403, "the migration console is for site administrators")
        if not hasattr(ctx.site, "engine"):
            raise HTTPException(404, "the migration console needs the database (TABFREE_DATABASE_URL)")
        return MigrationStore(ctx.site.engine, ctx.site.id)

    def run(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except MigrationError as exc:
            raise HTTPException(exc.status, exc.message) from None

    def item_or_404(s: MigrationStore, item_id: str) -> dict[str, Any]:
        item = s.get(item_id)
        if item is None:
            raise HTTPException(404, f"no migration item {item_id!r}")
        return item

    def start_repair(info: SessionInfo, item_id: str, instructions: str) -> None:
        from tabfree_api.migration.repair import RepairAgent

        agent = run(RepairAgent, ctx)
        with lock:
            if item_id in running:
                raise HTTPException(409, f"{item_id!r} is being repaired")
            running.add(item_id)

        def work() -> None:
            try:
                agent.run(item_id, info, instructions=instructions or None)
            finally:
                with lock:
                    running.discard(item_id)

        threading.Thread(target=work, name=f"repair-{item_id}", daemon=True).start()

    @router.get("/api/migration")
    def summary(info: Me) -> dict[str, Any]:
        s = store(info)
        out = s.summary()
        out["decisions"] = len(s.records("decision"))
        out["records"] = {kind: len(s.records(kind)) for kind in ("job", "alert", "custom_view")}
        access = next(iter(s.records("verification", ["access"])), None)
        out["access"] = access["data"] if access else None
        out["repairAvailable"] = ctx.provider is not None
        out["renderAvailable"] = getattr(ctx, "renderer", None) is not None
        return out

    @router.get("/api/migration/items")
    def items(info: Me, wave: int | None = None, status: str | None = None) -> list[dict[str, Any]]:
        return store(info).list_items(wave=wave, status=status)

    @router.get("/api/migration/items/{item_id}")
    def item(item_id: str, info: Me) -> dict[str, Any]:
        s = store(info)
        out = item_or_404(s, item_id)
        out["events"] = s.events(item_id, limit=100)
        out["verification"] = {
            r["key"].split(":", 1)[0]: r["data"]
            for r in s.records("verification")
            if r["key"].endswith(f":{item_id}")
        }
        out["running"] = item_id in running
        report = dict(out.get("report") or {})
        report.pop("source", None)  # the IR excerpt is for the agent, not the console
        out["report"] = report
        return out

    @router.get("/api/migration/items/{item_id}/{which}.png")
    def image(item_id: str, which: Literal["reference", "render"], info: Me) -> Response:
        data = store(info).image(item_id, f"{which}_png")
        if data is None:
            raise HTTPException(404, f"no {which} image")
        return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @router.post("/api/migration/items/{item_id}/actions")
    def act(item_id: str, body: ActionBody, info: Me, _: Csrf) -> dict[str, Any]:
        s = store(info)
        item_or_404(s, item_id)
        if body.action == "approve":
            revision = run(approve, ctx.site.engine, ctx.site.id, item_id, by=info.username, note=body.note)
            ctx.site.invalidate()
            ctx.audit(
                "migration_approve",
                info.username,
                resource_kind="dashboard",
                resource_id=item_id,
                revision=revision,
            )
            return item_or_404(s, item_id)
        out = run(s.act, item_id, body.action, by=info.username, note=body.note)
        ctx.audit(f"migration_{body.action}", info.username, resource_kind="dashboard", resource_id=item_id)
        if body.action == "request_changes" and ctx.provider is not None:
            # The reviewer's instructions go to the repair agent.
            start_repair(info, item_id, body.note)
        return dict(out)

    @router.post("/api/migration/items/{item_id}/repair", status_code=202)
    def repair(item_id: str, body: RepairBody, info: Me, _: Csrf) -> dict[str, str]:
        s = store(info)
        item_or_404(s, item_id)
        start_repair(info, item_id, body.instructions)
        ctx.audit("migration_repair", info.username, resource_kind="dashboard", resource_id=item_id)
        return {"status": "started"}

    @router.post("/api/migration/items/{item_id}/verify")
    def verify_item(item_id: str, info: Me, _: Csrf) -> dict[str, Any]:
        s = store(info)
        item_or_404(s, item_id)
        data = verify.data_parity(ctx, s, item_id, info)
        visual = verify.visual_parity(ctx, s, item_id, info)
        fidelity = verify.refresh_fidelity(
            ctx,
            s,
            item_id,
            info,
            data=data if data.get("status") == "done" else None,
            visual=visual if visual.get("status") == "done" else None,
        )
        s.event(
            item_id, "verify", by=info.username, data={"score": fidelity["score"], "grade": fidelity["grade"]}
        )
        return {"data": data, "visual": visual, "fidelity": fidelity}

    @router.post("/api/migration/access-parity")
    def access(info: Me, _: Csrf) -> dict[str, Any]:
        s = store(info)
        return verify.access_parity(ctx.site, s)

    @router.get("/api/migration/records/{kind}")
    def records(kind: str, info: Me) -> list[dict[str, Any]]:
        if kind not in RECORD_KINDS:
            raise HTTPException(404, f"no record kind {kind!r}")
        return store(info).records(kind)
