"""Stores on the metadata database (P3.1, P3.2): the site's content and permissions, sessions,
personal access tokens, local accounts and the audit log.

:class:`DbSite` has the same read interface as the Phase 1 file-backed ``content.Site``, so the portal
and the query service do not care where content lives. Reads use a short-lived snapshot of the site
(projects, rules, content heads, users), rebuilt after any write through this process and at most
``snapshot_ttl_s`` old otherwise, so a permission change made elsewhere applies within that time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, and_, delete, func, insert, select, update

from tabfree_api.auth import FLOW_TTL_S, SessionInfo
from tabfree_api.authz import (
    Capability,
    ContentEntry,
    Principal,
    Project,
    Rule,
    SiteRole,
    evaluate,
    max_role,
)
from tabfree_api.content import Bundle, ContentError, SiteQueryConfig, SiteUser
from tabfree_api.db import schema as t


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes; everything stored is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def document_hash(document: Mapping[str, Any], files: Mapping[str, str]) -> str:
    payload = json.dumps({"document": document, "files": files}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Revision:
    number: int
    document: Mapping[str, Any]
    files: Mapping[str, str]
    sha256: str
    message: str
    created_by: str | None
    created_at: dt.datetime


@dataclass
class _Snapshot:
    taken: float
    name: str
    default_role: SiteRole
    query: SiteQueryConfig
    projects: dict[str, Project]
    dashboards: dict[str, ContentEntry]
    model_entries: dict[str, ContentEntry]
    users: dict[str, SiteUser]


class DbSite:
    """One site's content and permissions in the database (read side plus the writes admins make)."""

    def __init__(self, engine: Engine, site_id: str, *, snapshot_ttl_s: float = 2.0) -> None:
        self.engine = engine
        self.id = site_id
        self.snapshot_ttl_s = snapshot_ttl_s
        self._snapshot: _Snapshot | None = None
        self._lock = threading.Lock()
        self.models: Mapping[str, Mapping[str, Any]] = _Models(self)

    # ---- snapshot ------------------------------------------------------------------------------

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None

    def _snap(self) -> _Snapshot:
        with self._lock:
            snap = self._snapshot
            if snap is not None and time.monotonic() - snap.taken < self.snapshot_ttl_s:
                return snap
        snap = self._load()
        with self._lock:
            self._snapshot = snap
        return snap

    def _load(self) -> _Snapshot:
        with self.engine.connect() as conn:
            site = conn.execute(select(t.sites).where(t.sites.c.id == self.id)).mappings().first()
            if site is None:
                raise ContentError(f"no site {self.id!r} in the database")
            usernames = dict(conn.execute(select(t.users.c.id, t.users.c.username)).all())
            group_names = dict(
                conn.execute(
                    select(t.groups.c.id, t.groups.c.name).where(t.groups.c.site_id == self.id)
                ).all()
            )
            rules: dict[tuple[str, str, str | None], list[Rule]] = {}
            grouped: dict[tuple[str, str, str | None, str | None, str | None], dict[Capability, Any]] = {}
            for r in conn.execute(
                select(t.permission_rules).where(t.permission_rules.c.site_id == self.id)
            ).mappings():
                key = (r["resource_kind"], r["resource_id"], r["content_type"], r["user_id"], r["group_id"])
                grouped.setdefault(key, {})[Capability(r["capability"])] = r["decision"]
            for (kind, rid, ctype, uid, gid), caps in grouped.items():
                rule = Rule(usernames.get(uid) if uid else None, group_names.get(gid) if gid else None, caps)
                rules.setdefault((kind, rid, ctype), []).append(rule)
            projects: dict[str, Project] = {}
            for p in conn.execute(select(t.projects).where(t.projects.c.site_id == self.id)).mappings():
                defaults = {
                    ctype: tuple(rs)
                    for (kind, rid, ctype), rs in rules.items()
                    if kind == "project" and rid == p["id"] and ctype
                }
                projects[p["id"]] = Project(
                    p["id"],
                    p["name"],
                    p["asset_permissions"],
                    defaults,
                    usernames.get(p["owner_id"]) if p["owner_id"] else None,
                    p["parent_id"],
                )
            dashboards: dict[str, ContentEntry] = {}
            models: dict[str, ContentEntry] = {}
            rows = conn.execute(
                select(t.content).where(t.content.c.site_id == self.id, t.content.c.deleted_at.is_(None))
            ).mappings()
            for c in rows:
                entry = ContentEntry(
                    c["id"],
                    c["project_id"],
                    usernames.get(c["owner_id"]) if c["owner_id"] else None,
                    tuple(rules.get((c["kind"], c["id"], None), ())),
                    c["kind"],
                    c["data_version"],
                )
                if c["kind"] == "dashboard":
                    dashboards[c["id"]] = entry
                elif c["kind"] == "model":
                    models[c["id"]] = entry
            memberships: dict[str, set[str]] = {}
            minimum: dict[str, SiteRole] = {}
            for uid, gid in conn.execute(
                select(t.group_members.c.user_id, t.group_members.c.group_id).where(
                    t.group_members.c.group_id.in_(list(group_names))
                )
            ):
                memberships.setdefault(uid, set()).add(group_names[gid])
            for g in conn.execute(select(t.groups).where(t.groups.c.site_id == self.id)).mappings():
                if g["minimum_site_role"]:
                    minimum[g["name"]] = SiteRole(g["minimum_site_role"])
            users: dict[str, SiteUser] = {}
            for u in conn.execute(
                select(t.users, t.site_users.c.site_role)
                .join(t.site_users, t.site_users.c.user_id == t.users.c.id)
                .where(t.site_users.c.site_id == self.id, t.users.c.disabled.is_(False))
            ).mappings():
                groups = frozenset(memberships.get(u["id"], set()))
                role = max_role(SiteRole(u["site_role"]), *(minimum[g] for g in groups if g in minimum))
                users[u["username"]] = SiteUser(
                    u["username"],
                    u["fullname"] or u["username"],
                    role,
                    groups,
                    dict(u["attributes"] or {}),
                    u["email"] or "",
                )
        settings = site["settings"] or {}
        return _Snapshot(
            time.monotonic(),
            site["name"],
            SiteRole(site["default_site_role"]),
            SiteQueryConfig(
                athena_workgroup=settings.get("athenaWorkgroup"),
                daily_scan_budget_bytes=settings.get("dailyScanBudgetBytes"),
                max_concurrent_queries=settings.get("maxConcurrentQueries"),
            ),
            projects,
            dashboards,
            models,
            users,
        )

    # ---- the content.Site read interface ----------------------------------------------------------

    @property
    def name(self) -> str:
        return self._snap().name

    @property
    def default_role(self) -> SiteRole:
        return self._snap().default_role

    @property
    def query(self) -> SiteQueryConfig:
        return self._snap().query

    @property
    def projects(self) -> dict[str, Project]:
        return self._snap().projects

    @property
    def dashboards(self) -> dict[str, ContentEntry]:
        return self._snap().dashboards

    @property
    def model_entries(self) -> dict[str, ContentEntry]:
        return self._snap().model_entries

    @property
    def users(self) -> dict[str, SiteUser]:
        return self._snap().users

    def site_role(self, username: str) -> SiteRole:
        user = self.users.get(username)
        return user.site_role if user else self.default_role

    def resolve(
        self, username: str, fullname: str, idp_groups: frozenset[str]
    ) -> tuple[str, frozenset[str], SiteRole]:
        """Who signs in: identity-provider groups plus local memberships, and the site role.

        A user the site does not know gets the site's default role (Tableau's grant role on sign-in)
        and is added to the site.
        """
        snap = self._snap()
        user = snap.users.get(username)
        if user is None:
            self.ensure_user(username, fullname, snap.default_role)
            groups = idp_groups
            role = snap.default_role
        else:
            groups = idp_groups | user.groups
            role = user.site_role
        with self.engine.connect() as conn:
            minimum = {
                name: SiteRole(level)
                for name, level in conn.execute(
                    select(t.groups.c.name, t.groups.c.minimum_site_role).where(
                        t.groups.c.site_id == self.id, t.groups.c.minimum_site_role.is_not(None)
                    )
                )
            }
        role = max_role(role, *(minimum[g] for g in groups if g in minimum))
        with self.engine.begin() as conn:
            conn.execute(update(t.users).where(t.users.c.username == username).values(last_sign_in_at=now()))
        return (user.fullname if user and not fullname else fullname or username), groups, role

    def can(self, principal: Principal, capability: Capability, dashboard: str) -> bool:
        entry = self.dashboards.get(dashboard)
        if entry is None:
            return False
        return evaluate(principal, capability, entry, self.projects).allowed

    def can_model(self, principal: Principal, capability: Capability, model: str) -> bool:
        entry = self.model_entries.get(model)
        if entry is None:
            return False
        return evaluate(principal, capability, entry, self.projects).allowed

    def data_version(self, model: str) -> str:
        entry = self.model_entries.get(model)
        return entry.data_version if entry else ""

    def visible(self, principal: Principal) -> list[dict[str, str]]:
        snap = self._snap()
        heads = self._heads("dashboard")
        out = []
        for entry in snap.dashboards.values():
            if evaluate(principal, Capability.VIEW, entry, snap.projects).allowed:
                project = snap.projects.get(entry.project or "")
                out.append(
                    {
                        "id": entry.id,
                        "title": heads.get(entry.id, entry.id),
                        "project": project.name if project else "",
                    }
                )
        return sorted(out, key=lambda d: (d["project"], d["title"]))

    def bundle(self, dashboard: str) -> Bundle:
        if dashboard not in self.dashboards:
            raise ContentError(f"no dashboard {dashboard!r}")
        revision = self.revision("dashboard", dashboard)
        return Bundle(revision.document, str(revision.files.get("index.html", "")))

    def _heads(self, kind: str) -> dict[str, str]:
        with self.engine.connect() as conn:
            return dict(
                conn.execute(
                    select(t.content.c.id, t.content.c.name).where(
                        t.content.c.site_id == self.id,
                        t.content.c.kind == kind,
                        t.content.c.deleted_at.is_(None),
                    )
                ).all()
            )

    # ---- revisions -----------------------------------------------------------------------------------

    def revision(self, kind: str, content_id: str, number: int | None = None) -> Revision:
        r = t.content_revisions
        with self.engine.connect() as conn:
            if number is None:
                number = conn.execute(
                    select(t.content.c.current_revision).where(
                        t.content.c.site_id == self.id, t.content.c.kind == kind, t.content.c.id == content_id
                    )
                ).scalar()
                if number is None:
                    raise ContentError(f"no {kind} {content_id!r}")
            row = (
                conn.execute(
                    select(r, t.users.c.username)
                    .outerjoin(t.users, t.users.c.id == r.c.created_by)
                    .where(
                        r.c.site_id == self.id,
                        r.c.kind == kind,
                        r.c.content_id == content_id,
                        r.c.number == number,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ContentError(f"no revision {number} of {kind} {content_id!r}")
        return Revision(
            row["number"],
            row["document"],
            row["files"] or {},
            row["sha256"],
            row["message"],
            row["username"],
            _aware(row["created_at"]),
        )

    def revisions(self, kind: str, content_id: str) -> list[Revision]:
        r = t.content_revisions
        with self.engine.connect() as conn:
            numbers = conn.execute(
                select(r.c.number)
                .where(r.c.site_id == self.id, r.c.kind == kind, r.c.content_id == content_id)
                .order_by(r.c.number)
            ).scalars()
            wanted = list(numbers)
        return [self.revision(kind, content_id, n) for n in wanted]

    # ---- writes ----------------------------------------------------------------------------------------

    def _user_id(self, conn: Any, username: str | None) -> str | None:
        if username is None:
            return None
        uid = conn.execute(select(t.users.c.id).where(t.users.c.username == username)).scalar()
        if uid is None:
            raise ContentError(f"no user {username!r}")
        return str(uid)

    def ensure_user(
        self,
        username: str,
        fullname: str = "",
        site_role: SiteRole | None = None,
        *,
        email: str | None = None,
        auth_source: str = "idp",
        attributes: Mapping[str, str] | None = None,
    ) -> str:
        """The user's id; creates the user, and their membership of this site, when missing."""
        with self.engine.begin() as conn:
            uid = conn.execute(select(t.users.c.id).where(t.users.c.username == username)).scalar()
            if uid is None:
                uid = str(uuid.uuid4())
                conn.execute(
                    insert(t.users).values(
                        id=uid,
                        username=username,
                        fullname=fullname,
                        email=email,
                        auth_source=auth_source,
                        attributes=dict(attributes or {}),
                        created_at=now(),
                    )
                )
            else:
                changes: dict[str, Any] = {}
                if attributes is not None:
                    changes["attributes"] = dict(attributes)
                if email is not None:
                    changes["email"] = email
                if changes:
                    conn.execute(update(t.users).where(t.users.c.id == uid).values(changes))
            exists = conn.execute(
                select(t.site_users.c.site_role).where(
                    t.site_users.c.site_id == self.id, t.site_users.c.user_id == uid
                )
            ).scalar()
            if exists is None:
                role = site_role or SiteRole(
                    conn.execute(
                        select(t.sites.c.default_site_role).where(t.sites.c.id == self.id)
                    ).scalar_one()
                )
                conn.execute(insert(t.site_users).values(site_id=self.id, user_id=uid, site_role=role.value))
            elif site_role is not None and exists != site_role.value:
                conn.execute(
                    update(t.site_users)
                    .where(t.site_users.c.site_id == self.id, t.site_users.c.user_id == uid)
                    .values(site_role=site_role.value)
                )
        self.invalidate()
        return str(uid)

    def ensure_group(
        self, name: str, *, source: str = "local", minimum_site_role: SiteRole | None = None
    ) -> str:
        with self.engine.begin() as conn:
            gid = conn.execute(
                select(t.groups.c.id).where(t.groups.c.site_id == self.id, t.groups.c.name == name)
            ).scalar()
            if gid is None:
                gid = str(uuid.uuid4())
                conn.execute(
                    insert(t.groups).values(
                        id=gid,
                        site_id=self.id,
                        name=name,
                        source=source,
                        minimum_site_role=minimum_site_role.value if minimum_site_role else None,
                    )
                )
        self.invalidate()
        return str(gid)

    def set_members(self, group: str, usernames: Sequence[str]) -> None:
        gid = self.ensure_group(group)
        with self.engine.begin() as conn:
            conn.execute(delete(t.group_members).where(t.group_members.c.group_id == gid))
            for username in usernames:
                uid = self._user_id(conn, username)
                conn.execute(insert(t.group_members).values(group_id=gid, user_id=uid))
        self.invalidate()

    def create_project(
        self,
        project_id: str,
        name: str,
        *,
        parent: str | None = None,
        owner: str | None = None,
        asset_permissions: str = "customizable",
        description: str = "",
    ) -> None:
        with self.engine.begin() as conn:
            if parent is not None:
                exists = conn.execute(
                    select(t.projects.c.id).where(t.projects.c.site_id == self.id, t.projects.c.id == parent)
                ).scalar()
                if exists is None:
                    raise ContentError(f"no parent project {parent!r}")
            conn.execute(
                insert(t.projects).values(
                    site_id=self.id,
                    id=project_id,
                    parent_id=parent,
                    name=name,
                    description=description,
                    owner_id=self._user_id(conn, owner),
                    asset_permissions=asset_permissions,
                    created_at=now(),
                )
            )
            if parent is not None:
                # Tableau: a nested project inherits its default rules from its parent when created.
                parent_rules = (
                    conn.execute(
                        select(t.permission_rules).where(
                            t.permission_rules.c.site_id == self.id,
                            t.permission_rules.c.resource_kind == "project",
                            t.permission_rules.c.resource_id == parent,
                            t.permission_rules.c.capability != Capability.PROJECT_LEADER.value,
                        )
                    )
                    .mappings()
                    .all()
                )
                for r in parent_rules:
                    conn.execute(
                        insert(t.permission_rules).values(
                            {k: v for k, v in r.items() if k != "id"} | {"resource_id": project_id}
                        )
                    )
        self.invalidate()

    def set_rules(
        self, resource_kind: str, resource_id: str, rules: Sequence[Rule], *, content_type: str | None = None
    ) -> None:
        """Replace the rules on a resource (a project's defaults for ``content_type``, or content)."""
        if resource_kind == "project" and content_type is None:
            raise ContentError("project rules need a content type (project, dashboard, model, connection)")
        with self.engine.begin() as conn:
            group_ids = dict(
                conn.execute(
                    select(t.groups.c.name, t.groups.c.id).where(t.groups.c.site_id == self.id)
                ).all()
            )
            ctype = t.permission_rules.c.content_type
            conn.execute(
                delete(t.permission_rules).where(
                    t.permission_rules.c.site_id == self.id,
                    t.permission_rules.c.resource_kind == resource_kind,
                    t.permission_rules.c.resource_id == resource_id,
                    ctype.is_(None) if content_type is None else ctype == content_type,
                )
            )
            for rule in rules:
                if rule.group is not None and rule.group not in group_ids:
                    group_ids[rule.group] = self._new_group(conn, rule.group)
                for capability, decision in rule.capabilities.items():
                    conn.execute(
                        insert(t.permission_rules).values(
                            site_id=self.id,
                            resource_kind=resource_kind,
                            resource_id=resource_id,
                            content_type=content_type,
                            user_id=self._user_id(conn, rule.user),
                            group_id=group_ids[rule.group] if rule.group else None,
                            capability=str(capability),
                            decision=decision,
                        )
                    )
        self.invalidate()

    def _new_group(self, conn: Any, name: str) -> str:
        gid = str(uuid.uuid4())
        conn.execute(insert(t.groups).values(id=gid, site_id=self.id, name=name, source="local"))
        return gid

    def publish(
        self,
        kind: str,
        content_id: str,
        document: Mapping[str, Any],
        *,
        files: Mapping[str, str] | None = None,
        name: str | None = None,
        project: str | None = None,
        owner: str | None = None,
        message: str = "",
        by: str | None = None,
        data_version: str | None = None,
        origin: Mapping[str, Any] | None = None,
    ) -> int:
        """Add an immutable revision and make it current; returns its number.

        New content in a project gets the project's default rules for its kind (Tableau copies the
        defaults on publish). Publishing identical content again returns the current revision.
        """
        files = dict(files or {})
        digest = document_hash(document, files)
        when = now()
        c = t.content
        key = and_(c.c.site_id == self.id, c.c.kind == kind, c.c.id == content_id)
        with self.engine.begin() as conn:
            head = conn.execute(select(c.c.current_revision, c.c.project_id).where(key)).first()
            if head is None:
                conn.execute(
                    insert(c).values(
                        site_id=self.id,
                        kind=kind,
                        id=content_id,
                        project_id=project,
                        name=name or str(document.get("title", content_id)),
                        owner_id=self._user_id(conn, owner or by),
                        data_version=data_version or "",
                        origin=dict(origin) if origin else None,
                        created_at=when,
                        updated_at=when,
                    )
                )
                number = 1
                if project is not None:
                    for r in (
                        conn.execute(
                            select(t.permission_rules).where(
                                t.permission_rules.c.site_id == self.id,
                                t.permission_rules.c.resource_kind == "project",
                                t.permission_rules.c.resource_id == project,
                                t.permission_rules.c.content_type == kind,
                            )
                        )
                        .mappings()
                        .all()
                    ):
                        values = {k: v for k, v in r.items() if k != "id"}
                        values.update(resource_kind=kind, resource_id=content_id, content_type=None)
                        conn.execute(insert(t.permission_rules).values(values))
            else:
                current = head[0]
                if current is not None:
                    same = conn.execute(
                        select(t.content_revisions.c.sha256).where(
                            t.content_revisions.c.site_id == self.id,
                            t.content_revisions.c.kind == kind,
                            t.content_revisions.c.content_id == content_id,
                            t.content_revisions.c.number == current,
                        )
                    ).scalar()
                    if same == digest:
                        return int(current)
                number = (
                    int(
                        conn.execute(
                            select(func.coalesce(func.max(t.content_revisions.c.number), 0)).where(
                                t.content_revisions.c.site_id == self.id,
                                t.content_revisions.c.kind == kind,
                                t.content_revisions.c.content_id == content_id,
                            )
                        ).scalar_one()
                    )
                    + 1
                )
            conn.execute(
                insert(t.content_revisions).values(
                    site_id=self.id,
                    kind=kind,
                    content_id=content_id,
                    number=number,
                    document=dict(document),
                    files=files,
                    sha256=digest,
                    message=message,
                    created_by=self._user_id(conn, by),
                    created_at=when,
                )
            )
            head_values: dict[str, Any] = {"current_revision": number, "updated_at": when}
            if name:
                head_values["name"] = name
            if data_version is not None:
                head_values["data_version"] = data_version
            conn.execute(update(c).where(key).values(head_values))
        self.invalidate()
        return number

    def restore(self, kind: str, content_id: str, number: int, *, by: str | None = None) -> int:
        """Make an old revision current again (as a new revision, so history stays append-only)."""
        old = self.revision(kind, content_id, number)
        return self.publish(
            kind, content_id, old.document, files=old.files, message=f"restore revision {number}", by=by
        )

    def move(self, kind: str, content_id: str, project: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.content)
                .where(t.content.c.site_id == self.id, t.content.c.kind == kind, t.content.c.id == content_id)
                .values(project_id=project, updated_at=now())
            )
        self.invalidate()

    def soft_delete(self, kind: str, content_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.content)
                .where(t.content.c.site_id == self.id, t.content.c.kind == kind, t.content.c.id == content_id)
                .values(deleted_at=now())
            )
        self.invalidate()

    def bump_data_version(self, model: str, version: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.content)
                .where(t.content.c.site_id == self.id, t.content.c.kind == "model", t.content.c.id == model)
                .values(data_version=version, updated_at=now())
            )
        self.invalidate()

    # ---- portal: listings, favorites, recents (P3.5) -------------------------------------------------

    def content_info(self, kind: str) -> dict[str, dict[str, Any]]:
        """Names, descriptions, owners, update times and origins of live content of one kind."""
        c = t.content
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(c, t.users.c.username)
                .outerjoin(t.users, t.users.c.id == c.c.owner_id)
                .where(c.c.site_id == self.id, c.c.kind == kind, c.c.deleted_at.is_(None))
            ).mappings()
            return {
                r["id"]: {
                    "name": r["name"],
                    "description": r["description"],
                    "owner": r["username"],
                    "updatedAt": _aware(r["updated_at"]).isoformat(),
                    "revision": r["current_revision"],
                    "origin": r["origin"],
                }
                for r in rows
            }

    def favorites(self, username: str) -> list[tuple[str, str]]:
        f = t.favorites
        with self.engine.connect() as conn:
            uid = self._user_id(conn, username)
            return [
                (k, cid)
                for k, cid in conn.execute(
                    select(f.c.kind, f.c.content_id)
                    .where(f.c.site_id == self.id, f.c.user_id == uid)
                    .order_by(f.c.created_at.desc())
                )
            ]

    def set_favorite(self, username: str, kind: str, content_id: str, on: bool) -> None:
        f = t.favorites
        with self.engine.begin() as conn:
            uid = self._user_id(conn, username)
            key = and_(
                f.c.site_id == self.id, f.c.user_id == uid, f.c.kind == kind, f.c.content_id == content_id
            )
            exists = conn.execute(select(f.c.kind).where(key)).first() is not None
            if on and not exists:
                conn.execute(
                    insert(f).values(
                        site_id=self.id, user_id=uid, kind=kind, content_id=content_id, created_at=now()
                    )
                )
            elif not on and exists:
                conn.execute(delete(f).where(key))

    def recents(self, username: str, limit: int = 20) -> list[tuple[str, str]]:
        """Content this user viewed most recently, from the audit log."""
        a = t.audit_events
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(a.c.resource_kind, a.c.resource_id, func.max(a.c.id).label("last"))
                .where(
                    a.c.site_id == self.id, a.c.actor == username, a.c.action == "view", a.c.outcome == "ok"
                )
                .group_by(a.c.resource_kind, a.c.resource_id)
                .order_by(func.max(a.c.id).desc())
                .limit(limit)
            )
            return [(str(k), str(cid)) for k, cid, _ in rows]

    # ---- admin (P3.5) ------------------------------------------------------------------------------------

    def admin_users(self) -> list[dict[str, Any]]:
        """Everyone on the site, disabled users included, with groups and last sign-in."""
        snap = self._snap()
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t.users, t.site_users.c.site_role)
                .join(t.site_users, t.site_users.c.user_id == t.users.c.id)
                .where(t.site_users.c.site_id == self.id)
                .order_by(t.users.c.username)
            ).mappings()
            out = []
            for u in rows:
                live = snap.users.get(u["username"])
                out.append(
                    {
                        "username": u["username"],
                        "fullname": u["fullname"],
                        "email": u["email"],
                        "siteRole": u["site_role"],
                        "effectiveSiteRole": live.site_role.value if live else u["site_role"],
                        "authSource": u["auth_source"],
                        "disabled": bool(u["disabled"]),
                        "groups": sorted(live.groups) if live else [],
                        "attributes": u["attributes"] or {},
                        "lastSignInAt": _aware(u["last_sign_in_at"]).isoformat()
                        if u["last_sign_in_at"]
                        else None,
                    }
                )
        return out

    def set_user(
        self,
        username: str,
        *,
        site_role: SiteRole | None = None,
        disabled: bool | None = None,
        fullname: str | None = None,
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            uid = self._user_id(conn, username)
            if site_role is not None:
                conn.execute(
                    update(t.site_users)
                    .where(t.site_users.c.site_id == self.id, t.site_users.c.user_id == uid)
                    .values(site_role=site_role.value)
                )
            changes: dict[str, Any] = {}
            if disabled is not None:
                changes["disabled"] = disabled
            if fullname is not None:
                changes["fullname"] = fullname
            if attributes is not None:
                changes["attributes"] = dict(attributes)
            if changes:
                conn.execute(update(t.users).where(t.users.c.id == uid).values(changes))
        self.invalidate()

    def admin_groups(self) -> list[dict[str, Any]]:
        snap = self._snap()
        members: dict[str, list[str]] = {}
        for user in snap.users.values():
            for g in user.groups:
                members.setdefault(g, []).append(user.username)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t.groups).where(t.groups.c.site_id == self.id).order_by(t.groups.c.name)
            )
            return [
                {
                    "name": g.name,
                    "source": g.source,
                    "minimumSiteRole": g.minimum_site_role,
                    "members": sorted(members.get(g.name, [])),
                }
                for g in rows
            ]

    def delete_group(self, name: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                delete(t.groups).where(t.groups.c.site_id == self.id, t.groups.c.name == name)
            )
        self.invalidate()
        return bool(result.rowcount)

    def update_site(
        self,
        *,
        name: str | None = None,
        default_site_role: SiteRole | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> None:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if default_site_role is not None:
            changes["default_site_role"] = default_site_role.value
        if settings is not None:
            changes["settings"] = dict(settings)
        if changes:
            with self.engine.begin() as conn:
                conn.execute(update(t.sites).where(t.sites.c.id == self.id).values(changes))
        self.invalidate()

    def settings(self) -> dict[str, Any]:
        with self.engine.connect() as conn:
            value = conn.execute(select(t.sites.c.settings).where(t.sites.c.id == self.id)).scalar()
        return dict(value or {})

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        asset_permissions: str | None = None,
        owner: str | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            changes: dict[str, Any] = {}
            if name is not None:
                changes["name"] = name
            if description is not None:
                changes["description"] = description
            if asset_permissions is not None:
                changes["asset_permissions"] = asset_permissions
            if owner is not None:
                changes["owner_id"] = self._user_id(conn, owner)
            if changes:
                conn.execute(
                    update(t.projects)
                    .where(t.projects.c.site_id == self.id, t.projects.c.id == project_id)
                    .values(changes)
                )
        self.invalidate()

    def delete_project(self, project_id: str) -> None:
        """Refused while the project (or a nested one) still holds live content."""
        nested = {
            p.id for p in self.projects.values() if project_id in {a.id for a in _chain(p.id, self.projects)}
        }
        with self.engine.begin() as conn:
            live = conn.execute(
                select(func.count())
                .select_from(t.content)
                .where(
                    t.content.c.site_id == self.id,
                    t.content.c.project_id.in_(sorted(nested)),
                    t.content.c.deleted_at.is_(None),
                )
            ).scalar_one()
            if live:
                raise ContentError("move or delete the project's content first")
            conn.execute(
                delete(t.permission_rules).where(
                    t.permission_rules.c.site_id == self.id,
                    t.permission_rules.c.resource_kind == "project",
                    t.permission_rules.c.resource_id.in_(sorted(nested)),
                )
            )
            conn.execute(
                delete(t.projects).where(t.projects.c.site_id == self.id, t.projects.c.id == project_id)
            )
        self.invalidate()

    # ---- old Tableau URLs (P3.9) --------------------------------------------------------------------------

    def set_legacy_url(self, path: str, kind: str, content_id: str) -> None:
        lu = t.legacy_urls
        with self.engine.begin() as conn:
            conn.execute(delete(lu).where(lu.c.site_id == self.id, lu.c.path == path))
            conn.execute(insert(lu).values(site_id=self.id, path=path, kind=kind, content_id=content_id))

    def legacy_urls(self) -> list[dict[str, str]]:
        lu = t.legacy_urls
        with self.engine.connect() as conn:
            rows = conn.execute(select(lu).where(lu.c.site_id == self.id).order_by(lu.c.path)).mappings()
            return [{"path": r["path"], "kind": r["kind"], "id": r["content_id"]} for r in rows]

    def resolve_legacy(self, path: str) -> tuple[str, str] | None:
        lu = t.legacy_urls
        with self.engine.connect() as conn:
            row = conn.execute(
                select(lu.c.kind, lu.c.content_id).where(lu.c.site_id == self.id, lu.c.path == path)
            ).first()
        return (str(row[0]), str(row[1])) if row else None


def _chain(project: str, projects: Mapping[str, Project]) -> list[Project]:
    from tabfree_api.authz import ancestry

    return ancestry(project, projects)


class _Models(Mapping[str, Mapping[str, Any]]):
    """The site's semantic models (current revisions), for the query service."""

    def __init__(self, site: DbSite) -> None:
        self._site = site
        self._cache: dict[tuple[str, int], Mapping[str, Any]] = {}

    def _current(self) -> dict[str, int]:
        with self._site.engine.connect() as conn:
            return {
                cid: int(rev)
                for cid, rev in conn.execute(
                    select(t.content.c.id, t.content.c.current_revision).where(
                        t.content.c.site_id == self._site.id,
                        t.content.c.kind == "model",
                        t.content.c.deleted_at.is_(None),
                        t.content.c.current_revision.is_not(None),
                    )
                )
            }

    def __getitem__(self, key: str) -> Mapping[str, Any]:
        number = self._current().get(key)
        if number is None:
            raise KeyError(key)
        if (key, number) not in self._cache:
            self._cache[(key, number)] = self._site.revision("model", key, number).document
        return self._cache[(key, number)]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._current()))

    def __len__(self) -> int:
        return len(self._current())


