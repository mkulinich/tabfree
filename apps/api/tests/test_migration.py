"""Phase 5 in the server: import, the console, reviewer actions, verification and the repair agent."""

from __future__ import annotations

import csv
import io
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tabfree_api.authoring import png
from tabfree_api.authoring.budget import TokenBudget
from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.main import create_app
from tabfree_api.migration import fidelity
from tabfree_api.migration.importer import import_factory
from tabfree_api.migration.repair import RepairAgent, system_prompt
from tabfree_api.migration.store import MigrationError, MigrationStore
from tabfree_api.settings import REPO_ROOT, Settings

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}
EXAMPLE = REPO_ROOT / "packages" / "schemas" / "examples"
ITEM = "sales-overview-tableau"


def make_png(width: int, height: int, pixel: Any) -> bytes:
    """An RGB PNG; ``pixel(x, y)`` gives each colour."""
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


REFERENCE = make_png(40, 30, lambda x, y: (40, 90, 200) if x < 20 else (250, 250, 250))


def factory_folder(root: Path) -> Path:
    """A small factory folder (as ``tabfree-migrate factory`` writes it) over the dev sample data."""
    out = root / "factory"
    (out / "models").mkdir(parents=True)
    # Copied as written: YAML 1.1 loaders would turn keys such as ``on`` into booleans.
    (out / "models" / "superstore_sales.model.yaml").write_text(
        (EXAMPLE / "models" / "superstore_sales.model.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    bundle = out / "dashboards" / ITEM
    (bundle / "reference").mkdir(parents=True)
    manifest = json.loads((EXAMPLE / "dashboards" / "sales-overview" / "tabfree.json").read_text())
    manifest["id"] = ITEM
    (bundle / "tabfree.json").write_text(json.dumps(manifest), encoding="utf-8")
    (bundle / "index.html").write_text((EXAMPLE / "dashboards" / "sales-overview" / "index.html").read_text())
    (bundle / "README.md").write_text("# Sales Overview\n\nConverted from Tableau.\n")
    (bundle / "reference.png").write_bytes(REFERENCE)
    (bundle / "source.json").write_text(
        json.dumps({"dashboard": {"name": "Overview", "zones": []}, "worksheets": []})
    )
    report = {
        "id": ITEM,
        "title": "Sales Overview",
        "workbook": "Superstore",
        "workbookLuid": "w1",
        "status": "converted",
        "coverage": {"sheets": 4, "converted": 4, "partial": 0, "fraction": 1.0},
        "unsupported": ["action 'Go to details': navigation"],
        "notes": [],
        "references": {"png": {"file": "reference.png"}, "csv": {}},
        "usage": 120,
    }
    (bundle / "report.json").write_text(json.dumps(report))
    manual = out / "dashboards" / "old-report"
    manual.mkdir()
    (manual / "report.json").write_text(
        json.dumps(
            {
                "id": "old-report",
                "title": "Old report",
                "workbook": "Legacy",
                "status": "manual",
                "coverage": {},
            }
        )
    )
    (manual / "README.md").write_text("# Old report\n")
    site = {
        "id": "dev",
        "dashboards": "dashboards",
        "models": {
            "path": "models",
            "entries": [
                {
                    "id": "superstore_sales",
                    "owner": "admin",
                    "permissions": [
                        {"group": "Executives", "capabilities": {"view": "allow", "connect": "allow"}}
                    ],
                }
            ],
        },
        "projects": [
            {
                "id": "migrated-sales",
                "name": "Migrated sales",
                "assetPermissions": "customizable",
                "owner": "admin",
                "defaults": {"dashboard": [{"group": "Managers", "capabilities": {"view": "allow"}}]},
                "dashboards": [
                    {
                        "id": ITEM,
                        "owner": "erin",
                        "permissions": [{"group": "Executives", "capabilities": {"view": "allow"}}],
                    }
                ],
            }
        ],
        "users": [{"username": "erin", "fullname": "Erin", "siteRole": "explorer", "groups": ["Executives"]}],
        "redirects": {"views/Superstore/Overview": ITEM},
    }
    (out / "site.yaml").write_text(yaml.safe_dump(site), encoding="utf-8")
    governance = {
        "schema": "tabfree/governance@0",
        "groups": [{"name": "Executives", "source": "idp"}],
        "favorites": {"erin": [{"kind": "dashboard", "id": ITEM}]},
        "jobs": [
            {
                "kind": "subscription",
                "subject": "Daily",
                "user": "erin",
                "dashboard": ITEM,
                "status": "pending",
            }
        ],
        "alerts": [
            {"title": "Low", "owner": "erin", "condition": None, "status": "needs-owner-confirmation"}
        ],
        "customViews": [],
        "content": {ITEM: {"tags": ["finance"]}},
        "access": {"erin": [ITEM], "casey": [ITEM]},
        "report": {"orphaned": []},
    }
    (out / "governance.json").write_text(json.dumps(governance))
    waves = {
        "items": {ITEM: {"wave": 1, "status": "converted"}, "old-report": {"wave": None, "status": "retire"}}
    }
    (out / "waves.json").write_text(json.dumps(waves))
    (out / "decisions.json").write_text(
        json.dumps(
            {"queue": [{"sourceKey": "sqlserver://erp/erp", "options": ["federated"], "datasources": []}]}
        )
    )
    return out


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    result = import_factory(engine, factory_folder(tmp_path), by="admin")
    assert result.items == 2
    assert not result.errors
    return create_app(
        Settings(
            environment="test",
            dev_login=True,
            database_url=url,
            site_id="dev",
            permission_cache_s=0,
            base_url="http://testserver",
        )
    )


def as_user(app: Any, user: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def info_of(app: Any, client: TestClient) -> Any:
    return app.state.context.sessions.get(client.cookies["tabfree_session"])


def test_import_creates_items_with_drafts_and_keeps_records(app: Any) -> None:
    ctx = app.state.context
    store = MigrationStore(ctx.site.engine, "dev")
    item = store.get(ITEM)
    assert item is not None
    assert (item["status"], item["wave"], item["usage"], item["owner"]) == ("converted", 1, 120, "erin")
    assert item["has_reference_png"]
    assert item["fidelity"]["components"]["coverage"] == 1.0
    assert item["report"]["source"]["dashboard"]["name"] == "Overview"
    draft = ctx.site  # the draft is private: not published, not visible to viewers
    assert ITEM not in draft.dashboards
    retired = store.get("old-report")
    assert retired is not None
    assert retired["status"] == "retired"
    assert retired["draft_id"] is None
    assert len(store.records("job")) == 1
    assert len(store.records("decision")) == 1
    assert store.records("alert")[0]["status"] == "needs-owner-confirmation"
    files = json.loads(next(iter(store.records("redirects")))["data"] and "{}")
    assert files == {}
    summary = store.summary()
    assert summary["waves"]["1"] == {"converted": 1}
    assert summary["waves"]["retired"] == {"retired": 1}


def test_the_console_is_for_site_administrators(app: Any) -> None:
    assert as_user(app, "erin").get("/api/migration").status_code == 403
    admin = as_user(app, "admin")
    summary = admin.get("/api/migration").json()
    assert summary["items"] == 2
    assert summary["decisions"] == 1
    assert summary["repairAvailable"] is False
    items = admin.get("/api/migration/items", params={"wave": 1}).json()
    assert [i["id"] for i in items] == [ITEM]
    detail = admin.get(f"/api/migration/items/{ITEM}").json()
    assert detail["events"][0]["action"] == "import"
    assert "source" not in detail["report"]
    image = admin.get(f"/api/migration/items/{ITEM}/reference.png")
    assert image.status_code == 200
    assert image.content == REFERENCE
    assert admin.get(f"/api/migration/items/{ITEM}/render.png").status_code == 404
    assert admin.get("/api/migration/records/decision").json()[0]["key"] == "sqlserver://erp/erp"
    assert as_user(app, "admin").get("/migration").status_code in (200, 307)


def test_reviewer_actions_and_approval_publish_with_tableau_permissions(app: Any, tmp_path: Path) -> None:
    admin = as_user(app, "admin")

    def act(action, note=""):
        return admin.post(
            f"/api/migration/items/{ITEM}/actions", json={"action": action, "note": note}, headers=CSRF
        )

    assert act("reopen").status_code == 409  # only from rebuild, retired or changes requested
    out = act("request_changes", "Make the trend a bar chart")
    assert out.status_code == 200
    assert out.json()["status"] == "changes_requested"
    assert act("reopen").json()["status"] == "review"
    approved = act("approve", "pilot sign-off")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["published_revision"] == 1
    assert act("approve").status_code == 409
    erin, casey = as_user(app, "erin"), as_user(app, "casey")
    dashboard = erin.get(f"/api/dashboards/{ITEM}")
    assert dashboard.status_code == 200
    assert casey.get(f"/api/dashboards/{ITEM}").status_code == 404
    resolved = erin.get("/api/redirects/resolve", params={"path": "#/views/Superstore/Overview"})
    assert resolved.json()["to"] == f"/dashboards/{ITEM}"
    favorites = erin.get("/api/home").json()
    assert ITEM in json.dumps(favorites)
    manifest = app.state.context.site.bundle(ITEM).manifest
    assert manifest["tags"] == ["finance"]
    # Re-importing leaves the approved item alone.
    result = import_factory(app.state.context.site.engine, tmp_path / "factory", by="admin")
    assert result.skipped_approved == [ITEM]


def test_access_parity_explains_differences(app: Any) -> None:
    report = as_user(app, "admin").post("/api/migration/access-parity", headers=CSRF).json()
    assert report["users"] == 2
    # Casey could open it in Tableau but is not an executive here: an unexplained difference.
    assert report["unexplained"] == 1
    assert report["ok"] is False
    (difference,) = report["differences"]
    assert difference == {"user": "casey", "dashboard": ITEM, "missing": True, "explanation": None}
    assert as_user(app, "admin").get("/api/migration").json()["access"]["unexplained"] == 1


class FakeRenderer:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.paths: list[str] = []

    def render_path(self, info: Any, path: str, state: Any, fmt: str) -> bytes:
        self.paths.append(path)
        return self.image


def test_verification_scores_data_and_visual_parity(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tabfree_api.authoring.service import AuthoringService

    ctx = app.state.context
    admin = as_user(app, "admin")
    store = MigrationStore(ctx.site.engine, "dev")
    draft_id = store.get(ITEM)["draft_id"]  # type: ignore[index]
    result = {
        "columns": [{"name": "Region", "role": "dimension"}, {"name": "Sales", "role": "measure"}],
        "rows": [["East", 100.5], ["West", 42.0]],
    }
    asked: list[str] = []

    def draft_query(self: Any, info: Any, draft: str, name: str, state: Any, selections: Any) -> Any:
        asked.append(name)
        return result

    monkeypatch.setattr(AuthoringService, "draft_query", draft_query)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([c["name"] for c in result["columns"]])
    writer.writerows(result["rows"])
    store.upsert(ITEM, {"reference_csv": {"by_region": buffer.getvalue()}})
    renderer = FakeRenderer(REFERENCE)
    ctx.renderer = renderer
    out = admin.post(f"/api/migration/items/{ITEM}/verify", headers=CSRF).json()
    assert out["data"]["score"] == 1.0, out["data"]
    assert out["data"]["queries"]["by_region"]["ok"]
    assert out["visual"]["similarity"] == 1.0
    assert renderer.paths == [f"/drafts/{draft_id}"]
    assert out["fidelity"]["grade"] == "pass"
    # A wrong number is a data parity failure, and the grade drops.
    store.upsert(ITEM, {"reference_csv": {"by_region": buffer.getvalue().replace(",", ",9", 1)}})
    worse = admin.post(f"/api/migration/items/{ITEM}/verify", headers=CSRF).json()
    assert asked
    assert set(asked) == {"by_region"}
    assert worse["data"]["score"] < 1.0
    assert worse["fidelity"]["grade"] != "pass"
    assert admin.get(f"/api/migration/items/{ITEM}/render.png").content == REFERENCE


def response(*content: dict[str, Any], stop: str = "end_turn") -> Any:
    from types import SimpleNamespace

    usage = SimpleNamespace(
        input_tokens=200, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    return SimpleNamespace(content=list(content), stop_reason=stop, usage=usage)


class Scripted:
    name = "scripted"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def model(self, task: str) -> str:
        return f"scripted-{task}"

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_the_repair_agent_edits_the_draft_and_rescores(app: Any) -> None:
    ctx = app.state.context
    store = MigrationStore(ctx.site.engine, "dev")
    draft_id = store.get(ITEM)["draft_id"]  # type: ignore[index]
    edit = {
        "type": "tool_use",
        "id": "t1",
        "name": "edit_dashboard_files",
        "input": {
            "draft": draft_id,
            "edits": [
                {
                    "path": "index.html",
                    "op": "replace",
                    "old": 'query="trend" mark="line"',
                    "new": 'query="trend" mark="bar"',
                }
            ],
        },
    }
    provider = Scripted(
        [
            response({"type": "text", "text": "The reference shows bars."}, edit, stop="tool_use"),
            response({"type": "text", "text": "Changed the trend to bars to match Tableau."}),
        ]
    )
    ctx.provider = provider
    ctx.budget = TokenBudget("dev", per_user=100_000, per_site=0)
    ctx.renderer = FakeRenderer(REFERENCE)
    info = info_of(app, as_user(app, "admin"))
    result = RepairAgent(ctx, max_iterations=2, target=0.5).run(ITEM, info)
    assert result.status == "review"
    assert result.iterations == 1
    assert result.fidelity["components"]["visual"] == 1.0
    draft = AuthoringServiceFor(ctx).drafts().get(draft_id)
    assert 'query="trend" mark="bar"' in draft.files["index.html"]
    first = provider.calls[0]
    assert first["task"] == "migrate"
    assert "You migrate Tableau dashboards" in first["system"][0]["text"]
    blocks = first["messages"][0]["content"]
    assert blocks[1]["type"] == "image"
    assert "<untrusted-data" in blocks[-1]["text"]
    item = store.get(ITEM)
    assert item is not None
    assert item["status"] == "review"
    assert item["repair_iterations"] == 1
    assert [e["action"] for e in store.events(ITEM)][:3] == [
        "repair_done",
        "repair_iteration",
        "repair_start",
    ]


def AuthoringServiceFor(ctx: Any) -> Any:  # noqa: N802 - reads like a constructor in the test
    from tabfree_api.authoring.service import AuthoringService

    return AuthoringService(ctx)


def test_repair_needs_ai_and_an_open_item(app: Any) -> None:
    ctx = app.state.context
    ctx.provider = None
    with pytest.raises(MigrationError):
        RepairAgent(ctx)
    ctx.provider = Scripted([])
    info = info_of(app, as_user(app, "admin"))
    with pytest.raises(MigrationError, match="no converted bundle"):
        RepairAgent(ctx).run("old-report", info)
    assert "Do not request or confirm publishing" in system_prompt()


def test_similarity_and_png_formats() -> None:
    other = make_png(80, 60, lambda x, y: (40, 90, 200) if x < 40 else (250, 250, 250))
    flipped = make_png(40, 30, lambda x, y: (250, 250, 250) if x < 20 else (40, 90, 200))
    blank = make_png(40, 30, lambda x, y: (255, 255, 255))
    assert png.similarity(REFERENCE, REFERENCE) == 1.0
    assert png.similarity(REFERENCE, other) > 0.95  # same layout at another size
    assert png.similarity(REFERENCE, flipped) < 0.5
    assert png.similarity(REFERENCE, blank) < 0.6


def test_fidelity_grades() -> None:
    assert fidelity.score(coverage=1.0, validation=1.0, data=1.0, visual=0.9)["grade"] == "pass"
    assert fidelity.score(coverage=1.0, validation=1.0)["grade"] == "review"  # no data proof yet
    assert fidelity.score(coverage=1.0, validation=0.0, data=1.0)["grade"] == "fail"
    assert fidelity.score(coverage=None)["grade"] == "fail"
    assert fidelity.data_score({"q": {"matched": 3, "missing": 1}}) == 0.75
