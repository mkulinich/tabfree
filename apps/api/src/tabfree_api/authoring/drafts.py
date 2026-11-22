"""Drafts and the two-step publish, in the metadata database.

A draft is a private working copy of a dashboard bundle. Every agent or editor change rewrites the
draft (never a revision); publishing turns it into a new immutable revision. Publishing takes two
steps: ``request_publish`` validates the draft and returns a short confirmation code; ``confirm``
needs that code within ten minutes, for exactly the files it was issued for. Any change in between
cancels the request.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, insert, select, update

from tabfree_api.authoring import files as bundle_files
from tabfree_api.db import schema as t

CONFIRM_TTL = dt.timedelta(minutes=10)
#: Open drafts one user may keep per site.
MAX_OPEN_DRAFTS = 50


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class DraftError(ValueError):
    pass


@dataclass(frozen=True)
class Draft:
    id: str
    content_id: str
    project: str | None
    base_revision: int | None
    files: Mapping[str, str]
    created_by: str
    agent_session: str | None
    status: str
    publish_pending: bool
    publish_expires_at: dt.datetime | None
    publish_message: str
    approved_by: str | None
    published_revision: int | None
    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def is_new(self) -> bool:
        return self.base_revision is None

    def to_dict(self, *, with_files: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "dashboard": self.content_id,
            "project": self.project,
            "baseRevision": self.base_revision,
            "new": self.is_new,
            "createdBy": self.created_by,
            "status": self.status,
            "publishPending": self.publish_pending,
            "approvedBy": self.approved_by,
            "publishedRevision": self.published_revision,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }
        if with_files:
            out["files"] = dict(self.files)
        return out


class DraftStore:
    def __init__(self, engine: Engine, site_id: str) -> None:
        self.engine = engine
        self.site_id = site_id

    def _uid(self, conn: Any, username: str) -> str:
        uid = conn.execute(select(t.users.c.id).where(t.users.c.username == username)).scalar()
        if uid is None:
            raise DraftError(f"no user {username!r}")
        return str(uid)

    def _select(self) -> Any:
        d = t.drafts
        return select(d, t.users.c.username).join(t.users, t.users.c.id == d.c.created_by)

    @staticmethod
    def _draft(row: Any) -> Draft:
        expires = _aware(row["publish_expires_at"])
        pending = row["publish_code_sha256"] is not None and expires is not None and expires > _now()
        created, updated = _aware(row["created_at"]), _aware(row["updated_at"])
        if created is None or updated is None:  # the columns are NOT NULL
            raise DraftError("corrupt draft row")
        return Draft(
            row["id"],
            row["content_id"],
            row["project_id"],
            row["base_revision"],
            dict(row["files"] or {}),
            row["username"],
            row["agent_session"],
            row["status"],
            pending,
            expires,
            row["publish_message"] or "",
            row["approved_by"],
            row["published_revision"],
            created,
            updated,
        )

    def create(
        self,
        username: str,
        dashboard: str,
        files: Mapping[str, str],
        *,
        base_revision: int | None,
        project: str | None = None,
        agent_session: str | None = None,
        max_open: int | None = MAX_OPEN_DRAFTS,
    ) -> Draft:
        """A new open draft. ``max_open=None`` lifts the per-person cap (the migration importer)."""
        bundle_files.check_limits(files)
        when = _now()
        draft_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            uid = self._uid(conn, username)
            open_count = len(
                conn.execute(
                    select(t.drafts.c.id).where(
                        t.drafts.c.site_id == self.site_id,
                        t.drafts.c.created_by == uid,
                        t.drafts.c.status == "open",
                    )
                ).all()
            )
            if max_open is not None and open_count >= max_open:
                raise DraftError(f"you have {open_count} open drafts; publish or discard some first")
            conn.execute(
                insert(t.drafts).values(
                    id=draft_id,
                    site_id=self.site_id,
                    kind="dashboard",
                    content_id=dashboard,
                    project_id=project,
                    base_revision=base_revision,
                    files=dict(files),
                    created_by=uid,
                    agent_session=agent_session,
                    status="open",
                    created_at=when,
                    updated_at=when,
                )
            )
        return self._must(draft_id)

    def _must(self, draft_id: str) -> Draft:
        draft = self.get(draft_id)
        if draft is None:
            raise DraftError(f"no draft {draft_id}")
        return draft

    def get(self, draft_id: str) -> Draft | None:
        try:
            uuid.UUID(draft_id)
        except ValueError:
            return None
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    self._select().where(t.drafts.c.id == draft_id, t.drafts.c.site_id == self.site_id)
                )
                .mappings()
                .first()
            )
        return self._draft(row) if row else None

    def list(self, username: str | None = None, *, dashboard: str | None = None) -> list[Draft]:
        query = self._select().where(t.drafts.c.site_id == self.site_id, t.drafts.c.status == "open")
        if username is not None:
            query = query.where(t.users.c.username == username)
        if dashboard is not None:
            query = query.where(t.drafts.c.content_id == dashboard)
        with self.engine.connect() as conn:
            rows = conn.execute(query.order_by(t.drafts.c.updated_at.desc())).mappings().all()
        return [self._draft(r) for r in rows]

    def update_files(self, draft_id: str, files: Mapping[str, str], *, agent_session: str | None) -> Draft:
        bundle_files.check_limits(files)
        with self.engine.begin() as conn:
            result = conn.execute(
                update(t.drafts)
                .where(t.drafts.c.id == draft_id, t.drafts.c.status == "open")
                .values(
                    files=dict(files),
                    agent_session=agent_session,
                    updated_at=_now(),
                    # A change cancels a pending publish request and any approval.
                    publish_code_sha256=None,
                    publish_files_sha256=None,
                    publish_expires_at=None,
                    approved_by=None,
                )
            )
        if not result.rowcount:
            raise DraftError("this draft is no longer open")
        return self._must(draft_id)

    def request_publish(self, draft_id: str, message: str) -> tuple[str, dt.datetime]:
        draft = self.get(draft_id)
        if draft is None or draft.status != "open":
            raise DraftError("this draft is no longer open")
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        expires = _now() + CONFIRM_TTL
        with self.engine.begin() as conn:
            conn.execute(
                update(t.drafts)
                .where(t.drafts.c.id == draft_id)
                .values(
                    publish_code_sha256=_sha(code),
                    publish_files_sha256=bundle_files.files_hash(draft.files),
                    publish_expires_at=expires,
                    publish_message=message[:500],
                )
            )
        return code, expires

    def check_code(self, draft_id: str, code: str) -> Draft:
        """The draft, if ``code`` confirms its pending publish request for its current files."""
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    self._select().where(t.drafts.c.id == draft_id, t.drafts.c.site_id == self.site_id)
                )
                .mappings()
                .first()
            )
        if row is None or row["status"] != "open":
            raise DraftError("this draft is no longer open")
        expected, expires = row["publish_code_sha256"], _aware(row["publish_expires_at"])
        if expected is None or expires is None:
            raise DraftError("request publishing first (request_publish)")
        if expires < _now():
            raise DraftError("the confirmation code has expired; request publishing again")
        if not hmac.compare_digest(expected, _sha(code.strip().upper())):
            raise DraftError("wrong confirmation code")
        draft = self._draft(row)
        if row["publish_files_sha256"] != bundle_files.files_hash(draft.files):
            raise DraftError("the draft changed after publishing was requested; request it again")
        return draft

    def approve(self, draft_id: str, approver: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.drafts)
                .where(t.drafts.c.id == draft_id, t.drafts.c.status == "open")
                .values(approved_by=approver)
            )

    def mark_published(self, draft_id: str, revision: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.drafts)
                .where(t.drafts.c.id == draft_id)
                .values(
                    status="published",
                    published_revision=revision,
                    publish_code_sha256=None,
                    updated_at=_now(),
                )
            )

    def discard(self, draft_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(t.drafts)
                .where(t.drafts.c.id == draft_id, t.drafts.c.status == "open")
                .values(status="discarded", publish_code_sha256=None, updated_at=_now())
            )