# ---- sessions, sign-in flows, personal access tokens ---------------------------------------------


class DbSessionStore:
    """Sessions and OIDC flows in the database (the ``auth.SessionStore`` interface), plus PATs."""

    def __init__(self, engine: Engine, site_id: str) -> None:
        self.engine = engine
        self.site_id = site_id

    def _uid(self, conn: Any, username: str) -> str:
        uid = conn.execute(select(t.users.c.id).where(t.users.c.username == username)).scalar()
        if uid is None:
            raise ContentError(f"no user {username!r}")
        return str(uid)

    def create(
        self,
        username: str,
        fullname: str,
        groups: frozenset[str],
        role: SiteRole,
        ttl_s: float,
        *,
        email: str = "",
        attributes: Mapping[str, str] | None = None,
        scopes: frozenset[str] | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        when = now()
        with self.engine.begin() as conn:
            conn.execute(delete(t.sessions).where(t.sessions.c.expires_at < when))
            conn.execute(
                insert(t.sessions).values(
                    token_sha256=_digest(token),
                    user_id=self._uid(conn, username),
                    site_id=self.site_id,
                    fullname=fullname,
                    groups=sorted(groups),
                    site_role=role.value,
                    email=email,
                    attributes=dict(attributes or {}),
                    scopes=sorted(scopes) if scopes is not None else None,
                    created_at=when,
                    expires_at=when + dt.timedelta(seconds=ttl_s),
                )
            )
        return token

    def get(self, token: str | None) -> SessionInfo | None:
        if not token or len(token) > 200:
            return None
        if token.startswith(PAT_PREFIX):
            return self.authenticate_pat(token)
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(t.sessions, t.users.c.username, t.users.c.disabled)
                    .join(t.users, t.users.c.id == t.sessions.c.user_id)
                    .where(t.sessions.c.token_sha256 == _digest(token))
                )
                .mappings()
                .first()
            )
        if row is None or row["disabled"] or _aware(row["expires_at"]) < now():
            return None
        return SessionInfo(
            row["username"],
            row["fullname"],
            frozenset(row["groups"]),
            SiteRole(row["site_role"]),
            _aware(row["expires_at"]).timestamp(),
            row["email"] or "",
            dict(row["attributes"] or {}),
            scopes=frozenset(row["scopes"]) if row["scopes"] is not None else None,
        )

    def delete(self, token: str | None) -> None:
        if token:
            with self.engine.begin() as conn:
                conn.execute(delete(t.sessions).where(t.sessions.c.token_sha256 == _digest(token)))

    def delete_user_sessions(self, username: str) -> None:
        """Sign a user out everywhere (disable, role change, password change)."""
        with self.engine.begin() as conn:
            conn.execute(delete(t.sessions).where(t.sessions.c.user_id == self._uid(conn, username)))

    def start_flow(self, return_to: str) -> tuple[str, str, str]:
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        when = now()
        with self.engine.begin() as conn:
            conn.execute(delete(t.oidc_flows).where(t.oidc_flows.c.expires_at < when))
            conn.execute(
                insert(t.oidc_flows).values(
                    state_sha256=_digest(state),
                    nonce=nonce,
                    verifier=verifier,
                    return_to=return_to,
                    expires_at=when + dt.timedelta(seconds=FLOW_TTL_S),
                )
            )
        return state, nonce, verifier

    def finish_flow(self, state: str) -> tuple[str, str, str] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(
                    t.oidc_flows.c.nonce,
                    t.oidc_flows.c.verifier,
                    t.oidc_flows.c.return_to,
                    t.oidc_flows.c.expires_at,
                ).where(t.oidc_flows.c.state_sha256 == _digest(state))
            ).first()
            conn.execute(delete(t.oidc_flows).where(t.oidc_flows.c.state_sha256 == _digest(state)))
        if row is None or _aware(row[3]) < now():
            return None
        return row[0], row[1], row[2]

    # ---- personal access tokens (Tableau PATs) -------------------------------------------------------

    def create_pat(self, username: str, name: str, *, days: int = 365) -> str:
        """A new token, shown once: ``tfp_<id>.<secret>``. Only the secret's hash is stored."""
        if not 1 <= len(name) <= 100:
            raise ContentError("token names are 1 to 100 characters")
        pat_id = str(uuid.uuid4())
        secret = secrets.token_urlsafe(32)
        when = now()
        with self.engine.begin() as conn:
            uid = self._uid(conn, username)
            taken = conn.execute(
                select(t.personal_access_tokens.c.id).where(
                    t.personal_access_tokens.c.user_id == uid,
                    t.personal_access_tokens.c.site_id == self.site_id,
                    t.personal_access_tokens.c.name == name,
                )
            ).scalar()
            if taken is not None:
                raise ContentError(f"a token named {name!r} exists; revoke it first")
            conn.execute(
                insert(t.personal_access_tokens).values(
                    id=pat_id,
                    user_id=uid,
                    site_id=self.site_id,
                    name=name,
                    secret_sha256=_digest(secret),
                    created_at=when,
                    expires_at=when + dt.timedelta(days=days),
                )
            )
        return f"{PAT_PREFIX}{pat_id}.{secret}"

    def authenticate_pat(self, token: str) -> SessionInfo | None:
        """A PAT is valid until it expires, is revoked, or goes unused for ``PAT_IDLE_DAYS``."""
        body = token[len(PAT_PREFIX) :]
        if "." not in body:
            return None
        _pat_id, secret = body.split(".", 1)
        p = t.personal_access_tokens
        when = now()
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(p, t.users.c.username, t.users.c.fullname, t.users.c.disabled)
                    .join(t.users, t.users.c.id == p.c.user_id)
                    .where(p.c.secret_sha256 == _digest(secret), p.c.site_id == self.site_id)
                )
                .mappings()
                .first()
            )
            if row is None or row["revoked_at"] is not None or row["disabled"]:
                return None
            last = _aware(row["last_used_at"] or row["created_at"])
            if _aware(row["expires_at"]) < when or when - last > dt.timedelta(days=PAT_IDLE_DAYS):
                return None
            conn.execute(update(p).where(p.c.id == row["id"]).values(last_used_at=when))
        return self._pat_session(row["username"], row["fullname"], _aware(row["expires_at"]))

    def _pat_session(self, username: str, fullname: str, expires: dt.datetime) -> SessionInfo | None:
        # Groups and the site role are read now, not frozen when the token was made.
        site = DbSite(self.engine, self.site_id, snapshot_ttl_s=0)
        user = site.users.get(username)
        if user is None:
            return None
        return SessionInfo(
            username,
            fullname or username,
            user.groups,
            user.site_role,
            expires.timestamp(),
            user.email,
            dict(user.attributes),
        )

    def list_pats(self, username: str) -> list[dict[str, Any]]:
        p = t.personal_access_tokens
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(p.c.id, p.c.name, p.c.created_at, p.c.expires_at, p.c.last_used_at, p.c.revoked_at)
                .where(p.c.user_id == self._uid(conn, username), p.c.site_id == self.site_id)
                .order_by(p.c.created_at)
            ).mappings()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "createdAt": _aware(r["created_at"]).isoformat(),
                    "expiresAt": _aware(r["expires_at"]).isoformat(),
                    "lastUsedAt": _aware(r["last_used_at"]).isoformat() if r["last_used_at"] else None,
                    "revoked": r["revoked_at"] is not None,
                }
                for r in rows
            ]

    def revoke_pat(self, username: str, pat_id: str) -> bool:
        p = t.personal_access_tokens
        with self.engine.begin() as conn:
            result = conn.execute(
                update(p)
                .where(p.c.id == pat_id, p.c.user_id == self._uid(conn, username), p.c.revoked_at.is_(None))
                .values(revoked_at=now())
            )
        return bool(result.rowcount)


