"""P3.6/P3.7: exports (capabilities, RLS, spreadsheet safety, renderer) and the usercontent origin."""

from __future__ import annotations

import base64
import io
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient

from tabfree_api.auth import SessionInfo
from tabfree_api.exports import print_url, safe_cell, to_csv, to_xlsx
from tabfree_api.main import create_app
from tabfree_api.settings import Settings
from tabfree_api.usercontent import FRAME_TTL_S, FrameSigner, build_frame

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}
STATE: dict[str, Any] = {"filters": {}, "parameters": {}, "highlight": None}


def make_app(tmp_path: Path, **extra: Any) -> Any:
    return create_app(
        Settings(
            environment="test",
            dev_login=True,
            session_db=tmp_path / "s.db",
            portal_dir=tmp_path / "x",
            **extra,
        )
    )


def as_user(app: Any, user: str, **kw: Any) -> TestClient:
    client = TestClient(app, **kw)
    assert client.post("/auth/dev", content=f"user={user}", follow_redirects=False).status_code == 303
    return client


def export(client: TestClient, fmt: str, query: str | None = "by_region") -> Any:
    body: dict[str, Any] = {"format": fmt, "state": STATE}
    if query:
        body["query"] = query
    return client.post("/api/dashboards/sales-overview/export", json=body, headers=CSRF)


