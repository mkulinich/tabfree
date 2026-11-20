from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from tabfree_migrate.inventory import OBJECT_TYPES, InventoryStore, Item, crawl


class FakeSource:
    server = "https://tableau.example.com"
    site = "finance"

    def __init__(self) -> None:
        self.objects: dict[str, list[Item]] = {
            "users": [Item("users", "u1", "ana", "2026-01-01T00:00:00Z", {"site_role": "Creator"})],
            "projects": [Item("projects", "p1", "Sales", None, {"parent_id": None})],
            "workbooks": [
                Item("workbooks", "w1", "Superstore", "2026-02-01T00:00:00Z", {"project_id": "p1"}),
                Item("workbooks", "w2", "Ops", "2026-02-02T00:00:00Z", {"project_id": "p1"}),
            ],
        }
        self.broken: set[str] = set()

    def items(self, object_type: str) -> Iterable[Item]:
        if object_type in self.broken:
            raise PermissionError(f"403 on {object_type}")
        return list(self.objects.get(object_type, []))


def test_first_crawl_adds_everything_in_order(tmp_path: Path) -> None:
    store = InventoryStore(tmp_path / "inventory.db")
    stats = crawl(FakeSource(), store)
    assert (stats.added, stats.updated, stats.unchanged) == (4, 0, 0)
    assert list(stats.counts) == list(OBJECT_TYPES)
    assert [i.name for i in store.objects("workbooks")] == ["Ops", "Superstore"]


def test_recrawl_is_idempotent_and_detects_changes(tmp_path: Path) -> None:
    store = InventoryStore(tmp_path / "inventory.db")
    source = FakeSource()
    crawl(source, store)
    assert crawl(source, store).unchanged == 4

    source.objects["workbooks"][0] = Item(
        "workbooks", "w1", "Superstore v2", "2026-03-01T00:00:00Z", {"project_id": "p1"}
    )
    del source.objects["workbooks"][1]
    stats = crawl(source, store)
    assert (stats.added, stats.updated, stats.unchanged) == (0, 1, 2)
    last_run = store.db.execute("SELECT MAX(id) FROM runs").fetchone()[0]
    assert store.missing_since(last_run) == [("workbooks", "w2", "Ops")]


def test_one_failing_endpoint_does_not_stop_the_crawl(tmp_path: Path) -> None:
    store = InventoryStore(tmp_path / "inventory.db")
    source = FakeSource()
    source.broken.add("projects")
    stats = crawl(source, store)
    assert stats.errors == {"projects": "PermissionError: 403 on projects"}
    assert stats.counts["workbooks"] == 2
    finished = store.db.execute("SELECT finished_at, stats FROM runs").fetchone()
    assert finished[0] is not None
    assert "403 on projects" in finished[1]