PAT_PREFIX = "tfp_"
#: Tableau expires a PAT after 15 days without use.
PAT_IDLE_DAYS = 15


# ---- local accounts ------------------------------------------------------------------------------


class LocalAccounts:
    """Password sign-in for accounts outside the identity provider (break-glass admin, service users).

    argon2id hashes; a failed attempt costs the same time whether or not the user exists; five
    failures in fifteen minutes lock the account for fifteen minutes (per process).
    """

    MAX_FAILURES = 5
    WINDOW_S = 15 * 60

    def __init__(self, engine: Engine) -> None:
        from argon2 import PasswordHasher

        self.engine = engine
        self._hasher = PasswordHasher()
        self._dummy = self._hasher.hash(secrets.token_urlsafe(16))
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def set_password(self, username: str, password: str) -> None:
        if len(password) < 12:
            raise ContentError("passwords are at least 12 characters")
        with self.engine.begin() as conn:
            result = conn.execute(
                update(t.users)
                .where(t.users.c.username == username)
                .values(password_hash=self._hasher.hash(password), auth_source="local")
            )
        if not result.rowcount:
            raise ContentError(f"no user {username!r}")

    def locked(self, username: str) -> bool:
        cutoff = time.monotonic() - self.WINDOW_S
        with self._lock:
            recent = [f for f in self._failures.get(username, []) if f > cutoff]
            self._failures[username] = recent
            return len(recent) >= self.MAX_FAILURES

    def verify(self, username: str, password: str) -> bool:
        from argon2.exceptions import Argon2Error

        if self.locked(username):
            return False
        with self.engine.connect() as conn:
            row = conn.execute(
                select(t.users.c.password_hash, t.users.c.disabled, t.users.c.auth_source).where(
                    t.users.c.username == username
                )
            ).first()
        stored = row[0] if row is not None and row[2] == "local" and not row[1] else None
        try:
            ok = self._hasher.verify(stored or self._dummy, password) and stored is not None
        except Argon2Error:
            ok = False
        if ok and stored is not None and self._hasher.check_needs_rehash(stored):
            self.set_password(username, password)
        if not ok:
            with self._lock:
                self._failures.setdefault(username, []).append(time.monotonic())
        return ok