def test_csv_export_respects_capabilities_and_rls(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    erin = as_user(app, "erin")
    response = export(erin, "csv")
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="sales-overview-by_region.csv"'
    text = response.content.decode("utf-8-sig")
    lines = text.strip().split("\r\n")
    assert lines[0] == "Region,Sales,Profit Ratio"
    assert {line.split(",")[0] for line in lines[1:]} == {"East", "West", "Central", "South"}
    # Managers may view but not download summary data (dev site rules).
    assert export(as_user(app, "alice"), "csv").status_code == 403
    # No View: the dashboard does not exist for them.
    assert export(as_user(app, "mallory"), "csv").status_code == 404
    assert export(erin, "csv", query=None).status_code == 422
    assert export(erin, "csv", query="nope").status_code == 422


def test_crosstab_export_is_a_formatted_workbook(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    erin = as_user(make_app(tmp_path), "erin")
    response = export(erin, "crosstab")
    assert response.status_code == 200
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet is not None
    assert [c.value for c in sheet[1]] == ["Region", "Sales", "Profit Ratio"]
    assert sheet["B2"].number_format.startswith("$")


def test_picture_exports_need_the_capability_and_a_renderer(tmp_path: Path) -> None:
    calls: list[tuple[str, str, Mapping[str, Any], str]] = []

    class FakeRenderer:
        def render(
            self, info: SessionInfo, dashboard: str, state: Mapping[str, Any], fmt: Literal["png", "pdf"]
        ) -> bytes:
            calls.append((info.username, dashboard, state, fmt))
            return b"%PDF-1.7 fake"

    app = make_app(tmp_path)
    admin = as_user(app, "admin")
    assert export(admin, "pdf", query=None).status_code == 501  # no renderer configured
    app.state.context.renderer = FakeRenderer()
    response = export(admin, "pdf", query=None)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert calls == [("admin", "sales-overview", {"filters": {}, "parameters": {}}, "pdf")]
    # Executives have no Download Image/PDF rule on the Sales project.
    assert export(as_user(app, "erin"), "png", query=None).status_code == 403


def test_spreadsheet_safety_and_totals() -> None:
    assert safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert safe_cell("+1") == "'+1"
    assert safe_cell("-x") == "'-x"
    assert safe_cell("@SUM") == "'@SUM"
    assert safe_cell(-3.5) == -3.5
    assert safe_cell("East") == "East"
    payload = {
        "columns": [{"name": "Region"}, {"name": "Sales", "format": "$#,##0"}, {"name": "__total"}],
        "rows": [["=cmd()", 1.0, None], [None, 1.0, "grand"]],
        "truncated": False,
    }
    assert to_csv(payload).decode("utf-8-sig") == "Region,Sales\r\n'=cmd(),1\r\n"
    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(to_xlsx(payload, "T"))).active
    assert sheet is not None
    assert sheet["A2"].value == "'=cmd()"
    assert sheet["A2"].data_type == "s"
    assert sheet.max_row == 2  # the grand total row is not summary data


def test_print_url_round_trips_state() -> None:
    url = print_url(
        "https://tabfree.example", "sales overview", {"filters": {"region": {"members": ["East"]}}}
    )
    assert url.startswith("https://tabfree.example/dashboards/sales%20overview?print=1&state=")
    encoded = url.split("state=")[1]
    decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert decoded == {"filters": {"region": {"members": ["East"]}}}


# ---- usercontent origin (P3.6) ---------------------------------------------------------------------------


def test_frame_urls_are_signed_and_expire() -> None:
    signer = FrameSigner(b"k" * 32)
    url = signer.url("https://uc.example", "dev", "sales-overview", now=1000.0)
    expires = int(url.split("e=")[1].split("&")[0])
    signature = url.split("s=")[1]
    assert signer.verify("dev", "sales-overview", expires, signature, now=1000.0)
    assert not signer.verify("dev", "other", expires, signature, now=1000.0)
    assert not signer.verify("other-site", "sales-overview", expires, signature, now=1000.0)
    assert not signer.verify("dev", "sales-overview", expires, signature, now=1000.0 + FRAME_TTL_S + 1)
    assert not FrameSigner(b"x" * 32).verify("dev", "sales-overview", expires, signature, now=1000.0)


def test_frames_inline_the_runtime_with_a_csp() -> None:
    page = build_frame("<html><head><title>x</title></head><body></body></html>", "a</script>b")
    assert page.index("Content-Security-Policy") < page.index("<script>") < page.index("<title>")
    assert "a<\\/script>b" in page
    assert "sandbox" not in page  # the sandbox directive is only valid as a header


def test_usercontent_origin_serves_only_frames(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "frame.js").write_text("window.tabfreeFrame = 1;")
    app = make_app(
        tmp_path,
        base_url="https://portal.example",
        usercontent_origin="https://uc.example",
        runtime_dir=runtime,
    )
    erin = as_user(app, "erin", base_url="https://portal.example")
    bundle = erin.get("/api/dashboards/sales-overview").json()
    assert "html" not in bundle
    frame_url: str = bundle["frameUrl"]
    assert frame_url.startswith("https://uc.example/frame/sales-overview?e=")
    uc = TestClient(app, base_url="https://uc.example")
    frame = uc.get(frame_url)
    assert frame.status_code == 200
    csp = frame.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts;")
    assert "connect-src 'none'" in csp
    assert csp.endswith("frame-ancestors https://portal.example")
    assert "window.tabfreeFrame = 1;" in frame.text
    assert "set-cookie" not in frame.headers
    # The usercontent host serves nothing else, and the portal host serves no frames.
    assert uc.get("/api/me").status_code == 404
    assert uc.get("/").status_code == 404
    assert erin.get(frame_url.replace("https://uc.example", "")).status_code == 404
    tampered = frame_url.replace("sales-overview", "sales-analysis", 1)
    assert uc.get(tampered).status_code == 404
    expired = FrameSigner().url("https://uc.example", "dev", "sales-overview", now=time.time() - 3600)
    assert uc.get(expired).status_code == 404


def test_portal_page_gets_a_strict_csp_with_a_usercontent_origin(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root"></div>')
    app = create_app(
        Settings(
            environment="test",
            session_db=tmp_path / "s.db",
            portal_dir=dist,
            base_url="https://portal.example",
            usercontent_origin="https://uc.example",
        )
    )
    csp = TestClient(app, base_url="https://portal.example").get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-src https://uc.example" in csp
    with pytest.raises(RuntimeError, match="must differ"):
        create_app(
            Settings(
                environment="test",
                session_db=tmp_path / "s2.db",
                base_url="https://portal.example",
                usercontent_origin="https://portal.example",
            )
        )
