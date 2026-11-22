"""Authoring operations with their permission checks: one place for the MCP tools, the portal and agents.

Every operation takes the caller's :class:`~tabfree_api.auth.SessionInfo`, so an agent acting for a
user can do exactly what that user can, and RLS filters every query it runs.

Tableau's capabilities decide (ADR-006):

- **Read:** content is listed and opened only with View. Model fields need View on the model; sample
  values and ad-hoc queries need Connect.
- **Drafts:** starting a draft of a dashboard needs Web Edit on it; a new dashboard needs Publish on
  the project. Drafts are private to the person who made them (and site administrators).
- **Publishing:** saving over a dashboard needs Overwrite; a new one needs Publish on the project. It
  takes two steps (request, then confirm with the code), and an agent can never confirm: the person
  does, in the portal or in their own MCP client. With the site's ``authoringApproval`` set to
  ``required``, a project leader, the project owner or a site administrator must approve first,
  unless they are the author.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tabfree_api.auth import SessionInfo
from tabfree_api.authoring import files as bundle_files
from tabfree_api.authoring.drafts import Draft, DraftError, DraftStore
from tabfree_api.authoring.fence import fence
from tabfree_api.authoring.validation import ValidationReport, validate
from tabfree_api.authz import Capability, ContentEntry, Project, SiteRole, ancestry, evaluate
from tabfree_api.content import ContentError
from tabfree_api.engines import EngineError, EngineTimeoutError
from tabfree_compiler import CompileError, Session

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
#: Rows of a governed query an agent sees (the full result stays in the dashboard).
AGENT_ROWS = 200
SAMPLE_DIMENSIONS = 12
SAMPLE_VALUES = 8
PREVIEW_QUERIES = 25


class AuthoringError(Exception):
    """A refused or failed operation. ``status`` is the HTTP status; the message is safe to show."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _not_found(what: str) -> AuthoringError:
    return AuthoringError(404, f"no {what}")


@dataclass(frozen=True)
class PublishRequest:
    draft: Draft
    code: str
    expires: dt.datetime
    report: ValidationReport
    diff: str


