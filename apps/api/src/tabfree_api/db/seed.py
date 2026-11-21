"""Create sites and import a Phase 1 site file into the database (dev, tests, the first deployment)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, insert, select

from tabfree_api.authz import Rule
from tabfree_api.content import ContentError, Site
from tabfree_api.db import schema as t
from tabfree_api.db.store import DbSite, now


def create_site(
    engine: Engine,
    site_id: str,
    name: str,
    *,
    default_site_role: str = "viewer",
    settings: Mapping[str, Any] | None = None,
) -> DbSite:
    with engine.begin() as conn:
        if conn.execute(select(t.sites.c.id).where(t.sites.c.id == site_id)).scalar() is not None:
            raise ContentError(f"site {site_id!r} exists")
        conn.execute(
            insert(t.sites).values(
                id=site_id,
                name=name,
                default_site_role=default_site_role,
                settings=dict(settings or {}),
                created_at=now(),
            )
        )
    return DbSite(engine, site_id)


def import_site(engine: Engine, site: Site, *, by: str | None = None) -> DbSite:
    """Copy a site file (users, groups, projects, rules, dashboards, models) into a new database site."""
    settings: dict[str, Any] = {}
    if site.query.athena_workgroup:
        settings["athenaWorkgroup"] = site.query.athena_workgroup
    if site.query.daily_scan_budget_bytes is not None:
        settings["dailyScanBudgetBytes"] = site.query.daily_scan_budget_bytes
    if site.query.max_concurrent_queries is not None:
        settings["maxConcurrentQueries"] = site.query.max_concurrent_queries
    db = create_site(engine, site.id, site.id, default_site_role=site.default_role.value, settings=settings)
    members: dict[str, list[str]] = {}
    for user in site.users.values():
        db.ensure_user(user.username, user.fullname, user.site_role)
        for group in user.groups:
            members.setdefault(group, []).append(user.username)
    for group, usernames in sorted(members.items()):
        db.set_members(group, usernames)

    def known(rules: tuple[Rule, ...]) -> tuple[Rule, ...]:
        for rule in rules:
            if rule.user is not None and rule.user not in site.users:
                db.ensure_user(rule.user, rule.user)
        return rules

    pending = dict(site.projects)
    while pending:  # parents before children
        ready = [p for p in pending.values() if p.parent is None or p.parent not in pending]
        if not ready:
            raise ContentError("project parents form a cycle")
        for project in ready:
            if project.owner and project.owner not in site.users:
                db.ensure_user(project.owner, project.owner)
            db.create_project(
                project.id,
                project.name,
                parent=project.parent,
                owner=project.owner,
                asset_permissions=project.asset_permissions,
            )
            for content_type, rules in project.defaults.items():
                db.set_rules("project", project.id, known(rules), content_type=content_type)
            del pending[project.id]
    for entry in site.dashboards.values():
        bundle = site.bundle(entry.id)
        if entry.owner and entry.owner not in site.users:
            db.ensure_user(entry.owner, entry.owner)
        db.publish(
            "dashboard",
            entry.id,
            bundle.manifest,
            files={"index.html": bundle.html},
            project=entry.project,
            owner=entry.owner,
            message="imported from the site file",
            by=by,
        )
        db.set_rules("dashboard", entry.id, known(entry.rules))
    for model_id, model in site.models.items():
        model_entry = site.model_entries.get(model_id)
        owner = model_entry.owner if model_entry else None
        if owner and owner not in site.users:
            db.ensure_user(owner, owner)
        db.publish(
            "model",
            model_id,
            model,
            name=str(model.get("title", model_id)),
            project=model_entry.project if model_entry else None,
            owner=owner,
            data_version=model_entry.data_version if model_entry else "",
            message="imported from the site file",
            by=by,
        )
        db.set_rules("model", model_id, known(model_entry.rules if model_entry else ()))
    return db
