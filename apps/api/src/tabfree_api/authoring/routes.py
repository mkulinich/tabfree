"""Portal API for drafts and "Edit with Claude" (P4.3): the same operations as the MCP tools.

No ``from __future__ import annotations``: FastAPI evaluates the ``Depends`` annotations.
"""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from tabfree_api.auth import SessionInfo
from tabfree_api.authoring.files import MAX_EDITS
from tabfree_api.authoring.service import AuthoringError, AuthoringService


class EditBody(BaseModel):
    edits: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_EDITS)


class DraftQueryBody(BaseModel):
    query: str = Field(max_length=64)
    state: dict[str, Any] = Field(default_factory=dict)
    selections: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class DraftDomainBody(BaseModel):
    filter: str = Field(max_length=64)
    state: dict[str, Any] = Field(default_factory=dict)


class PublishRequestBody(BaseModel):
    message: str = Field(default="", max_length=500)


class ConfirmBody(BaseModel):
    confirmation: str = Field(min_length=4, max_length=32)


class NewDashboardBody(BaseModel):
    dashboard: str = Field(min_length=1, max_length=100)
    project: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=100)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    #: "auto" picks the quick loop for small edits and the sandbox for builds (when one is configured).
    mode: Literal["auto", "quick", "sandbox"] = "auto"


