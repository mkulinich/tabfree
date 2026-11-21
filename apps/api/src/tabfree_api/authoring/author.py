"""The Author service (BUILD_PLAN P4.3): runs "Edit with Claude" sessions and records their progress.

A session belongs to one user and one draft. It runs in one of two ways:

- **quick** (P4.4): the in-process tool loop in a worker thread. No sandbox, so it starts at once.
- **sandbox**: one ECS Fargate task per session running the Claude Agent SDK (ADR-008). The task gets
  the draft id, the request and a short-lived on-behalf-of token for the MCP server, and nothing else:
  its security group lets it reach only the model endpoint and TabFree. It reports progress to
  ``/api/agent-sessions/{id}/report`` with the same token. ``local`` runs the same agent as a
  subprocess, for development.

"auto" picks the sandbox when one is configured and the request reads like a build (several tiles,
a new page), and the quick loop otherwise.

Progress events are stored on the session row, so any API task can stream them to the chat panel.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any

from sqlalchemy import Engine, insert, select, update

from tabfree_api.auth import SessionInfo
from tabfree_api.authoring.budget import BudgetError
from tabfree_api.authoring.service import AuthoringError, AuthoringService
from tabfree_api.db import schema as t

MAX_EVENTS = 500
POLL_S = 0.5
#: Words that suggest a larger build, for "auto" mode.
_BUILD = re.compile(
    r"\b(new (page|dashboard|tab)|build|several|multiple|tiles|redesign|from scratch)\b", re.I
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class AgentSessions:
    def __init__(self, engine: Engine, site_id: str) -> None:
        self.engine = engine
        self.site_id = site_id
        self._lock = threading.Lock()

    def create(self, username: str, draft_id: str, mode: str, model: str) -> str:
        session_id = "as_" + secrets.token_hex(12)
        when = _now()
        with self.engine.begin() as conn:
            uid = conn.execute(select(t.users.c.id).where(t.users.c.username == username)).scalar_one()
            conn.execute(
                insert(t.agent_sessions).values(
                    id=session_id,
                    site_id=self.site_id,
                    user_id=uid,
                    draft_id=draft_id,
                    mode=mode,
                    model=model,
                    status="running",
                    events=[],
                    transcript=[],
                    created_at=when,
                    updated_at=when,
                )
            )
        return session_id

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(t.agent_sessions, t.users.c.username)
                    .join(t.users, t.users.c.id == t.agent_sessions.c.user_id)
                    .where(
                        t.agent_sessions.c.id == session_id[:64], t.agent_sessions.c.site_id == self.site_id
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def latest(self, username: str, draft_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(t.agent_sessions.c.id)
                .join(t.users, t.users.c.id == t.agent_sessions.c.user_id)
                .where(t.users.c.username == username, t.agent_sessions.c.draft_id == draft_id)
                .order_by(t.agent_sessions.c.created_at.desc())
            ).first()
        return self.get(row[0]) if row else None

    def add_event(self, session_id: str, event: Mapping[str, Any]) -> None:
        entry = {**event, "at": _now().isoformat()}
        with self._lock, self.engine.begin() as conn:
            events = conn.execute(
                select(t.agent_sessions.c.events).where(t.agent_sessions.c.id == session_id)
            ).scalar()
            events = [*(events or []), entry][-MAX_EVENTS:]
            conn.execute(
                update(t.agent_sessions)
                .where(t.agent_sessions.c.id == session_id)
                .values(events=events, updated_at=_now())
            )

    def finish(
        self,
        session_id: str,
        status: str,
        *,
        transcript: Any = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": _now()}
        if transcript is not None:
            values["transcript"] = transcript
        if input_tokens or output_tokens:
            values["input_tokens"] = t.agent_sessions.c.input_tokens + input_tokens
            values["output_tokens"] = t.agent_sessions.c.output_tokens + output_tokens
        with self.engine.begin() as conn:
            conn.execute(
                update(t.agent_sessions)
                .where(t.agent_sessions.c.id == session_id, t.agent_sessions.c.status == "running")
                .values(values)
            )

    def resume(self, session_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.agent_sessions)
                .where(t.agent_sessions.c.id == session_id)
                .values(status="running", updated_at=_now())
            )


class AuthorRunner:
    def __init__(self, ctx: Any, *, sandbox: Any = None) -> None:
        self.ctx = ctx
        self.service = AuthoringService(ctx)
        site = ctx.site
        if not hasattr(site, "engine"):
            raise RuntimeError("Edit with Claude needs the metadata database (TABFREE_DATABASE_URL)")
        self.sessions = AgentSessions(site.engine, site.id)
        self.sandbox = sandbox
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: set[str] = set()

    # ---- sessions -----------------------------------------------------------------------------------

    def _mine(self, info: SessionInfo, session_id: str) -> dict[str, Any]:
        row = self.sessions.get(session_id)
        if row is None or row["username"] != info.username:
            raise AuthoringError(404, f"no agent session {session_id!r}")
        return row

    def start(self, info: SessionInfo, draft_id: str, message: str, mode: str = "auto") -> dict[str, Any]:
        if info.agent_session:
            raise AuthoringError(403, "an agent cannot start another agent")
        draft = self.service._open(info, draft_id)
        provider = self.ctx.provider
        if provider is None:
            raise AuthoringError(503, "Edit with Claude is switched off on this server")
        if self.ctx.budget is not None:
            try:
                self.ctx.budget.check(info.username)
            except BudgetError as exc:
                raise AuthoringError(429, exc.message) from None
        if mode == "auto":
            mode = "sandbox" if self.sandbox is not None and _BUILD.search(message) else "quick"
        if mode == "sandbox" and self.sandbox is None:
            raise AuthoringError(503, "no sandbox is configured (TABFREE_AUTHOR_BACKEND); use a quick edit")
        task = "quick" if mode == "quick" else "build"
        previous = self.sessions.latest(info.username, draft.id)
        follow_up = (
            previous is not None and previous["mode"] == "quick" == mode and previous["status"] != "running"
        )
        if follow_up and previous is not None:
            session_id = previous["id"]
            self.sessions.resume(session_id)
        else:
            session_id = self.sessions.create(info.username, draft.id, mode, provider.model(task))
        self.sessions.add_event(session_id, {"type": "user", "text": message})
        self.service.audit(
            info,
            "agent_start",
            resource_kind="dashboard",
            resource_id=draft.content_id,
            draft=draft.id,
            agent_session=session_id,
            mode=mode,
        )
        if mode == "quick":
            history = list(previous["transcript"]) if follow_up and previous is not None else []
            thread = threading.Thread(
                target=self._run_quick,
                args=(info, session_id, draft.id, message, history),
                daemon=True,
                name=f"tabfree-quick-{session_id}",
            )
            self._threads[session_id] = thread
            thread.start()
        else:
            self._run_sandbox(info, session_id, draft.id, message, provider.model(task))
        return {"id": session_id, "mode": mode, "model": provider.model(task)}

    def _run_quick(
        self, info: SessionInfo, session_id: str, draft_id: str, message: str, history: list[dict[str, Any]]
    ) -> None:
        from tabfree_api.authoring.quick import QuickEditor

        settings = self.ctx.settings
        editor = QuickEditor(
            self.service,
            self.ctx.provider,
            self.ctx.budget,
            max_turns=settings.ai_quick_max_turns,
            timeout_s=settings.ai_quick_timeout_s,
        )
        try:
            result = editor.run(
                info,
                session_id,
                draft_id,
                message,
                history=history,
                emit=lambda e: self.sessions.add_event(session_id, e),
                cancelled=lambda: session_id in self._cancel,
            )
        except Exception as exc:
            self.sessions.add_event(
                session_id, {"type": "error", "text": f"Claude could not finish: {type(exc).__name__}"}
            )
            self.sessions.finish(session_id, "failed")
            self.ctx.audit(
                "agent_finish", info.username, "error", agent_session=session_id, error=type(exc).__name__
            )
            return
        self.sessions.finish(
            session_id,
            result.status,
            transcript=result.transcript,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        self.sessions.add_event(
            session_id, {"type": "done", "text": result.text or "Done.", "status": result.status}
        )
        self.ctx.audit(
            "agent_finish",
            info.username,
            agent_session=session_id,
            status=result.status,
            turns=result.turns,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def _run_sandbox(
        self, info: SessionInfo, session_id: str, draft_id: str, message: str, model: str
    ) -> None:
        settings = self.ctx.settings
        if self.ctx.oauth is None:
            raise AuthoringError(503, "sandboxes need the OAuth store (the metadata database)")
        mcp_url = settings.author_mcp_url or settings.base_url.rstrip("/") + "/mcp"
        from tabfree_api.authoring.oauth import mcp_resource

        token = self.ctx.oauth.mint_agent_token(info.username, session_id, mcp_resource(settings), ttl_s=1800)
        env = {
            "TABFREE_MCP_URL": mcp_url,
            "TABFREE_REPORT_URL": settings.base_url.rstrip("/") + f"/api/agent-sessions/{session_id}/report",
            "TABFREE_AGENT_TOKEN": token,
            "TABFREE_AGENT_SESSION": session_id,
            "TABFREE_DRAFT": draft_id,
            "TABFREE_REQUEST": message,
            "TABFREE_MODEL": model,
            "TABFREE_AI_PROVIDER": settings.ai_provider,
        }
        if settings.ai_bedrock_region:
            env["AWS_REGION"] = settings.ai_bedrock_region
        try:
            handle = self.sandbox.launch(session_id, env)
        except Exception as exc:
            self.ctx.oauth.revoke_agent_session(session_id)
            self.sessions.finish(session_id, "failed")
            raise AuthoringError(502, f"the sandbox did not start: {type(exc).__name__}") from None
        self.sessions.add_event(
            session_id, {"type": "status", "text": "Starting a sandbox…", "task": str(handle)}
        )

    def report(self, info: SessionInfo, session_id: str, body: Mapping[str, Any]) -> None:
        """Progress from a sandbox, sent with its own on-behalf-of token."""
        if info.agent_session != session_id:
            raise AuthoringError(403, "this token does not belong to that session")
        row = self.sessions.get(session_id)
        if row is None or row["username"] != info.username:
            raise AuthoringError(404, "no such session")
        for event in list(body.get("events") or [])[:50]:
            if isinstance(event, Mapping) and event.get("type") in (
                "assistant",
                "tool",
                "status",
                "error",
                "draft",
            ):
                self.sessions.add_event(
                    session_id, {"type": event["type"], "text": str(event.get("text", ""))[:4000]}
                )
        usage = body.get("usage") or {}
        tokens_in, tokens_out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        if (tokens_in or tokens_out) and self.ctx.budget is not None:
            self.ctx.budget.record(info.username, tokens_in, tokens_out)
        status = body.get("status")
        if status in ("done", "failed"):
            self.sessions.finish(session_id, str(status), input_tokens=tokens_in, output_tokens=tokens_out)
            self.sessions.add_event(
                session_id, {"type": "done", "text": str(body.get("text") or "Done."), "status": status}
            )
            self.ctx.oauth.revoke_agent_session(session_id)
            self.ctx.audit(
                "agent_finish",
                info.username,
                agent_session=session_id,
                status=status,
                cost_usd=body.get("cost_usd"),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
            )

    def get(self, info: SessionInfo, session_id: str, after: int = 0) -> dict[str, Any]:
        row = self._mine(info, session_id)
        events = list(row["events"] or [])
        return {
            "id": row["id"],
            "draft": row["draft_id"],
            "mode": row["mode"],
            "model": row["model"],
            "status": row["status"],
            "events": events[after:],
            "next": len(events),
            "tokens": {"input": row["input_tokens"], "output": row["output_tokens"]},
        }

    def stream(
        self, info: SessionInfo, session_id: str, *, poll_s: float = POLL_S, limit_s: float = 900.0
    ) -> Iterator[str]:
        sent = 0
        started = time.monotonic()
        while True:
            state = self.get(info, session_id, sent)
            for event in state["events"]:
                yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"
            sent = state["next"]
            if state["status"] != "running" or time.monotonic() - started > limit_s:
                yield f"event: end\ndata: {json.dumps({'status': state['status']})}\n\n"
                return
            time.sleep(poll_s)

    def cancel(self, info: SessionInfo, session_id: str) -> None:
        row = self._mine(info, session_id)
        self._cancel.add(session_id)
        if row["mode"] == "sandbox" and self.sandbox is not None:
            self.sandbox.stop(session_id)
            if self.ctx.oauth is not None:
                self.ctx.oauth.revoke_agent_session(session_id)
        self.sessions.finish(session_id, "cancelled")
        self.sessions.add_event(session_id, {"type": "done", "text": "Cancelled.", "status": "cancelled"})

    def wait(self, session_id: str, timeout: float = 30.0) -> None:
        """Wait for a quick session's thread (tests and the CLI)."""
        thread = self._threads.get(session_id)
        if thread is not None:
            thread.join(timeout)


