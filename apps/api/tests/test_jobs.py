"""Phase 6 jobs: schedules, the queue, refresh, subscriptions, alerts, webhooks (P6.1-P6.4, P6.9)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db import schema as t
from tabfree_api.db.seed import import_site
from tabfree_api.jobs import alerts, refresh, webhooks
from tabfree_api.jobs.mail import LogMailer, mime
from tabfree_api.jobs.queue import JobQueue
from tabfree_api.jobs.schedule import ScheduleError, describe, next_run, validate
from tabfree_api.jobs.scheduler import Scheduler, SchedulesStore
from tabfree_api.jobs.worker import Permanent, Worker
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

CSRF = {"X-TabFree-Request": "1"}
UTC = dt.UTC


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


# -- schedules ------------------------------------------------------------------------------------------


def test_daily_schedules_keep_wall_clock_time_across_daylight_saving() -> None:
    spec = {"frequency": "daily", "at": "06:30", "timezone": "Europe/London"}
    winter = next_run(spec, at("2027-01-10T12:00:00"))
    assert winter == at("2027-01-11T06:30:00")  # GMT
    summer = next_run(spec, at("2027-07-10T12:00:00"))
    assert summer == at("2027-07-11T05:30:00")  # BST is UTC+1
    # The spring-forward day: 01:30 does not exist in London; it runs at the first valid instant.
    gap = next_run(
        {"frequency": "daily", "at": "01:30", "timezone": "Europe/London"}, at("2027-03-28T00:00:00")
    )
    assert gap.astimezone(dt.UTC) == at("2027-03-28T01:30:00")


def test_weekly_monthly_and_hourly_schedules() -> None:
    weekly = {"frequency": "weekly", "at": "08:00", "weekdays": ["Mon", "thu"], "timezone": "UTC"}
    assert next_run(weekly, at("2027-01-05T09:00:00")) == at("2027-01-07T08:00:00")  # Tue → Thu
    monthly = {"frequency": "monthly", "at": "07:00", "days": [-1], "timezone": "UTC"}
    assert next_run(monthly, at("2027-02-01T00:00:00")) == at("2027-02-28T07:00:00")
    hourly = {"frequency": "hourly", "every": 4, "start": "06:00", "end": "18:00", "timezone": "UTC"}
    assert next_run(hourly, at("2027-01-05T10:00:00")) == at("2027-01-05T14:00:00")
    assert next_run(hourly, at("2027-01-05T18:30:00")) == at("2027-01-06T06:00:00")
    assert describe(weekly) == "every Mon, Thu at 08:00 (UTC)"
    for bad in (
        {"frequency": "yearly"},
        {"frequency": "daily", "at": "25:00"},
        {"frequency": "daily", "timezone": "Mars/Base"},
        {"frequency": "weekly", "at": "08:00"},
        {"frequency": "hourly", "every": 5},
        {"frequency": "monthly", "days": [0]},
    ):
        with pytest.raises(ScheduleError):
            validate(bad)


# -- the app --------------------------------------------------------------------------------------------


class Clock:
    def __init__(self, start: dt.datetime) -> None:
        self.now = start

    def __call__(self) -> dt.datetime:
        return self.now


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], str]] = []

    def render(self, info: Any, dashboard: str, state: Any, fmt: str) -> bytes:
        self.calls.append((info.username, dashboard, dict(state), fmt))
        return b"\x89PNG-for-" + info.username.encode()


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    engine = connect(url)
    upgrade(engine)
    db = import_site(engine, Site(Settings().site_file))
    for name in ("admin", "erin", "casey", "mallory", "ivan", "alice"):
        db.ensure_user(name, name, email=f"{name}@corp.example")
    application = create_app(
        Settings(
            environment="test",
            dev_login=True,
            database_url=url,
            site_id="dev",
            permission_cache_s=0,
            base_url="http://testserver",
            mail_internal_domains=["corp.example"],
        )
    )
    ctx = application.state.context
    ctx.renderer = FakeRenderer()
    ctx.mailer = LogMailer()
    return application


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def worker(app: Any, handlers: Any = None) -> Worker:
    ctx = app.state.context
    return Worker(ctx, ctx.jobs, handlers=handlers)


# -- the queue ------------------------------------------------------------------------------------------


def test_the_queue_claims_retries_with_backoff_and_reclaims_expired_leases(app: Any) -> None:
    clock = Clock(at("2027-01-05T10:00:00"))
    queue = JobQueue(app.state.context.site.engine, clock=clock)
    first = queue.enqueue("dev", "refresh", {"task": "a"}, dedupe_key="refresh:a", max_attempts=2)
    assert first is not None
    assert queue.enqueue("dev", "refresh", {"task": "a"}, dedupe_key="refresh:a") is None  # deduplicated
    job = queue.claim("w1", lease_s=60)
    assert job is not None
    assert job.attempts == 1
    assert queue.claim("w2") is None
    assert queue.fail(job.id, "engine busy") == "queued"
    assert queue.claim("w1") is None  # backing off
    clock.now += dt.timedelta(seconds=31)
    again = queue.claim("w1", lease_s=60)
    assert again is not None
    assert again.attempts == 2
    clock.now += dt.timedelta(seconds=61)  # the worker died: the lease expires
    assert queue.claim("w2") is not None
    assert queue.fail(first, "still broken") == "failed"
    record = queue.get(first)
    assert record is not None
    assert record["status"] == "failed"
    assert record["error"] == "still broken"
    retried = queue.retry(first, by="admin")
    assert retried is not None
    assert queue.cancel(retried)
    clock.now += dt.timedelta(days=40)
    assert queue.prune(clock.now - dt.timedelta(days=30)) == 2


def test_the_scheduler_fires_each_due_schedule_once(app: Any) -> None:
    ctx = app.state.context
    clock = Clock(at("2027-01-05T05:00:00"))
    store = SchedulesStore(ctx.site.engine, "dev", clock=clock)
    daily = store.create("Daily 06:00", {"frequency": "daily", "at": "06:00", "timezone": "UTC"})
    assert daily["next_run_at"] == "2027-01-05T06:00:00+00:00"
    when = clock()
    with ctx.site.engine.begin() as conn:
        conn.execute(
            insert(t.refresh_tasks).values(
                site_id="dev",
                id="r1",
                model_id="superstore_sales",
                mode="full",
                schedule_id=daily["id"],
                settings={},
                enabled=True,
                created_at=when,
            )
        )
        conn.execute(
            insert(t.subscriptions).values(
                site_id="dev",
                id="s1",
                owner="erin",
                dashboard_id="sales-overview",
                subject="Daily",
                recipients=[{"user": "erin"}],
                state={},
                schedule_id=daily["id"],
                status="active",
                created_at=when,
                updated_at=when,
            )
        )
        conn.execute(
            insert(t.subscriptions).values(
                site_id="dev",
                id="s2",
                owner="erin",
                dashboard_id="sales-overview",
                subject="Paused",
                recipients=[],
                state={},
                schedule_id=daily["id"],
                status="paused",
                created_at=when,
                updated_at=when,
            )
        )
    queue = JobQueue(ctx.site.engine, clock=clock)
    first, second = (
        Scheduler(ctx.site.engine, queue, clock=clock),
        Scheduler(ctx.site.engine, queue, clock=clock),
    )
    assert first.tick() == 0  # not due yet
    clock.now = at("2027-01-05T06:00:30")
    assert first.tick() == 2
    assert second.tick() == 0
    kinds = sorted(j["kind"] for j in queue.list("dev"))
    assert kinds == ["refresh", "subscription"]
    assert store.get(daily["id"])["next_run_at"] == "2027-01-06T06:00:00+00:00"  # type: ignore[index]
    with pytest.raises(ScheduleError, match="in use"):
        store.delete(daily["id"])


# -- refresh (P6.2) -------------------------------------------------------------------------------------


def test_refresh_statements() -> None:
    table = {
        "target": "orders",
        "database": "lake",
        "source": "SELECT * FROM erp.dbo.orders",
        "location": "s3://lake/w/orders",
    }
    statements, kept = refresh.full_athena(table, "20270105060000", ["20270101060000", "20270104060000"])
    assert statements[0].startswith("CREATE TABLE lake.orders__v20270105060000 WITH (table_type = 'ICEBERG'")
    assert "location = 's3://lake/w/orders/v20270105060000/'" in statements[0]
    assert statements[1] == "CREATE OR REPLACE VIEW lake.orders AS SELECT * FROM lake.orders__v20270105060000"
    assert statements[2:] == ["DROP TABLE IF EXISTS lake.orders__v20270101060000"]
    assert kept == ["20270104060000", "20270105060000"]
    swap = refresh.full_redshift({**table, "schema": "mart"}, exists=True)
    assert swap[3:5] == [
        "ALTER TABLE mart.orders RENAME TO orders__old",
        "ALTER TABLE mart.orders__new RENAME TO orders",
    ]
    (merge,) = refresh.incremental(
        {
            **table,
            "incrementalKey": "updated_at",
            "uniqueKey": ["id"],
            "columns": ["id", "amount", "updated_at"],
        },
        "athena",
    )
    assert merge.startswith(
        "MERGE INTO lake.orders AS target USING (SELECT * FROM (SELECT * FROM erp.dbo.orders)"
    )
    assert "WHEN MATCHED THEN UPDATE SET amount = s.amount, updated_at = s.updated_at" in merge
    for bad in (
        {**table, "target": "orders; DROP TABLE x"},
        {**table, "source": "SELECT 1; DROP TABLE y"},
        {**table, "source": "DELETE FROM x"},
    ):
        with pytest.raises(Permanent):
            refresh.full_athena(bad, "1", [])


def test_a_refresh_runs_bumps_the_data_version_and_triggers_followers(app: Any) -> None:
    ctx = app.state.context
    engine = ctx.service._engine_for(ctx.site.models["superstore_sales"])
    engine.connection.execute("CREATE TABLE main.daily_sales (day DATE, amount DOUBLE)")
    when = ctx.jobs.clock()
    with ctx.site.engine.begin() as conn:
        conn.execute(
            insert(t.refresh_tasks).values(
                site_id="dev",
                id="inc",
                model_id="superstore_sales",
                mode="incremental",
                settings={
                    "tables": [
                        {
                            "target": "daily_sales",
                            "database": "main",
                            "source": "SELECT DATE '2027-01-01' AS day, 10.0 AS amount",
                            "incrementalKey": "day",
                            "keyType": "date",
                        }
                    ]
                },
                owner="admin",
                enabled=True,
                created_at=when,
            )
        )
        conn.execute(
            insert(t.subscriptions).values(
                site_id="dev",
                id="after",
                owner="erin",
                dashboard_id="sales-overview",
                subject="After refresh",
                recipients=[{"user": "erin"}],
                state={},
                after_refresh_of="superstore_sales",
                status="active",
                created_at=when,
                updated_at=when,
            )
        )
    ctx.jobs.enqueue("dev", "refresh", {"task": "inc"}, created_by="admin")
    w = worker(app)
    assert w.run_once()
    rows = engine.connection.execute("SELECT count(*) FROM main.daily_sales").fetchone()
    assert rows[0] == 1
    jobs = {j["kind"]: j for j in ctx.jobs.list("dev")}
    assert jobs["refresh"]["status"] == "succeeded"
    assert ctx.site.data_version("superstore_sales") == jobs["refresh"]["result"]["dataVersion"]
    assert jobs["subscription"]["created_by"] == "event:refresh:superstore_sales"
    w.drain()  # the follow-on subscription renders for Erin
    assert ctx.renderer.calls[-1][:2] == ("erin", "sales-overview")
    # Running it again adds nothing: the key is not past the target's maximum.
    ctx.jobs.enqueue("dev", "refresh", {"task": "inc"}, created_by="admin")
    w.drain()
    assert engine.connection.execute("SELECT count(*) FROM main.daily_sales").fetchone()[0] == 1


# -- subscriptions (P6.3) -------------------------------------------------------------------------------


def test_subscriptions_render_per_recipient_and_honour_permissions(app: Any) -> None:
    ctx = app.state.context
    erin, mallory = as_user(app, "erin"), as_user(app, "mallory")
    schedule = (
        as_user(app, "admin")
        .post(
            "/api/schedules",
            json={"name": "Morning", "spec": {"frequency": "daily", "at": "07:00"}},
            headers=CSRF,
        )
        .json()
    )
    body = {
        "dashboard": "sales-overview",
        "subject": "Sales",
        "schedule": schedule["id"],
        "state": {"filters": {"region": {"members": ["East"]}}},
    }
    # Subscribing others needs the dashboard's owner, a project leader or an administrator.
    denied = erin.post("/api/subscriptions", json={**body, "recipients": [{"user": "alice"}]}, headers=CSRF)
    assert denied.status_code == 403
    assert mallory.post("/api/subscriptions", json=body, headers=CSRF).status_code == 404  # no View
    admin = as_user(app, "admin")
    made = admin.post(
        "/api/subscriptions",
        json={
            **body,
            "recipients": [
                {"user": "erin"},
                {"user": "mallory"},
                {"group": "Executives"},
                {"email": "partner@other.example"},
            ],
        },
        headers=CSRF,
    ).json()
    assert made["status"] == "pending_confirmation"
    assert made["unconfirmed"] == ["partner@other.example"]
    assert admin.post(f"/api/subscriptions/{made['id']}/resume", headers=CSRF).status_code == 409
    assert admin.post(f"/api/subscriptions/{made['id']}/confirm", headers=CSRF).json() == {"status": "active"}
    admin.post(f"/api/subscriptions/{made['id']}/send", headers=CSRF)
    worker(app).drain()
    rendered = {(user, tuple(sorted(state))) for user, _d, state, _f in ctx.renderer.calls}
    # Erin and Casey (Executives) render as themselves; the partner as the owner; Mallory is skipped.
    assert {u for u, _ in rendered} == {"erin", "casey", "admin"}
    sent = {m.to[0]: m for m in ctx.mailer.sent}
    assert set(sent) == {"erin@corp.example", "casey@corp.example", "partner@other.example"}
    message = sent["erin@corp.example"]
    assert message.attachments[0].data == b"\x89PNG-for-erin"
    assert message.headers["List-Unsubscribe"].startswith("<http://testserver/unsubscribe?token=")
    job = ctx.jobs.list("dev", kind="subscription")[0]
    assert {"address": "mallory@corp.example", "reason": "no View permission"} in job["result"]["skipped"]
    # Unsubscribing: the link shows a form; posting it stops further mail to that address.
    token = message.headers["List-Unsubscribe"].split("token=")[1].rstrip(">")
    anonymous = TestClient(app)
    assert "Unsubscribe" in anonymous.get("/unsubscribe", params={"token": token}).text
    assert anonymous.post("/unsubscribe", data={"token": token}).status_code == 200
    assert anonymous.get("/unsubscribe", params={"token": token[:-2] + "xx"}).status_code == 400
    ctx.mailer.sent.clear()
    admin.post(f"/api/subscriptions/{made['id']}/send", headers=CSRF)
    worker(app).drain()
    assert "erin@corp.example" not in {m.to[0] for m in ctx.mailer.sent}


def test_only_if_data_skips_empty_dashboards(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = app.state.context
    monkeypatch.setattr(ctx.service, "query", lambda *a, **k: {"columns": [], "rows": [], "truncated": False})
    erin = as_user(app, "erin")
    made = erin.post(
        "/api/subscriptions",
        json={
            "dashboard": "sales-overview",
            "subject": "If any",
            "afterRefreshOf": "superstore_sales",
            "onlyIfData": True,
        },
        headers=CSRF,
    ).json()
    erin.post(f"/api/subscriptions/{made['id']}/send", headers=CSRF)
    worker(app).drain()
    assert ctx.mailer.sent == []
    assert ctx.jobs.list("dev", kind="subscription")[0]["result"]["skipped"] == [
        {"address": "erin@corp.example", "reason": "no data"}
    ]


# -- alerts (P6.4) --------------------------------------------------------------------------------------


def test_alert_conditions_and_repeats() -> None:
    cond = alerts.check_condition({"operator": "<", "threshold": 100})
    assert alerts.evaluate([150, 90.5, None], cond) == (True, 90.5)
    assert alerts.evaluate([150], cond) == (False, 150.0)
    assert alerts.evaluate([40, 50], {**cond, "aggregate": "sum"}) == (True, 90.0)
    assert alerts.evaluate([40, 150], {**cond, "aggregate": "all"})[0] is False
    with pytest.raises(ValueError, match="operator"):
        alerts.check_condition({"operator": "~", "threshold": 1})
    when = at("2027-01-05T10:00:00")
    assert alerts.should_notify("once", False, True, None, when)
    assert not alerts.should_notify("once", True, True, when, when)
    assert alerts.should_notify("every_check", True, True, when, when)
    assert alerts.should_notify("daily", True, True, when - dt.timedelta(hours=25), when)
    assert not alerts.should_notify("daily", False, False, None, when)


def test_alerts_notify_once_until_the_condition_clears(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = app.state.context
    values = {"Sales": 50.0}
    monkeypatch.setattr(
        ctx.service, "query",
        lambda *a, **k: {
            "columns": [{"name": "Sales"}, {"name": "Profit"}],
            "rows": [[values["Sales"], 1.0]],
            "truncated": False,
        },
    )  # fmt: skip
    erin = as_user(app, "erin")
    bad = erin.post(
        "/api/alerts",
        json={
            "dashboard": "sales-overview",
            "title": "x",
            "query": "nope",
            "measure": "Sales",
            "condition": {"operator": "<", "threshold": 100},
            "afterRefreshOf": "superstore_sales",
        },
        headers=CSRF,
    )
    assert bad.status_code == 422
    refused = erin.post(
        "/api/alerts",
        json={
            "dashboard": "sales-overview",
            "title": "x",
            "query": "kpis",
            "measure": "Sales",
            "condition": {"operator": "<", "threshold": 100},
            "afterRefreshOf": "superstore_sales",
            "recipients": [{"user": "mallory"}],
        },
        headers=CSRF,
    )
    assert refused.status_code == 422  # Mallory cannot view the dashboard
    made = erin.post(
        "/api/alerts",
        json={
            "dashboard": "sales-overview",
            "title": "Sales are low",
            "query": "kpis",
            "measure": "Sales",
            "condition": {"operator": "<", "threshold": 100},
            "afterRefreshOf": "superstore_sales",
        },
        headers=CSRF,
    ).json()

    def check() -> dict[str, Any]:
        erin.post(f"/api/alerts/{made['id']}/check", headers=CSRF)
        worker(app).drain()
        return dict(ctx.jobs.list("dev", kind="alert")[0]["result"])

    assert check() == {"firing": True, "value": 50.0, "notified": 1}
    assert ctx.mailer.sent[0].subject == "Alert: Sales are low"
    assert "Sales is 50" in ctx.mailer.sent[0].text
    assert check()["notified"] == 0  # still low: no repeat
    values["Sales"] = 500.0
    assert check() == {"firing": False, "value": 500.0, "notified": 0}
    values["Sales"] = 10.0
    assert check()["notified"] == 1  # it cleared, then fired again


# -- webhooks (P6.9), failures and permissions ----------------------------------------------------------


def test_webhooks_are_signed_and_retried(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = app.state.context
    admin = as_user(app, "admin")
    assert (
        admin.post(
            "/api/webhooks",
            json={"name": "x", "url": "http://example.com/hook", "events": ["*"]},
            headers=CSRF,
        ).status_code
        == 422
    )
    assert as_user(app, "erin").get("/api/webhooks").status_code == 403
    hook = admin.post(
        "/api/webhooks",
        json={"name": "CI", "url": "https://hooks.example/tabfree", "events": ["content.restored"]},
        headers=CSRF,
    ).json()
    assert "secret" not in admin.get("/api/webhooks").json()[0]
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    statuses = [503, 200]
    monkeypatch.setattr(
        webhooks,
        "post",
        lambda url, body, headers: calls.append((url, body, dict(headers))) or statuses.pop(0),
    )
    restored = admin.post("/api/content/dashboard/sales-overview/revisions/1/restore", headers=CSRF)
    assert restored.status_code == 200, restored.text
    w = worker(app)
    w.drain()
    job = ctx.jobs.list("dev", kind="webhook")[0]
    assert job["status"] == "queued"
    assert "HTTP 503" in job["error"]
    ctx.jobs.clock = lambda: dt.datetime.now(UTC) + dt.timedelta(minutes=5)
    w.drain()
    url, body, headers = calls[-1]
    assert url == "https://hooks.example/tabfree"
    assert headers["X-TabFree-Event"] == "content.restored"
    expected = "sha256=" + hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-TabFree-Signature"] == expected
    assert json.loads(body)["data"]["resource_id"] == "sales-overview"


def test_a_job_that_keeps_failing_tells_its_owner(app: Any) -> None:
    ctx = app.state.context

    def broken(_ctx: Any, _job: Any) -> Any:
        raise Permanent("the source table is gone")

    ctx.jobs.enqueue("dev", "refresh", {"task": "x"}, created_by="erin")
    Worker(ctx, ctx.jobs, handlers={"refresh": broken}).drain()
    (mail,) = ctx.mailer.sent
    assert mail.to == ["erin@corp.example"]
    assert "the source table is gone" in mail.text
    jobs = as_user(app, "erin").get("/api/jobs").json()["jobs"]
    assert jobs[0]["status"] == "failed"
    assert as_user(app, "ivan").get("/api/jobs").json()["jobs"] == []
    assert as_user(app, "erin").post(f"/api/jobs/{jobs[0]['id']}/retry", headers=CSRF).status_code == 201


def test_schedules_are_for_administrators_and_mail_is_well_formed(app: Any) -> None:
    erin = as_user(app, "erin")
    assert (
        erin.post(
            "/api/schedules", json={"name": "x", "spec": {"frequency": "daily"}}, headers=CSRF
        ).status_code
        == 403
    )
    admin = as_user(app, "admin")
    assert (
        admin.post(
            "/api/schedules",
            json={"name": "bad", "spec": {"frequency": "daily", "at": "99:00"}},
            headers=CSRF,
        ).status_code
        == 422
    )
    assert erin.get("/api/schedules").status_code == 200
    from tabfree_api.jobs.mail import Attachment, Message

    raw = mime(
        Message(["a@x"], "Hi\r\nBcc: evil@x", "body", attachments=[Attachment("d.png", "image/png", b"x")]),
        "TabFree <t@x>",
    )
    assert raw["Subject"] == "Hi  Bcc: evil@x"
    assert raw["Bcc"] is None
    with app.state.context.site.engine.connect() as conn:
        assert conn.execute(select(t.jobs.c.id)).first() is None