class AuthoringService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    # ---- plumbing ----------------------------------------------------------------------------------

    @property
    def site(self) -> Any:
        return self.ctx.site

    def drafts(self) -> DraftStore:
        site = self.site
        if not hasattr(site, "publish"):
            raise AuthoringError(
                501, "drafts and publishing need the metadata database (TABFREE_DATABASE_URL)"
            )
        store: DraftStore | None = getattr(self.ctx, "_draft_store", None)
        if store is None:
            store = DraftStore(site.engine, site.id)
            self.ctx._draft_store = store
        return store

    def session(self, info: SessionInfo) -> Session:
        return Session(
            username=info.username,
            fullname=info.fullname,
            email=info.email,
            groups=info.groups,
            attributes=dict(info.attributes),
            today=self.ctx.today,
        )

    def audit(self, info: SessionInfo, action: str, outcome: str = "ok", **detail: Any) -> None:
        if info.agent_session:
            detail["agent_session"] = info.agent_session
        self.ctx.audit(action, info.username, outcome, **detail)

    def _can(self, info: SessionInfo, capability: Capability, entry: ContentEntry | Project) -> bool:
        return evaluate(info.principal(), capability, entry, self.site.projects).allowed

    def _dashboard(self, info: SessionInfo, dashboard: str) -> ContentEntry:
        entry: ContentEntry | None = self.site.dashboards.get(dashboard)
        if entry is None or not self._can(info, Capability.VIEW, entry):
            raise _not_found(f"dashboard {dashboard!r}")  # 404, not 403: no probing
        return entry

    def _model_entry(self, info: SessionInfo, model: str) -> ContentEntry:
        from tabfree_api.authz import ModelEntry

        if model not in self.site.models:
            raise _not_found(f"model {model!r}")
        entry: ContentEntry = self.site.model_entries.get(model) or ModelEntry(model, None, ())
        if not self._can(info, Capability.VIEW, entry):
            raise _not_found(f"model {model!r}")
        return entry

    def _draft(self, info: SessionInfo, draft_id: str) -> Draft:
        draft = self.drafts().get(draft_id)
        mine = draft is not None and (
            draft.created_by == info.username or info.site_role is SiteRole.SITE_ADMINISTRATOR
        )
        if draft is None or not mine:
            raise _not_found(f"draft {draft_id!r}")
        return draft

    def _open(self, info: SessionInfo, draft_id: str) -> Draft:
        draft = self._draft(info, draft_id)
        if draft.status != "open":
            raise AuthoringError(409, f"draft {draft_id} is {draft.status}; start a new one")
        return draft

    def _published_files(self, dashboard: str, number: int | None = None) -> dict[str, str]:
        if hasattr(self.site, "revision"):
            revision = self.site.revision("dashboard", dashboard, number)
            return bundle_files.from_revision(revision.document, revision.files)
        bundle = self.site.bundle(dashboard)
        return bundle_files.from_revision(bundle.manifest, {bundle_files.HTML: bundle.html})

    def _published_models(self, dashboard: str) -> set[str]:
        if dashboard not in self.site.dashboards:
            return set()
        manifest = self.site.bundle(dashboard).manifest
        return {str(m) for m in (manifest.get("models") or {}).values()}

    def model_allowed(self, info: SessionInfo, draft: Draft) -> Any:
        """Which models a draft may use: the published dashboard's, or ones the author may query."""
        published = set() if draft.is_new else self._published_models(draft.content_id)

        def allowed(model: str) -> bool:
            if model in published:
                return True
            try:
                entry = self._model_entry(info, model)
            except AuthoringError:
                return False
            return self._can(info, Capability.CONNECT, entry)

        return allowed

    # ---- read ----------------------------------------------------------------------------------------

    def list_content(
        self,
        info: SessionInfo,
        *,
        kind: str | None = None,
        query: str | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        principal = info.principal()
        out: list[dict[str, Any]] = []
        sources: list[tuple[str, Mapping[str, ContentEntry]]] = []
        if kind in (None, "dashboard"):
            sources.append(("dashboard", self.site.dashboards))
        if kind in (None, "model"):
            sources.append(("model", self.site.model_entries))
        for item_kind, entries in sources:
            for entry in entries.values():
                if not evaluate(principal, Capability.VIEW, entry, self.site.projects).allowed:
                    continue
                if project is not None and project not in {
                    p.id for p in ancestry(entry.project, self.site.projects)
                }:
                    continue
                if item_kind == "dashboard":
                    manifest = self.site.bundle(entry.id).manifest
                    title, description = manifest.get("title", entry.id), manifest.get("description", "")
                else:
                    doc = self.site.models.get(entry.id, {})
                    title, description = doc.get("title", entry.id), doc.get("description", "")
                path = [p.name for p in reversed(ancestry(entry.project, self.site.projects))]
                caps = [
                    c.value
                    for c in (Capability.WEB_EDIT, Capability.OVERWRITE, Capability.CONNECT)
                    if evaluate(principal, c, entry, self.site.projects).allowed
                ]
                out.append(
                    {
                        "kind": item_kind,
                        "id": entry.id,
                        "title": str(title),
                        "description": str(description),
                        "project": entry.project,
                        "projectPath": path,
                        "youMay": caps,
                    }
                )
        if query:
            terms = query.casefold().split()
            out = [
                d
                for d in out
                if all(
                    t
                    in " ".join(
                        [d["id"], d["title"], d["description"], " ".join(d["projectPath"])]
                    ).casefold()
                    for t in terms
                )
            ]
        return sorted(out, key=lambda d: (d["kind"], d["projectPath"], d["title"].casefold()))

    def projects(self, info: SessionInfo) -> list[dict[str, Any]]:
        principal = info.principal()
        return [
            {
                "id": p.id,
                "name": p.name,
                "parent": p.parent,
                "canPublish": evaluate(principal, Capability.PUBLISH, p, self.site.projects).allowed,
            }
            for p in self.site.projects.values()
            if evaluate(principal, Capability.VIEW, p, self.site.projects).allowed
        ]

    def describe_model(self, info: SessionInfo, model: str, *, samples: bool = True) -> dict[str, Any]:
        entry = self._model_entry(info, model)
        doc = self.site.models[model]
        fields = [
            {
                k: f[k]
                for k in ("name", "role", "type", "description", "folder", "formula", "format")
                if k in f
            }
            for f in doc.get("fields", [])
            if not f.get("hidden")
        ]
        out: dict[str, Any] = {
            "id": model,
            "title": doc.get("title", model),
            "description": doc.get("description", ""),
            "fields": fields,
            "hierarchies": doc.get("hierarchies", []),
            "parameters": [
                {k: p[k] for k in ("name", "type", "default", "allowed") if k in p}
                for p in doc.get("parameters", [])
            ],
            "canQuery": self._can(info, Capability.CONNECT, entry),
        }
        if samples and out["canQuery"]:
            dimensions = [
                f["name"]
                for f in fields
                if f.get("role") == "dimension" and f.get("type") in ("string", None)
            ][:SAMPLE_DIMENSIONS]
            values: dict[str, Any] = {}
            for name in dimensions:
                try:
                    result = self.ctx.service.explore(
                        model, {"dimensions": [name], "limit": SAMPLE_VALUES}, self.session(info)
                    )
                except Exception:  # noqa: S112 - samples are best effort
                    continue
                values[name] = [r[0] for r in result["rows"]]
            # Sample values are data: fenced, so text in them is never taken as instructions.
            out["sampleValues"] = fence(values, "sample-values")
        return out

    def run_query(self, info: SessionInfo, model: str, query: Mapping[str, Any]) -> dict[str, Any]:
        entry = self._model_entry(info, model)
        if not self._can(info, Capability.CONNECT, entry):
            raise AuthoringError(403, f"governed queries on {model!r} need the Connect capability")
        try:
            result = self.ctx.service.explore(model, query, self.session(info))
        except (CompileError, ContentError) as exc:
            raise AuthoringError(422, str(exc)) from None
        except EngineTimeoutError as exc:
            raise AuthoringError(504, str(exc)) from None
        except EngineError as exc:
            raise AuthoringError(502, str(exc)) from None
        self.audit(info, "agent_query", resource_kind="model", resource_id=model, rows=len(result["rows"]))
        rows = result["rows"][:AGENT_ROWS]
        return {
            "columns": result["columns"],
            "rowCount": len(result["rows"]),
            "truncated": result["truncated"] or len(result["rows"]) > AGENT_ROWS,
            "rows": fence(rows, "query-rows"),
        }

    def get_dashboard(
        self, info: SessionInfo, dashboard: str | None = None, draft: str | None = None
    ) -> dict[str, Any]:
        if draft is not None:
            found = self._draft(info, draft)
            return {"dashboard": found.content_id, "draft": found.to_dict(), "files": dict(found.files)}
        if dashboard is None:
            raise AuthoringError(422, "name a dashboard or a draft")
        entry = self._dashboard(info, dashboard)
        out: dict[str, Any] = {
            "dashboard": dashboard,
            "files": self._published_files(dashboard),
            "youMay": [
                c.value for c in (Capability.WEB_EDIT, Capability.OVERWRITE) if self._can(info, c, entry)
            ],
        }
        if hasattr(self.site, "publish"):
            mine = self.drafts().list(info.username, dashboard=dashboard)
            out["openDrafts"] = [d.id for d in mine]
        return out

    # ---- drafts --------------------------------------------------------------------------------------

    def start_draft(self, info: SessionInfo, dashboard: str) -> Draft:
        entry = self._dashboard(info, dashboard)
        if not self._can(info, Capability.WEB_EDIT, entry):
            raise AuthoringError(403, f"editing {dashboard!r} needs the Web Edit capability")
        base = self.site.revision("dashboard", dashboard) if hasattr(self.site, "revision") else None
        draft = self.drafts().create(
            info.username,
            dashboard,
            self._published_files(dashboard),
            base_revision=base.number if base else None,
            agent_session=info.agent_session,
        )
        self.audit(info, "draft_create", resource_kind="dashboard", resource_id=dashboard, draft=draft.id)
        return draft

    def create_dashboard(
        self, info: SessionInfo, dashboard: str, *, project: str, title: str, model: str
    ) -> Draft:
        if not _SLUG.match(dashboard):
            raise AuthoringError(422, "dashboard ids are lowercase letters, digits and hyphens (a slug)")
        found = self.site.projects.get(project)
        if found is None or not self._can(info, Capability.VIEW, found):
            raise _not_found(f"project {project!r}")
        if not self._can(info, Capability.PUBLISH, found):
            raise AuthoringError(403, f"new dashboards in {found.name!r} need the Publish capability")
        if dashboard in self.site.dashboards:
            raise AuthoringError(409, f"dashboard {dashboard!r} exists; edit it instead")
        model_entry = self._model_entry(info, model)
        if not self._can(info, Capability.CONNECT, model_entry):
            raise AuthoringError(403, f"building on {model!r} needs the Connect capability")
        try:
            files = bundle_files.starter(dashboard, title[:200], model, self.site.models[model])
        except bundle_files.EditError as exc:
            raise AuthoringError(422, str(exc)) from None
        draft = self.drafts().create(
            info.username,
            dashboard,
            files,
            base_revision=None,
            project=project,
            agent_session=info.agent_session,
        )
        self.audit(
            info, "draft_create", resource_kind="dashboard", resource_id=dashboard, draft=draft.id, new=True
        )
        return draft

    def edit(
        self,
        info: SessionInfo,
        edits: Sequence[Mapping[str, Any]],
        *,
        draft: str | None = None,
        dashboard: str | None = None,
    ) -> tuple[Draft, ValidationReport]:
        """Apply edits to a draft (starting one from the dashboard when needed); quick validation."""
        if draft is not None:
            current = self._open(info, draft)
        elif dashboard is not None:
            mine = (
                self.drafts().list(info.username, dashboard=dashboard)
                if dashboard in self.site.dashboards
                else []
            )
            current = mine[0] if mine else self.start_draft(info, dashboard)
        else:
            raise AuthoringError(422, "name the draft (or the dashboard) to edit")
        try:
            files = bundle_files.apply_edits(current.files, edits)
        except bundle_files.EditError as exc:
            raise AuthoringError(422, str(exc)) from None
        try:
            updated = self.drafts().update_files(current.id, files, agent_session=info.agent_session)
        except DraftError as exc:
            raise AuthoringError(409, str(exc)) from None
        self.audit(
            info,
            "draft_edit",
            resource_kind="dashboard",
            resource_id=updated.content_id,
            draft=updated.id,
            edits=len(edits),
        )
        return updated, self.validate_draft(info, updated, quick=True)

    def discard(self, info: SessionInfo, draft_id: str) -> None:
        draft = self._open(info, draft_id)
        self.drafts().discard(draft.id)
        self.audit(
            info, "draft_discard", resource_kind="dashboard", resource_id=draft.content_id, draft=draft.id
        )

    def list_drafts(self, info: SessionInfo) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self.drafts().list(info.username)]

    # ---- validation and preview ---------------------------------------------------------------------

    def _run_draft_query(
        self,
        info: SessionInfo,
        draft: Draft,
        manifest: Mapping[str, Any],
        name: str,
        state: Mapping[str, Any] | None = None,
        selections: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = self.model_allowed(info, draft)
        for model in (manifest.get("models") or {}).values():
            if not allowed(str(model)):
                raise AuthoringError(403, f"this draft uses model {model!r}, which you may not query")
        result: dict[str, Any] = self.ctx.service.query(
            {**manifest, "id": f"draft:{draft.id}"}, name, state or {}, selections or {}, self.session(info)
        )
        return result

    def validate_draft(self, info: SessionInfo, draft: Draft, *, quick: bool = False) -> ValidationReport:
        session = self.session(info)
        service = self.ctx.service

        def compile_query(manifest: Mapping[str, Any], name: str, dialect: str) -> Any:
            return service.compile_named(manifest, name, dialect, session)

        def run(manifest: Mapping[str, Any], name: str) -> Mapping[str, Any]:
            return self._run_draft_query(info, draft, manifest, name)

        screenshots = None
        renderer = self.ctx.renderer
        if not quick and renderer is not None and hasattr(renderer, "render_path") and not draft.is_new:

            def screenshots() -> tuple[bytes, bytes] | None:
                before = renderer.render_path(info, f"/dashboards/{draft.content_id}", {}, "png")
                after = renderer.render_path(info, f"/drafts/{draft.id}", {}, "png")
                return before, after

        return validate(
            draft.files,
            compile_query=compile_query,
            run_query=run,
            screenshots=screenshots,
            model_allowed=self.model_allowed(info, draft),
            quick=quick,
        )

    def validate(self, info: SessionInfo, draft_id: str, *, quick: bool = False) -> ValidationReport:
        return self.validate_draft(info, self._draft(info, draft_id), quick=quick)

    def preview(self, info: SessionInfo, draft_id: str) -> dict[str, Any]:
        """The draft as the author would see it: the bundle and its queries' results (default state)."""
        draft = self._draft(info, draft_id)
        try:
            manifest = bundle_files.parse_manifest(draft.files)
        except bundle_files.EditError as exc:
            raise AuthoringError(422, str(exc)) from None
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name in list(manifest.get("queries", {}))[:PREVIEW_QUERIES]:
            try:
                results[name] = self._run_draft_query(info, draft, manifest, name)
            except AuthoringError:
                raise
            except Exception as exc:
                errors[name] = str(exc)
        domains: dict[str, Any] = {}
        for name in list(manifest.get("filters", {}))[:PREVIEW_QUERIES]:
            try:
                domains[name] = self.draft_domain(info, draft.id, name, {})
            except Exception:  # noqa: S112 - range and relative-date filters have no member list
                continue
        png = None
        renderer = self.ctx.renderer
        if renderer is not None and hasattr(renderer, "render_path"):
            try:
                png = renderer.render_path(info, f"/drafts/{draft.id}", {}, "png")
            except Exception:
                png = None
        return {
            "draft": draft.to_dict(),
            "manifest": manifest,
            "html": draft.files.get(bundle_files.HTML, ""),
            "results": results,
            "domains": domains,
            "errors": errors,
            "png": png,
        }

    def draft_query(
        self,
        info: SessionInfo,
        draft_id: str,
        name: str,
        state: Mapping[str, Any],
        selections: Mapping[str, Any],
    ) -> dict[str, Any]:
        """A query of a draft, for the live preview in the portal (as the author, with RLS)."""
        draft = self._draft(info, draft_id)
        try:
            manifest = bundle_files.parse_manifest(draft.files)
        except bundle_files.EditError as exc:
            raise AuthoringError(422, str(exc)) from None
        try:
            return self._run_draft_query(info, draft, manifest, name, state, selections)
        except (CompileError, ContentError) as exc:
            raise AuthoringError(400, str(exc)) from None
        except EngineTimeoutError as exc:
            raise AuthoringError(504, str(exc)) from None
        except EngineError as exc:
            raise AuthoringError(502, str(exc)) from None

    def draft_domain(
        self, info: SessionInfo, draft_id: str, name: str, state: Mapping[str, Any]
    ) -> dict[str, Any]:
        """A filter's members for a draft's live preview (as the author, with RLS)."""
        draft = self._draft(info, draft_id)
        try:
            manifest = bundle_files.parse_manifest(draft.files)
        except bundle_files.EditError as exc:
            raise AuthoringError(422, str(exc)) from None
        allowed = self.model_allowed(info, draft)
        for model in (manifest.get("models") or {}).values():
            if not allowed(str(model)):
                raise AuthoringError(403, f"this draft uses model {model!r}, which you may not query")
        try:
            result: dict[str, Any] = self.ctx.service.domain(
                {**manifest, "id": f"draft:{draft.id}"}, name, state, self.session(info)
            )
        except (CompileError, ContentError) as exc:
            raise AuthoringError(400, str(exc)) from None
        except EngineTimeoutError as exc:
            raise AuthoringError(504, str(exc)) from None
        except EngineError as exc:
            raise AuthoringError(502, str(exc)) from None
        return result

    # ---- history -----------------------------------------------------------------------------------

    def list_revisions(self, info: SessionInfo, dashboard: str) -> list[dict[str, Any]]:
        entry = self._dashboard(info, dashboard)
        if not (self._can(info, Capability.WEB_EDIT, entry) or self._can(info, Capability.OVERWRITE, entry)):
            raise AuthoringError(403, "revision history needs Web Edit or Overwrite")
        if not hasattr(self.site, "revisions"):
            raise AuthoringError(501, "revision history needs the metadata database")
        return [
            {
                "number": r.number,
                "message": r.message,
                "createdBy": r.created_by,
                "createdAt": r.created_at.isoformat(),
            }
            for r in self.site.revisions("dashboard", dashboard)
        ]

    def diff(
        self,
        info: SessionInfo,
        *,
        dashboard: str | None = None,
        from_revision: int | None = None,
        to_revision: int | None = None,
        draft: str | None = None,
    ) -> str:
        """Revision against revision, or a draft against the revision it started from."""
        if draft is not None:
            found = self._draft(info, draft)
            before = {} if found.is_new else self._published_files(found.content_id, found.base_revision)
            label = f"revision {found.base_revision}" if found.base_revision else "new"
            return bundle_files.diff(before, found.files, labels=(label, "draft"))
        if dashboard is None or from_revision is None:
            raise AuthoringError(422, "name a draft, or a dashboard and from_revision")
        self.list_revisions(info, dashboard)  # the same permission as the history
        try:
            before = self._published_files(dashboard, from_revision)
            after = self._published_files(dashboard, to_revision)
        except ContentError as exc:
            raise AuthoringError(404, str(exc)) from None
        return bundle_files.diff(
            before, after, labels=(f"revision {from_revision}", f"revision {to_revision or 'current'}")
        )

    # ---- publishing --------------------------------------------------------------------------------

    def _approval_required(self) -> bool:
        settings = self.site.settings() if hasattr(self.site, "settings") else {}
        return str(settings.get("authoringApproval", "none")) == "required"

    def _reviewer(self, info: SessionInfo, draft: Draft) -> bool:
        """Site administrators, and owners or leaders of the dashboard's project or one above it."""
        if info.site_role is SiteRole.SITE_ADMINISTRATOR:
            return True
        project = draft.project if draft.is_new else self.site.dashboards[draft.content_id].project
        principal = info.principal()
        return any(
            p.owner == info.username or p.leads(principal) for p in ancestry(project, self.site.projects)
        )

    def _may_publish(self, info: SessionInfo, draft: Draft) -> None:
        if draft.is_new:
            project = self.site.projects.get(draft.project or "")
            if project is None or not self._can(info, Capability.PUBLISH, project):
                raise AuthoringError(403, "publishing a new dashboard needs Publish on its project")
            if draft.content_id in self.site.dashboards:
                raise AuthoringError(409, f"dashboard {draft.content_id!r} was published meanwhile")
        else:
            entry = self._dashboard(info, draft.content_id)
            if not self._can(info, Capability.OVERWRITE, entry):
                raise AuthoringError(
                    403, f"saving over {draft.content_id!r} needs the Overwrite capability; ask its owner"
                )

    def request_publish(self, info: SessionInfo, draft_id: str, message: str = "") -> PublishRequest:
        draft = self._open(info, draft_id)
        self._may_publish(info, draft)
        report = self.validate_draft(info, draft)
        if not report.ok:
            self.audit(
                info,
                "publish_request",
                "deny",
                resource_id=draft.content_id,
                draft=draft.id,
                reason="invalid",
            )
            raise AuthoringError(422, "the draft does not pass validation:\n" + report.summary())
        code, expires = self.drafts().request_publish(draft.id, message)
        self.audit(
            info, "publish_request", resource_kind="dashboard", resource_id=draft.content_id, draft=draft.id
        )
        return PublishRequest(draft, code, expires, report, self.diff(info, draft=draft.id))

    def approve(self, info: SessionInfo, draft_id: str) -> None:
        draft = self.drafts().get(draft_id)
        if draft is None or draft.status != "open":
            raise _not_found(f"draft {draft_id!r}")
        if info.agent_session:
            raise AuthoringError(403, "an agent cannot approve a draft")
        if draft.created_by == info.username or not self._reviewer(info, draft):
            raise AuthoringError(
                403, "approval needs a project leader, the project owner or an administrator"
            )
        self.drafts().approve(draft.id, info.username)
        self.audit(
            info, "publish_approve", resource_kind="dashboard", resource_id=draft.content_id, draft=draft.id
        )

    def confirm_publish(self, info: SessionInfo, draft_id: str, code: str) -> int:
        if info.agent_session:
            # Two-step publish: the person confirms, never the agent working for them.
            raise AuthoringError(
                403, "an agent cannot confirm publishing; the person confirms it in the portal"
            )
        draft = self._open(info, draft_id)
        self._may_publish(info, draft)
        try:
            draft = self.drafts().check_code(draft.id, code)
        except DraftError as exc:
            self.audit(info, "publish_confirm", "deny", resource_id=draft.content_id, draft=draft.id)
            raise AuthoringError(409, str(exc)) from None
        if self._approval_required() and draft.approved_by is None and not self._reviewer(info, draft):
            raise AuthoringError(409, "this site requires approval before publishing; ask a project leader")
        document, other = bundle_files.to_revision(draft.files)
        if draft.is_new:
            document["id"] = draft.content_id
        number: int = self.site.publish(
            "dashboard",
            draft.content_id,
            document,
            files=other,
            project=draft.project,
            owner=info.username if draft.is_new else None,
            message=draft.publish_message or "Edited with TabFree authoring",
            by=info.username,
        )
        self.drafts().mark_published(draft.id, number)
        self.audit(
            info,
            "content_publish",
            resource_kind="dashboard",
            resource_id=draft.content_id,
            draft=draft.id,
            revision=number,
            agent_session=draft.agent_session,
        )
        return number