# ---- sandbox backends ------------------------------------------------------------------------------


class FargateSandbox:
    """One ECS Fargate task per session (ADR-008). Not exercised without AWS (P7 security tests)."""

    def __init__(
        self,
        cluster: str,
        task_definition: str,
        subnets: list[str],
        security_groups: list[str],
        region: str | None = None,
        client: Any = None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("ecs", region_name=region)
        self.ecs = client
        self.cluster, self.task_definition = cluster, task_definition
        self.subnets, self.security_groups = subnets, security_groups
        self.tasks: dict[str, str] = {}

    def launch(self, session_id: str, env: Mapping[str, str]) -> str:
        response = self.ecs.run_task(
            cluster=self.cluster,
            taskDefinition=self.task_definition,
            launchType="FARGATE",
            count=1,
            startedBy=session_id[:36],
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self.subnets,
                    "securityGroups": self.security_groups,
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {"name": "author-agent", "environment": [{"name": k, "value": v} for k, v in env.items()]}
                ]
            },
            tags=[{"key": "tabfree:agent-session", "value": session_id}],
        )
        failures = response.get("failures") or []
        if failures or not response.get("tasks"):
            raise RuntimeError(f"run_task failed: {failures[:1]}")
        arn = str(response["tasks"][0]["taskArn"])
        self.tasks[session_id] = arn
        return arn

    def stop(self, session_id: str) -> None:
        arn = self.tasks.pop(session_id, None)
        if arn:
            self.ecs.stop_task(cluster=self.cluster, task=arn, reason="cancelled by the user")