def add_routes(router: APIRouter, ctx: Any, current: Any, csrf: Any) -> None:
    service = AuthoringService(ctx)
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806

    def run(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except AuthoringError as exc:
            raise HTTPException(exc.status, exc.message) from None

    @router.get("/api/drafts")
    def drafts(info: Me) -> list[dict[str, Any]]:
        return list(run(service.list_drafts, info))

    @router.post("/api/dashboards/{dashboard}/drafts", status_code=201)
    def start(dashboard: str, info: Me, _: Csrf) -> dict[str, Any]:
        """The caller's open draft of the dashboard, or a new one (needs Web Edit)."""
        mine = list(run(service.drafts).list(info.username, dashboard=dashboard))
        draft = mine[0] if mine else run(service.start_draft, info, dashboard)
        return dict(draft.to_dict(with_files=True))

    @router.post("/api/drafts", status_code=201)
    def new_dashboard(body: NewDashboardBody, info: Me, _: Csrf) -> dict[str, Any]:
        draft = run(
            service.create_dashboard,
            info,
            body.dashboard,
            project=body.project,
            title=body.title,
            model=body.model,
        )
        return dict(draft.to_dict(with_files=True))

    @router.get("/api/drafts/{draft_id}")
    def get_draft(draft_id: str, info: Me) -> dict[str, Any]:
        out = run(service.get_dashboard, info, None, draft_id)
        files = out["files"]
        manifest: Any
        try:
            manifest = json.loads(files.get("tabfree.json", ""))
        except ValueError:
            manifest = None
        return {**out["draft"], "files": files, "manifest": manifest, "html": files.get("index.html", "")}

    @router.get("/api/drafts/{draft_id}/bundle")
    def draft_bundle(draft_id: str, info: Me) -> dict[str, Any]:
        """What the live preview mounts: the manifest, and the HTML or a signed usercontent URL."""
        from tabfree_api.authoring.files import EditError, parse_manifest
        from tabfree_api.usercontent import DRAFT_PREFIX

        out = run(service.get_dashboard, info, None, draft_id)
        try:
            manifest = parse_manifest(out["files"])
        except EditError as exc:
            raise HTTPException(422, str(exc)) from None
        if ctx.frame_signer is not None and ctx.settings.usercontent_origin:
            url = ctx.frame_signer.url(ctx.settings.usercontent_origin, ctx.site.id, DRAFT_PREFIX + draft_id)
            return {"manifest": manifest, "frameUrl": url}
        return {"manifest": manifest, "html": out["files"].get("index.html", "")}

    @router.post("/api/drafts/{draft_id}/edit")
    def edit(draft_id: str, body: EditBody, info: Me, _: Csrf) -> dict[str, Any]:
        draft, report = run(service.edit, info, body.edits, draft=draft_id)
        return {"draft": draft.to_dict(), "validation": report.to_dict()}

    @router.get("/api/drafts/{draft_id}/diff")
    def diff(draft_id: str, info: Me) -> dict[str, str]:
        return {"diff": run(service.diff, info, draft=draft_id)}

    @router.post("/api/drafts/{draft_id}/validate")
    def validate(draft_id: str, info: Me, _: Csrf) -> dict[str, Any]:
        return dict(run(service.validate, info, draft_id).to_dict())

    @router.post("/api/drafts/{draft_id}/query")
    def query(draft_id: str, body: DraftQueryBody, info: Me, _: Csrf) -> dict[str, Any]:
        return dict(run(service.draft_query, info, draft_id, body.query, body.state, body.selections))

    @router.post("/api/drafts/{draft_id}/domain")
    def domain(draft_id: str, body: DraftDomainBody, info: Me, _: Csrf) -> dict[str, Any]:
        return dict(run(service.draft_domain, info, draft_id, body.filter, body.state))

    @router.post("/api/drafts/{draft_id}/publish-request")
    def publish_request(draft_id: str, body: PublishRequestBody, info: Me, _: Csrf) -> dict[str, Any]:
        request = run(service.request_publish, info, draft_id, body.message)
        return {
            "confirmation": request.code,
            "expires": request.expires.isoformat(),
            "validation": request.report.to_dict(),
            "diff": request.diff,
        }

    @router.post("/api/drafts/{draft_id}/publish")
    def publish(draft_id: str, body: ConfirmBody, info: Me, _: Csrf) -> dict[str, int]:
        return {"revision": run(service.confirm_publish, info, draft_id, body.confirmation)}

    @router.post("/api/drafts/{draft_id}/approve", status_code=204)
    def approve(draft_id: str, info: Me, _: Csrf) -> Response:
        run(service.approve, info, draft_id)
        return Response(status_code=204)

    @router.delete("/api/drafts/{draft_id}", status_code=204)
    def discard(draft_id: str, info: Me, _: Csrf) -> Response:
        run(service.discard, info, draft_id)
        return Response(status_code=204)

    # ---- Edit with Claude (P4.3, P4.4) -------------------------------------------------------------

    def author() -> Any:
        if ctx.author is None:
            raise HTTPException(503, "Edit with Claude is switched off on this server (TABFREE_AI_PROVIDER)")
        return ctx.author

    @router.post("/api/drafts/{draft_id}/chat", status_code=202)
    def chat(draft_id: str, body: ChatBody, info: Me, _: Csrf) -> dict[str, Any]:
        """Ask Claude to change the draft; progress streams from the session's events."""
        run(service._open, info, draft_id)
        return dict(run(author().start, info, draft_id, body.message, body.mode))

    @router.get("/api/agent-sessions/{session_id}")
    def agent_session(session_id: str, info: Me, after: int = 0) -> dict[str, Any]:
        return dict(run(author().get, info, session_id, after))

    @router.get("/api/agent-sessions/{session_id}/events")
    def agent_events(session_id: str, info: Me) -> StreamingResponse:
        """Server-sent events: each progress event as it happens, then ``done``."""
        runner = author()
        run(runner.get, info, session_id, 0)  # permission check before streaming
        return StreamingResponse(
            runner.stream(info, session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/agent-sessions/{session_id}/report", status_code=204)
    def report(session_id: str, body: dict[str, Any], info: Me) -> Response:
        """Progress from a sandbox (bearer: the session's on-behalf-of token, so no CSRF header)."""
        run(author().report, info, session_id, body)
        return Response(status_code=204)

    @router.post("/api/agent-sessions/{session_id}/cancel", status_code=204)
    def cancel(session_id: str, info: Me, _: Csrf) -> Response:
        run(author().cancel, info, session_id)
        return Response(status_code=204)