# ---- audit ---------------------------------------------------------------------------------------


class AuditLog:
    """Append-only audit events (P3.8). ``detail`` never carries data values."""

    def __init__(self, engine: Engine, site_id: str | None) -> None:
        self.engine = engine
        self.site_id = site_id

    def record(
        self,
        action: str,
        *,
        actor: str | None,
        outcome: str = "ok",
        resource_kind: str | None = None,
        resource_id: str | None = None,
        **detail: Any,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(t.audit_events).values(
                    at=now(),
                    site_id=self.site_id,
                    actor=actor,
                    action=action,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    outcome=outcome,
                    detail=detail,
                )
            )

    def events(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
        actor: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        a = t.audit_events
        query = select(a).where(a.c.site_id == self.site_id).order_by(a.c.id.desc()).limit(min(limit, 1000))
        if before is not None:
            query = query.where(a.c.id < before)
        if actor is not None:
            query = query.where(a.c.actor == actor)
        if action is not None:
            query = query.where(a.c.action == action)
        if resource_id is not None:
            query = query.where(a.c.resource_id == resource_id)
        with self.engine.connect() as conn:
            return [
                {
                    "id": r["id"],
                    "at": _aware(r["at"]).isoformat(),
                    "actor": r["actor"],
                    "action": r["action"],
                    "resourceKind": r["resource_kind"],
                    "resourceId": r["resource_id"],
                    "outcome": r["outcome"],
                    "detail": r["detail"],
                }
                for r in conn.execute(query).mappings()
            ]