class LocalSandbox:
    """The author agent as a subprocess (development only: no isolation)."""

    def __init__(self, command: list[str] | None = None) -> None:
        self.command = command or [sys.executable, "-m", "tabfree_author_agent"]
        self.processes: dict[str, subprocess.Popen[bytes]] = {}

    def launch(self, session_id: str, env: Mapping[str, str]) -> str:
        process = subprocess.Popen(self.command, env={**os.environ, **env})  # noqa: S603 - a fixed command
        self.processes[session_id] = process
        return f"pid {process.pid}"

    def stop(self, session_id: str) -> None:
        process = self.processes.pop(session_id, None)
        if process is not None:
            process.terminate()


def sandbox_from_settings(settings: Any) -> Any:
    if settings.author_backend == "fargate":
        if not (settings.author_cluster and settings.author_task_definition and settings.author_subnets):
            raise RuntimeError(
                "the fargate author backend needs TABFREE_AUTHOR_CLUSTER, _TASK_DEFINITION and _SUBNETS"
            )
        return FargateSandbox(
            settings.author_cluster,
            settings.author_task_definition,
            settings.author_subnets,
            settings.author_security_groups,
            settings.ai_bedrock_region,
        )
    if settings.author_backend == "local":
        if settings.environment not in ("dev", "test"):
            raise RuntimeError("the local author backend has no isolation; use it only in dev")
        return LocalSandbox()
    return None
