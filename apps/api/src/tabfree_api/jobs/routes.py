"""Portal API for schedules, jobs, refresh tasks, subscriptions, alerts and webhooks (P6.1-P6.4, P6.9).

No ``from __future__ import annotations``: FastAPI evaluates the ``Depends`` annotations.

Who may do what:
- schedules, webhooks and every job: site administrators;
- refresh tasks: Refresh Extracts on the model (owners, leaders, administrators by default);
- subscriptions: anyone who can view the dashboard may subscribe themselves; adding other people,
  groups or addresses needs the dashboard's owner, a project leader or an administrator;
- alerts: anyone who can view the dashboard, for themselves and other users who can view it;
- people see their own subscriptions, alerts and the jobs they started or own.
"""

import urllib.parse
import uuid
from html import escape
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, insert, select, update

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import Capability, SiteRole, ancestry
from tabfree_api.db import schema as t
from tabfree_api.db.store import now
from tabfree_api.jobs import alerts as alerts_mod
from tabfree_api.jobs import common, webhooks
from tabfree_api.jobs.queue import JobQueue, _aware
from tabfree_api.jobs.refresh import MODES
from tabfree_api.jobs.schedule import ScheduleError
from tabfree_api.jobs.scheduler import SchedulesStore


class ScheduleBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    spec: dict[str, Any]
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    spec: dict[str, Any] | None = None
    enabled: bool | None = None


class RefreshBody(BaseModel):
    model: str = Field(max_length=100)
    mode: Literal["full", "incremental", "materialized_view", "rollup"]
    schedule: str | None = None
    settings: dict[str, Any]
    enabled: bool = True


class Recipient(BaseModel):
    user: str | None = Field(default=None, max_length=255)
    group: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)


class SubscriptionBody(BaseModel):
    dashboard: str = Field(max_length=100)
    subject: str = Field(min_length=1, max_length=255)
    message: str = Field(default="", max_length=4000)
    recipients: list[Recipient] = Field(default_factory=list, max_length=500)
    format: Literal["png", "pdf"] = "png"
    state: dict[str, Any] = Field(default_factory=dict)
    customView: str | None = None  # noqa: N815 - JSON field name
    schedule: str | None = None
    afterRefreshOf: str | None = Field(default=None, max_length=100)  # noqa: N815 - JSON field name
    onlyIfData: bool = False  # noqa: N815 - JSON field name
    webhookUrl: str | None = Field(default=None, max_length=2000)  # noqa: N815 - JSON field name


class AlertBody(BaseModel):
    dashboard: str = Field(max_length=100)
    title: str = Field(min_length=1, max_length=255)
    query: str = Field(max_length=64)
    measure: str = Field(max_length=256)
    condition: dict[str, Any]
    state: dict[str, Any] = Field(default_factory=dict)
    recipients: list[Recipient] = Field(default_factory=list, max_length=200)
    schedule: str | None = None
    afterRefreshOf: str | None = Field(default=None, max_length=100)  # noqa: N815 - JSON field name
    repeat: Literal["once", "every_check", "daily"] = "once"


class WebhookBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(max_length=2000)
    events: list[str] = Field(min_length=1, max_length=20)


def _rows(engine: Any, query: Any) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        out = []
        for row in conn.execute(query).mappings():
            item = {k: v for k, v in row.items() if k != "site_id"}
            for key, value in list(item.items()):
                if hasattr(value, "isoformat"):
                    item[key] = (_aware(value) or value).isoformat()
            out.append(item)
        return out


