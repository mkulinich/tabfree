"""Verification reports (BUILD_PLAN P5.5): does TabFree give the same answers as Tableau did?

- **Access parity.** For each user, the dashboards Tableau let them open ("Query Workbooks for User",
  harvested by ``crawl --type user_workbooks``) against TabFree's View decision on the same migrated
  dashboards, evaluated with the imported rules even before an item is published. Every difference is
  listed; differences on dashboards that were retired or not converted are explained, the rest are not
  (M5 asks for zero unexplained differences).
- **Data parity.** Each query with a captured Tableau CSV runs on the item's draft as the reviewer and
  is compared row by row (:func:`tabfree_compiler.parity.compare`).
- **RLS parity.** The same comparison as sample users: Tableau CSVs captured per user (through a
  connected app, ``<user>/<query>.csv``) against TabFree results run with that user's identity.
- **Visual parity.** The draft is rendered and compared with Tableau's PNG
  (:func:`tabfree_api.authoring.png.similarity`).

Each check updates the item's fidelity and leaves a ``verification`` record.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tabfree_api.auth import SessionInfo
from tabfree_api.authoring import files as bundle_files
from tabfree_api.authoring import png
from tabfree_api.authoring.drafts import DraftStore
from tabfree_api.authz import Capability, ContentEntry, Principal, evaluate, parse_rules
from tabfree_api.db.store import DbSite
from tabfree_api.migration.fidelity import data_score, score, validation_score
from tabfree_api.migration.store import MigrationStore
from tabfree_compiler.parity import compare

_EXPLAINED = {
    "retired": "retired in Tableau's place",
    "manual": "not converted yet",
    "rebuild": "being rebuilt",
}


def _principal(db: DbSite, username: str) -> Principal | None:
    user = db.users.get(username)
    if user is None:
        return None
    return Principal(user.username, frozenset(user.groups), user.site_role, user.fullname, user.attributes)


def session_for(db: DbSite, username: str, *, ttl_s: float = 600) -> SessionInfo:
    user = db.users.get(username)
    if user is None:
        raise KeyError(username)
    return SessionInfo(
        user.username,
        user.fullname,
        frozenset(user.groups),
        user.site_role,
        time.time() + ttl_s,
        email=user.email,
        attributes=dict(user.attributes),
    )


def access_parity(db: DbSite, store: MigrationStore) -> dict[str, Any]:
    reference = next(iter(store.records("access_reference", ["tableau"])), None)
    if reference is None:
        return {"status": "skipped", "reason": "no Tableau access reference (crawl --type user_workbooks)"}
    tableau: Mapping[str, list[str]] = reference["data"]
    items = {i["id"]: i for i in store.list_items(limit=100_000)}
    entries: dict[str, ContentEntry] = {}
    for item_id, item in items.items():
        full = store.get(item_id) or {}
        site_entry = (full.get("report") or {}).get("site") or {}
        rules = parse_rules(site_entry.get("permissions") or [])
        if item["status"] == "approved" and item_id in db.dashboards:
            entries[item_id] = db.dashboards[item_id]
        else:
            entries[item_id] = ContentEntry(item_id, item.get("project_id"), item.get("owner"), rules)
    differences = []
    unexplained = 0
    for username, expected in sorted(tableau.items()):
        principal = _principal(db, username)
        expected_set = {d for d in expected if d in items}
        if principal is None:
            differences.append({"user": username, "explanation": None, "missingUser": True})
            unexplained += 1
            continue
        actual = {
            d
            for d, entry in entries.items()
            if evaluate(principal, Capability.VIEW, entry, db.projects).allowed
        }
        for kind, ids in (("missing", expected_set - actual), ("extra", actual - expected_set)):
            for dashboard in sorted(ids):
                status = items[dashboard]["status"]
                why = _EXPLAINED.get(status)
                if why is None:
                    unexplained += 1
                differences.append({"user": username, "dashboard": dashboard, kind: True, "explanation": why})
    report = {
        "status": "done",
        "users": len(tableau),
        "differences": differences,
        "unexplained": unexplained,
        "ok": unexplained == 0,
    }
    store.put_record("verification", "access", report, "ok" if unexplained == 0 else "differences")
    return report


def _parity(expected_csv: str, run: Any) -> dict[str, Any]:
    try:
        result = run()
    except Exception as exc:  # a failing query is a parity failure, recorded with its reason
        return {
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "expected": max(1, expected_csv.count("\n") - 1),
        }
    report = compare(expected_csv, result)
    return {
        "matched": report.matched,
        "missing": len(report.missing),
        "extra": len(report.extra),
        "mismatched": len(report.mismatches),
        "unmapped": report.unmapped,
        "examples": report.mismatches[:5] + [f"missing {k}" for k in report.missing[:3]],
        "ok": report.ok,
    }


def data_parity(ctx: Any, store: MigrationStore, item_id: str, info: SessionInfo) -> dict[str, Any]:
    from tabfree_api.authoring.service import AuthoringService

    item = store.get(item_id)
    if item is None or not item.get("draft_id"):
        return {"status": "skipped", "reason": "no draft"}
    csvs: Mapping[str, str] = item.get("reference_csv") or {}
    if not csvs:
        return {"status": "skipped", "reason": "no captured Tableau CSV"}
    service = AuthoringService(ctx)
    results = {
        query: _parity(csv_text, lambda q=query: service.draft_query(info, str(item["draft_id"]), q, {}, {}))
        for query, csv_text in csvs.items()
    }
    report = {"status": "done", "queries": results, "score": data_score(results)}
    store.put_record("verification", f"data:{item_id}", report)
    return report


def rls_parity(ctx: Any, store: MigrationStore, item_id: str, samples: Path) -> dict[str, Any]:
    """``samples/<username>/<query>.csv``: Tableau's data for that user, captured through a connected app."""
    from tabfree_api.authoring.service import AuthoringService

    item = store.get(item_id)
    if item is None or not item.get("draft_id"):
        return {"status": "skipped", "reason": "no draft"}
    db: DbSite = ctx.site
    service = AuthoringService(ctx)
    draft_row = DraftStore(db.engine, db.id).get(str(item["draft_id"]))
    if draft_row is None:
        return {"status": "skipped", "reason": "no draft"}
    manifest = {**bundle_files.parse_manifest(draft_row.files), "id": f"draft:{draft_row.id}"}

    def as_viewer(viewer: SessionInfo, query: str) -> Any:
        # The administrator's check, run with the viewer's identity so their RLS applies. Viewers
        # need no Connect for this: the dashboard, once published, is what grants them its data.
        return ctx.service.query(manifest, query, {}, {}, service.session(viewer))

    users: dict[str, Any] = {}
    for folder in sorted(p for p in samples.iterdir() if p.is_dir()):
        if folder.name not in db.users:
            users[folder.name] = {"error": "not a TabFree user"}
            continue
        info = session_for(db, folder.name)
        users[folder.name] = {
            path.stem: _parity(
                path.read_text(encoding="utf-8", errors="replace"),
                lambda q=path.stem, i=info: as_viewer(i, q),
            )
            for path in sorted(folder.glob("*.csv"))
        }
    ok = all(isinstance(r, dict) and all(q.get("ok") for q in r.values()) for r in users.values())
    report = {"status": "done", "users": users, "ok": ok}
    store.put_record("verification", f"rls:{item_id}", report, "ok" if ok else "differences")
    return report


