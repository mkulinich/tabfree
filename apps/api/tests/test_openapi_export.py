"""P7.6: the API reference is generated from the app and covers the public endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from tabfree_api.openapi import main


def test_the_reference_is_written_and_lists_the_public_api(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main([str(out)]) == 0
    spec = json.loads(out.read_text())
    assert spec["info"]["title"] == "TabFree API"
    paths = spec["paths"]
    for path in ("/api/dashboards/{dashboard_id}/query", "/api/me", "/scim/v2/Users"):
        assert any(p.startswith(path.split("{")[0]) for p in paths), path
    again = tmp_path / "again.json"
    assert main([str(again)]) == 0
    assert again.read_text() == out.read_text()  # the same on every run, so the docs diff cleanly