def add_routes(router: APIRouter, ctx: Any, current: Any, csrf: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias
    Csrf = Annotated[None, Depends(csrf)]  # noqa: N806

    def db() -> Any:
        if ctx.jobs is None:
            raise HTTPException(404, "schedules and jobs need the database (TABFREE_DATABASE_URL)")
        return ctx.site

    def is_admin(info: SessionInfo) -> bool:
        return info.site_role is SiteRole.SITE_ADMINISTRATOR

    def require_admin(info: SessionInfo) -> None:
        if not is_admin(info):
            raise HTTPException(403, "site administrators only")

    def viewable(info: SessionInfo, dashboard: str) -> None:
        if dashboard not in db().dashboards or not common.can_view(ctx.site, info, dashboard):
            raise HTTPException(404, f"no dashboard {dashboard!r}")

    def manages_dashboard(info: SessionInfo, dashboard: str) -> bool:
        if is_admin(info):
            return True
        entry = ctx.site.dashboards[dashboard]
        if entry.owner == info.username:
            return True
        principal = info.principal()
        return any(
            p.owner == info.username or p.leads(principal) for p in ancestry(entry.project, ctx.site.projects)
        )

    def schedule_exists(schedule: str | None) -> None:
        if (
            schedule is not None
            and SchedulesStore(ctx.site.engine, ctx.site.id, clock=now).get(schedule) is None
        ):
            raise HTTPException(422, f"no schedule {schedule!r}")

    # ---- schedules (P6.1) ------------------------------------------------------------------------------
    def schedules_store() -> SchedulesStore:
        site = db()
        return SchedulesStore(site.engine, site.id, clock=now)

    @router.get("/api/schedules")
    def list_schedules(info: Me) -> list[dict[str, Any]]:
        # Everyone may pick a schedule for their subscriptions and alerts.
        return schedules_store().list()

    @router.post("/api/schedules", status_code=201)
    def create_schedule(body: ScheduleBody, info: Me, _: Csrf) -> dict[str, Any]:
        require_admin(info)
        try:
            out = schedules_store().create(body.name, body.spec, enabled=body.enabled)
        except ScheduleError as exc:
            raise HTTPException(422, str(exc)) from None
        ctx.audit("schedule_create", info.username, resource_kind="schedule", resource_id=out["id"])
        return out

    @router.patch("/api/schedules/{schedule_id}")
    def patch_schedule(schedule_id: str, body: SchedulePatch, info: Me, _: Csrf) -> dict[str, Any]:
        require_admin(info)
        try:
            return schedules_store().update(schedule_id, name=body.name, spec=body.spec, enabled=body.enabled)
        except ScheduleError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.delete("/api/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: str, info: Me, _: Csrf) -> None:
        require_admin(info)
        try:
            if not schedules_store().delete(schedule_id):
                raise HTTPException(404, "no such schedule")
        except ScheduleError as exc:
            raise HTTPException(409, str(exc)) from None

    # ---- jobs (P6.1) -----------------------------------------------------------------------------------
    @router.get("/api/jobs")
    def list_jobs(
        info: Me, status: str | None = None, kind: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        site = db()
        queue: JobQueue = ctx.jobs
        mine = None if is_admin(info) else info.username
        jobs = queue.list(site.id, status=status, kind=kind, created_by=mine, limit=limit)
        if mine is not None:
            jobs += [
                j
                for j in _owned_jobs(info.username, status, kind, limit)
                if j["id"] not in {x["id"] for x in jobs}
            ]
        return {
            "jobs": sorted(jobs, key=lambda j: -int(j["id"]))[:limit],
            "counts": queue.counts(site.id) if mine is None else {},
        }

    def _owned_jobs(username: str, status: str | None, kind: str | None, limit: int) -> list[dict[str, Any]]:
        """Scheduled jobs for this person's subscriptions, alerts and refresh tasks."""
        site = ctx.site
        keys: list[tuple[str, str, str]] = []
        for table, job_kind, key in (
            (t.subscriptions, "subscription", "subscription"),
            (t.data_alerts, "alert", "alert"),
            (t.refresh_tasks, "refresh", "task"),
        ):
            with site.engine.connect() as conn:
                ids = [
                    r[0]
                    for r in conn.execute(
                        select(table.c.id).where(table.c.site_id == site.id, table.c.owner == username)
                    )
                ]
            keys += [(job_kind, key, i) for i in ids]
        if not keys:
            return []
        recent = ctx.jobs.list(site.id, status=status, kind=kind, limit=1000)
        wanted = {(k, i) for k, _, i in keys}
        names = {k: key for k, key, _ in keys}
        return [
            j
            for j in recent
            if (j["kind"], str((j["payload"] or {}).get(names.get(j["kind"], ""), ""))) in wanted
        ][:limit]

    def job_or_404(info: SessionInfo, job_id: int) -> dict[str, Any]:
        job = ctx.jobs.get(job_id) if ctx.jobs is not None else None
        if job is None or job["site_id"] != ctx.site.id:
            raise HTTPException(404, "no such job")
        if (
            not is_admin(info)
            and job["created_by"] != info.username
            and job["id"] not in {j["id"] for j in _owned_jobs(info.username, None, None, 1000)}
        ):
            raise HTTPException(404, "no such job")
        return dict(job)

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: int, info: Me) -> dict[str, Any]:
        return job_or_404(info, job_id)

    @router.post("/api/jobs/{job_id}/retry", status_code=201)
    def retry_job(job_id: int, info: Me, _: Csrf) -> dict[str, Any]:
        job_or_404(info, job_id)
        new = ctx.jobs.retry(job_id, by=info.username)
        if new is None:
            raise HTTPException(409, "only finished jobs can run again")
        return {"id": new}

    @router.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, info: Me, _: Csrf) -> dict[str, Any]:
        job_or_404(info, job_id)
        if not ctx.jobs.cancel(job_id):
            raise HTTPException(409, "only queued jobs can be cancelled")
        return {"status": "cancelled"}

    # ---- refresh tasks (P6.2) --------------------------------------------------------------------------
    def may_refresh(info: SessionInfo, model: str) -> None:
        if model not in db().models or not ctx.site.can_model(
            info.principal(), Capability.REFRESH_EXTRACTS, model
        ):
            raise HTTPException(403, f"refreshing {model!r} needs the Refresh Extracts capability")

    @router.get("/api/refresh-tasks")
    def list_refresh(info: Me) -> list[dict[str, Any]]:
        site = db()
        r = t.refresh_tasks.c
        rows = _rows(site.engine, select(t.refresh_tasks).where(r.site_id == site.id).order_by(r.model_id))
        return [
            row
            for row in rows
            if is_admin(info)
            or ctx.site.can_model(info.principal(), Capability.REFRESH_EXTRACTS, row["model_id"])
        ]

    @router.post("/api/refresh-tasks", status_code=201)
    def create_refresh(body: RefreshBody, info: Me, _: Csrf) -> dict[str, Any]:
        site = db()
        may_refresh(info, body.model)
        schedule_exists(body.schedule)
        if body.mode not in MODES:
            raise HTTPException(422, "unknown mode")
        tid = str(uuid.uuid4())
        with site.engine.begin() as conn:
            conn.execute(
                insert(t.refresh_tasks).values(
                    site_id=site.id, id=tid, model_id=body.model, mode=body.mode, schedule_id=body.schedule,
                    settings=body.settings, owner=info.username, enabled=body.enabled, created_at=now(),
                )
            )  # fmt: skip
        ctx.audit("refresh_task_create", info.username, resource_kind="model", resource_id=body.model)
        return {"id": tid}

    @router.post("/api/refresh-tasks/{task_id}/run", status_code=202)
    def run_refresh(task_id: str, info: Me, _: Csrf) -> dict[str, Any]:
        site = db()
        r = t.refresh_tasks.c
        with site.engine.connect() as conn:
            row = conn.execute(select(r.model_id).where(r.site_id == site.id, r.id == task_id)).first()
        if row is None:
            raise HTTPException(404, "no such refresh task")
        may_refresh(info, str(row[0]))
        job = ctx.jobs.enqueue(
            site.id,
            "refresh",
            {"task": task_id},
            created_by=info.username,
            dedupe_key=f"refresh:{task_id}",
            priority=5,
        )
        return {"job": job, "status": "queued" if job else "already queued"}

    @router.delete("/api/refresh-tasks/{task_id}", status_code=204)
    def delete_refresh(task_id: str, info: Me, _: Csrf) -> None:
        site = db()
        r = t.refresh_tasks.c
        with site.engine.connect() as conn:
            row = conn.execute(select(r.model_id).where(r.site_id == site.id, r.id == task_id)).first()
        if row is None:
            raise HTTPException(404, "no such refresh task")
        may_refresh(info, str(row[0]))
        with site.engine.begin() as conn:
            conn.execute(delete(t.refresh_tasks).where(r.site_id == site.id, r.id == task_id))

    # ---- subscriptions (P6.3) --------------------------------------------------------------------------
    def check_recipients(
        info: SessionInfo, dashboard: str, recipients: list[Recipient]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in recipients:
            entry = {k: v for k, v in r.model_dump().items() if v}
            if len(entry) != 1:
                raise HTTPException(422, "each recipient is one of user, group or email")
            out.append(entry)
        others = [r for r in out if r.get("user") != info.username]
        if others and not manages_dashboard(info, dashboard):
            raise HTTPException(
                403, "only the dashboard's owner, a project leader or an administrator subscribes others"
            )
        return out or [{"user": info.username}]

    def subscription_or_404(info: SessionInfo, sub_id: str) -> dict[str, Any]:
        site = db()
        s = t.subscriptions.c
        rows = _rows(site.engine, select(t.subscriptions).where(s.site_id == site.id, s.id == sub_id))
        if not rows or (rows[0]["owner"] != info.username and not is_admin(info)):
            raise HTTPException(404, "no such subscription")
        return rows[0]

    @router.get("/api/subscriptions")
    def list_subscriptions(info: Me, dashboard: str | None = None) -> list[dict[str, Any]]:
        site = db()
        s = t.subscriptions.c
        query = select(t.subscriptions).where(s.site_id == site.id)
        if not is_admin(info):
            query = query.where(s.owner == info.username)
        if dashboard:
            query = query.where(s.dashboard_id == dashboard)
        return _rows(site.engine, query.order_by(s.created_at.desc()))

    @router.post("/api/subscriptions", status_code=201)
    def create_subscription(body: SubscriptionBody, info: Me, _: Csrf) -> dict[str, Any]:
        site = db()
        viewable(info, body.dashboard)
        schedule_exists(body.schedule)
        if body.schedule is None and body.afterRefreshOf is None:
            raise HTTPException(422, "a subscription runs on a schedule or after a model refreshes")
        if body.webhookUrl:
            try:
                webhooks.check_url(body.webhookUrl, ctx.settings.environment)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        recipients = check_recipients(info, body.dashboard, body.recipients)
        external = [
            r["email"]
            for r in recipients
            if "email" in r and not common.is_internal(ctx.settings, r["email"])
        ]
        status = "pending_confirmation" if external else "active"
        sid = str(uuid.uuid4())
        when = now()
        with site.engine.begin() as conn:
            conn.execute(
                insert(t.subscriptions).values(
                    site_id=site.id, id=sid, owner=info.username, dashboard_id=body.dashboard,
                    subject=body.subject, message=body.message, recipients=recipients, format=body.format,
                    state=body.state, custom_view_id=body.customView, schedule_id=body.schedule,
                    after_refresh_of=body.afterRefreshOf, only_if_data=body.onlyIfData,
                    webhook_url=body.webhookUrl, status=status, created_at=when, updated_at=when,
                )
            )  # fmt: skip
        ctx.audit("subscription_create", info.username, resource_kind="dashboard", resource_id=body.dashboard)
        return {"id": sid, "status": status, "unconfirmed": external}

    @router.post("/api/subscriptions/{sub_id}/confirm")
    def confirm_external(sub_id: str, info: Me, _: Csrf) -> dict[str, Any]:
        """The owner confirms external addresses: they will see the dashboard as the owner does."""
        sub = subscription_or_404(info, sub_id)
        if sub["owner"] != info.username:
            raise HTTPException(403, "only the subscription's owner confirms its external recipients")
        recipients = [{**r, "confirmed": True} if "email" in r else r for r in sub["recipients"]]
        site = ctx.site
        s = t.subscriptions.c
        with site.engine.begin() as conn:
            conn.execute(
                update(t.subscriptions)
                .where(s.site_id == site.id, s.id == sub_id)
                .values(recipients=recipients, status="active", updated_at=now())
            )
        ctx.audit(
            "subscription_confirm_external",
            info.username,
            resource_kind="dashboard",
            resource_id=sub["dashboard_id"],
        )
        return {"status": "active"}

    @router.post("/api/subscriptions/{sub_id}/{action}")
    def subscription_action(
        sub_id: str, action: Literal["pause", "resume", "send"], info: Me, _: Csrf
    ) -> dict[str, Any]:
        sub = subscription_or_404(info, sub_id)
        site = ctx.site
        s = t.subscriptions.c
        if action == "send":
            job = ctx.jobs.enqueue(
                site.id, "subscription", {"subscription": sub_id}, created_by=info.username, priority=5
            )
            return {"job": job}
        if sub["status"] == "pending_confirmation":
            raise HTTPException(409, "confirm the external recipients first")
        with site.engine.begin() as conn:
            conn.execute(
                update(t.subscriptions)
                .where(s.site_id == site.id, s.id == sub_id)
                .values(status="paused" if action == "pause" else "active", updated_at=now())
            )
        return {"status": "paused" if action == "pause" else "active"}

    @router.delete("/api/subscriptions/{sub_id}", status_code=204)
    def delete_subscription(sub_id: str, info: Me, _: Csrf) -> None:
        subscription_or_404(info, sub_id)
        s = t.subscriptions.c
        with ctx.site.engine.begin() as conn:
            conn.execute(delete(t.subscriptions).where(s.site_id == ctx.site.id, s.id == sub_id))

    def _unsubscribe_parts(token: str) -> list[str]:
        parts = common.verify(ctx.settings, token)
        if not parts or len(parts) != 4 or parts[0] != "unsubscribe" or parts[1] != db().id:
            raise HTTPException(400, "this unsubscribe link is not valid")
        return parts

    @router.get("/unsubscribe", include_in_schema=False)
    def unsubscribe_page(token: str) -> HTMLResponse:
        _unsubscribe_parts(token)
        # A form, not a GET action: mail scanners follow links.
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Unsubscribe</title>"
            "<main style='font-family:system-ui;max-width:32rem;margin:4rem auto'>"
            "<h1>Unsubscribe</h1><p>Stop receiving this TabFree subscription?</p>"
            f"<form method=post><input type=hidden name=token value='{escape(token, quote=True)}'>"
            "<button type=submit>Unsubscribe</button></form></main>",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @router.post("/unsubscribe", include_in_schema=False)
    async def unsubscribe(request: Request) -> HTMLResponse:
        # Parsed with the standard library: the form has one field, and no multipart dependency.
        form = urllib.parse.parse_qs((await request.body())[:4096].decode("utf-8", "replace"))
        token = str((form.get("token") or [request.query_params.get("token") or ""])[0])
        _, site_id, sub_id, address = _unsubscribe_parts(token)
        m = t.mail_suppressions.c
        with ctx.site.engine.begin() as conn:
            exists = conn.execute(
                select(m.address).where(
                    m.site_id == site_id, m.address == address, m.subscription_id == sub_id
                )
            ).first()
            if exists is None:
                conn.execute(
                    insert(t.mail_suppressions).values(
                        site_id=site_id, address=address, subscription_id=sub_id, created_at=now()
                    )
                )
        ctx.audit("unsubscribe", None, resource_kind="subscription", resource_id=sub_id)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Unsubscribed</title><p>You are unsubscribed.</p>"
        )

    # ---- alerts (P6.4) ---------------------------------------------------------------------------------
    def alert_or_404(info: SessionInfo, alert_id: str) -> dict[str, Any]:
        a = t.data_alerts.c
        rows = _rows(db().engine, select(t.data_alerts).where(a.site_id == ctx.site.id, a.id == alert_id))
        if not rows or (rows[0]["owner"] != info.username and not is_admin(info)):
            raise HTTPException(404, "no such alert")
        return rows[0]

    @router.get("/api/alerts")
    def list_alerts(info: Me, dashboard: str | None = None) -> list[dict[str, Any]]:
        a = t.data_alerts.c
        query = select(t.data_alerts).where(a.site_id == db().id)
        if not is_admin(info):
            query = query.where(a.owner == info.username)
        if dashboard:
            query = query.where(a.dashboard_id == dashboard)
        return _rows(ctx.site.engine, query.order_by(a.created_at.desc()))

    @router.post("/api/alerts", status_code=201)
    def create_alert(body: AlertBody, info: Me, _: Csrf) -> dict[str, Any]:
        site = db()
        viewable(info, body.dashboard)
        schedule_exists(body.schedule)
        if body.schedule is None and body.afterRefreshOf is None:
            raise HTTPException(422, "an alert is checked on a schedule or after a model refreshes")
        manifest = site.bundle(body.dashboard).manifest
        if body.query not in manifest.get("queries", {}):
            raise HTTPException(422, f"no query {body.query!r} in the dashboard")
        try:
            condition = alerts_mod.check_condition(body.condition)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        recipients = []
        for r in body.recipients:
            if not r.user or r.group or r.email:
                raise HTTPException(422, "alert recipients are TabFree users")
            other = common.session_info(site, r.user) if r.user in site.users else None
            if other is None or not common.can_view(site, other, body.dashboard):
                raise HTTPException(422, f"{r.user!r} cannot view this dashboard")
            recipients.append({"user": r.user})
        aid = str(uuid.uuid4())
        with site.engine.begin() as conn:
            conn.execute(
                insert(t.data_alerts).values(
                    site_id=site.id, id=aid, owner=info.username, title=body.title,
                    dashboard_id=body.dashboard,
                    query=body.query, measure=body.measure, condition=condition, state=body.state,
                    recipients=recipients or [{"user": info.username}], schedule_id=body.schedule,
                    after_refresh_of=body.afterRefreshOf, repeat=body.repeat, enabled=True, created_at=now(),
                )
            )  # fmt: skip
        ctx.audit("alert_create", info.username, resource_kind="dashboard", resource_id=body.dashboard)
        return {"id": aid}

    @router.post("/api/alerts/{alert_id}/check", status_code=202)
    def check_alert(alert_id: str, info: Me, _: Csrf) -> dict[str, Any]:
        alert_or_404(info, alert_id)
        return {
            "job": ctx.jobs.enqueue(
                ctx.site.id, "alert", {"alert": alert_id}, created_by=info.username, priority=5
            )
        }

    @router.delete("/api/alerts/{alert_id}", status_code=204)
    def delete_alert(alert_id: str, info: Me, _: Csrf) -> None:
        alert_or_404(info, alert_id)
        a = t.data_alerts.c
        with ctx.site.engine.begin() as conn:
            conn.execute(delete(t.data_alerts).where(and_(a.site_id == ctx.site.id, a.id == alert_id)))

    # ---- webhooks (P6.9) -------------------------------------------------------------------------------
    @router.get("/api/webhooks")
    def list_webhooks(info: Me) -> list[dict[str, Any]]:
        require_admin(info)
        w = t.webhooks.c
        rows = _rows(db().engine, select(t.webhooks).where(w.site_id == ctx.site.id))
        for row in rows:
            row.pop("secret", None)
        return rows

    @router.post("/api/webhooks", status_code=201)
    def create_webhook(body: WebhookBody, info: Me, _: Csrf) -> dict[str, Any]:
        require_admin(info)
        site = db()
        try:
            webhooks.check_url(body.url, ctx.settings.environment)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        unknown = sorted(set(body.events) - set(webhooks.EVENTS) - {"*"})
        if unknown:
            raise HTTPException(422, f"unknown events: {', '.join(unknown)}")
        import secrets as random

        hid, secret = str(uuid.uuid4()), random.token_urlsafe(32)
        with site.engine.begin() as conn:
            conn.execute(
                insert(t.webhooks).values(
                    site_id=site.id, id=hid, name=body.name, url=body.url, events=body.events,
                    secret=secret, enabled=True, created_by=info.username, created_at=now(),
                )
            )  # fmt: skip
        ctx.audit("webhook_create", info.username, resource_kind="webhook", resource_id=hid)
        return {"id": hid, "secret": secret}

    @router.delete("/api/webhooks/{hook_id}", status_code=204)
    def delete_webhook(hook_id: str, info: Me, _: Csrf) -> None:
        require_admin(info)
        w = t.webhooks.c
        with db().engine.begin() as conn:
            if (
                conn.execute(delete(t.webhooks).where(w.site_id == ctx.site.id, w.id == hook_id)).rowcount
                != 1
            ):
                raise HTTPException(404, "no such webhook")