def visual_parity(ctx: Any, store: MigrationStore, item_id: str, info: SessionInfo) -> dict[str, Any]:
    item = store.get(item_id)
    reference = store.image(item_id, "reference_png")
    if item is None or not item.get("draft_id"):
        return {"status": "skipped", "reason": "no draft"}
    if reference is None:
        return {"status": "skipped", "reason": "no Tableau PNG"}
    renderer = getattr(ctx, "renderer", None)
    if renderer is None or not hasattr(renderer, "render_path"):
        return {"status": "skipped", "reason": "no renderer (install tabfree-api[render] and Chromium)"}
    render = renderer.render_path(info, f"/drafts/{item['draft_id']}", {}, "png")
    store.upsert(item_id, {"render_png": render})
    try:
        value = png.similarity(reference, render)
    except png.PngError as exc:
        return {"status": "failed", "reason": str(exc)}
    report = {"status": "done", "similarity": value}
    store.put_record("verification", f"visual:{item_id}", report)
    return report


def refresh_fidelity(
    ctx: Any,
    store: MigrationStore,
    item_id: str,
    info: SessionInfo,
    *,
    data: dict[str, Any] | None = None,
    visual: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the draft and recompute the item's fidelity from the latest checks."""
    from tabfree_api.authoring.service import AuthoringService

    item = store.get(item_id) or {}
    report = item.get("report") or {}
    validation = None
    if item.get("draft_id"):
        draft = DraftStore(ctx.site.engine, ctx.site.id).get(str(item["draft_id"]))
        if draft is not None:
            validation = AuthoringService(ctx).validate_draft(info, draft, quick=True).to_dict()
    previous = (item.get("fidelity") or {}).get("components") or {}
    fidelity = score(
        coverage=(report.get("coverage") or {}).get("fraction"),
        validation=validation_score(validation),
        data=(data or {}).get("score", previous.get("data")),
        visual=(visual or {}).get("similarity", previous.get("visual")),
        unsupported=report.get("unsupported") or [],
    )
    fidelity["validation"] = validation
    store.upsert(item_id, {"fidelity": fidelity})
    return fidelity
