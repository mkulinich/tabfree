from __future__ import annotations

import pytest

from tabfree_migrate.tableau import TableauConnection

_SECRET = "s3cr3t-never-print-me"


@pytest.fixture
def secret() -> str:
    return _SECRET


@pytest.fixture
def connection() -> TableauConnection:
    return TableauConnection("https://tableau.example.com", "finance", "tabfree-smoke", _SECRET)


@pytest.fixture
def tableau_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLEAU_SERVER_URL", "https://tableau.example.com/")
    monkeypatch.setenv("TABLEAU_SITE", "finance")
    monkeypatch.setenv("TABLEAU_PAT_NAME", "tabfree-smoke")
    monkeypatch.setenv("TABLEAU_PAT_SECRET", _SECRET)
