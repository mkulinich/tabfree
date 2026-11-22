"""The Superstore sample folder can be moved for an installed package (the demo image)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabfree_api.sample import superstore_dir


def test_superstore_dir_defaults_to_the_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TABFREE_SUPERSTORE_DIR", raising=False)
    assert superstore_dir().parts[-3:] == ("corpus", "derived", "superstore")


def test_superstore_dir_follows_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TABFREE_SUPERSTORE_DIR", str(tmp_path))
    assert superstore_dir() == tmp_path
