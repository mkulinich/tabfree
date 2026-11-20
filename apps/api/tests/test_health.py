from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tabfree_api import __version__
from tabfree_api.main import create_app
from tabfree_api.settings import Settings


def client() -> TestClient:
    return TestClient(create_app(Settings(environment="test", session_db=":memory:")))


def test_healthz() -> None:
    response = client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_environment() -> None:
    response = client().get("/api/version")
    assert response.json() == {"name": "tabfree-api", "version": __version__, "environment": "test"}


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABFREE_ENVIRONMENT", "staging")
    assert Settings().environment == "staging"
