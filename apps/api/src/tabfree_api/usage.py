"""Usage and performance for administrators (BUILD_PLAN P6.7), from the audit log and job history.

``GET /api/admin/usage?days=30``: views per day, the most used dashboards and their viewers, active
people, query latency (median and 95th percentile, per dashboard) and cache hit rate, bytes scanned,
jobs by kind and outcome with recent failures, and how much content, history and drafts the site
holds. Events are read for the window and summarised here, so the same code works on SQLite and
Postgres; the window is capped so a busy site's report stays quick.
"""

import datetime as dt
from collections import Counter, defaultdict
from statistics import median
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import SiteRole
from tabfree_api.db import schema as t
from tabfree_api.db.store import now

MAX_EVENTS = 500_000


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))])


def report(site: Any, days: int) -> dict[str, Any]:
    since = now() - dt.timedelta(days=days)
    a = t.audit_events.c
    with site.engine.connect() as conn:
        events = conn.execute(
            select(a.at, a.actor, a.action, a.resource_kind, a.resource_id, a.outcome, a.detail)
            .where(
                a.site_id == site.id,
                a.at >= since,
                a.action.in_(("view", "query_metrics", "sign_in", "export")),
            )
            .order_by(a.id.desc())
            .limit(MAX_EVENTS)
        ).all()
        j = t.jobs.c
        jobs = conn.execute(
            select(j.kind, j.status, j.error, j.id, j.finished_at).where(
                j.site_id == site.id, j.created_at >= since
            )
        ).all()
        c = t.content.c
        content = dict(
            conn.execute(
                select(c.kind, func.count())
                .where(c.site_id == site.id, c.deleted_at.is_(None))
                .group_by(c.kind)
            ).all()
        )
        deleted = conn.execute(
            select(func.count()).where(c.site_id == site.id, c.deleted_at.is_not(None))
        ).scalar()
        revisions = conn.execute(
            select(func.count()).where(t.content_revisions.c.site_id == site.id)
        ).scalar()
        drafts = conn.execute(
            select(func.count()).where(t.drafts.c.site_id == site.id, t.drafts.c.status == "open")
        ).scalar()
    per_day: Counter[str] = Counter()
    viewers_per_day: dict[str, set[str]] = defaultdict(set)
    dashboard_views: Counter[str] = Counter()
    dashboard_viewers: dict[str, set[str]] = defaultdict(set)
    durations: dict[str, list[float]] = defaultdict(list)
    all_durations: list[float] = []
    cache: Counter[str] = Counter()
    scanned = 0
    active: set[str] = set()
    exports = 0
    for at, actor, action, kind, resource, outcome, detail in events:
        day = at.date().isoformat()
        if action == "view" and outcome == "ok" and kind == "dashboard":
            per_day[day] += 1
            dashboard_views[str(resource)] += 1
            if actor:
                viewers_per_day[day].add(actor)
                dashboard_viewers[str(resource)].add(actor)
                active.add(actor)
        elif action == "query_metrics":
            detail = detail or {}
            duration = float(detail.get("duration_ms", 0))
            all_durations.append(duration)
            durations[str(resource)].append(duration)
            cache[str(detail.get("cache", "miss"))] += 1
            scanned += int(detail.get("scanned", 0) or 0)
        elif action == "export" and outcome == "ok":
            exports += 1
        elif action == "sign_in" and actor:
            active.add(actor)
    names = {d: site.bundle(d).manifest.get("title", d) for d in dashboard_views if d in site.dashboards}
    top = [
        {
            "dashboard": d,
            "title": names.get(d, d),
            "views": n,
            "viewers": len(dashboard_viewers[d]),
            "p50Ms": median(durations[d]) if durations[d] else None,
            "p95Ms": _p95(durations[d]) if durations[d] else None,
        }
        for d, n in dashboard_views.most_common(25)
    ]
    slow = sorted(((_p95(v), r, len(v)) for r, v in durations.items() if len(v) >= 5), reverse=True)[:10]
    slowest = [{"resource": r, "p95Ms": p95, "queries": n} for p95, r, n in slow]
    job_counts: Counter[tuple[str, str]] = Counter((str(k), str(s)) for k, s, *_ in jobs)
    failures = [
        {"id": i, "kind": k, "error": (e or "")[:300], "at": f.isoformat() if f else None}
        for k, s, e, i, f in jobs
        if s == "failed"
    ][:20]
    total_queries = sum(cache.values())
    return {
        "days": days,
        "truncated": len(events) >= MAX_EVENTS,
        "viewsPerDay": [
            {"day": d, "views": per_day[d], "viewers": len(viewers_per_day[d])} for d in sorted(per_day)
        ],
        "topDashboards": top,
        "activeUsers": len(active),
        "exports": exports,
        "queries": {
            "count": total_queries,
            "p50Ms": median(all_durations) if all_durations else None,
            "p95Ms": _p95(all_durations) if all_durations else None,
            "cacheHitRate": round(cache["hit"] / total_queries, 3) if total_queries else None,
            "scannedBytes": scanned,
            "slowest": slowest,
        },
        "jobs": [{"kind": k, "status": s, "count": n} for (k, s), n in sorted(job_counts.items())],
        "jobFailures": failures,
        "storage": {
            "dashboards": content.get("dashboard", 0),
            "models": content.get("model", 0),
            "revisions": int(revisions or 0),
            "openDrafts": int(drafts or 0),
            "recycleBin": int(deleted or 0),
        },
    }


def add_routes(router: APIRouter, ctx: Any, current: Any) -> None:
    Me = Annotated[SessionInfo, Depends(current)]  # noqa: N806 - a type alias

    @router.get("/api/admin/usage")
    def usage(info: Me, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
        if info.site_role is not SiteRole.SITE_ADMINISTRATOR:
            raise HTTPException(403, "site administrators only")
        if not hasattr(ctx.site, "engine"):
            raise HTTPException(404, "usage needs the database (TABFREE_DATABASE_URL)")
        return report(ctx.site, days)
