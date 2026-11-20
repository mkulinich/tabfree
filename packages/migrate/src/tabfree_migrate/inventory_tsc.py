"""TSC-backed :class:`InventorySource` (read-only REST calls only).

Not yet exercised against a live Tableau Server; ``tabfree-migrate smoke`` and the first real
crawl (P0.6 / P1.1) are where it gets verified.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Any

from tabfree_migrate.inventory import Item
from tabfree_migrate.tableau import TableauConnection, TscReader

_SIMPLE = (str, int, float, bool, type(None))


def _plain(value: Any, depth: int = 0) -> Any:
    """Convert TSC model objects to JSON-able data (public properties only, bounded depth)."""
    if isinstance(value, _SIMPLE):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(v, depth + 1) for v in value]
    if depth >= 3:
        return str(value)
    props = {
        name: getattr(value, name)
        for name in dir(type(value))
        if not name.startswith("_") and isinstance(getattr(type(value), name, None), property)
    }
    return {k: _plain(v, depth + 1) for k, v in props.items() if not callable(v)}


def _permission_rules(rules: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "grantee_type": getattr(rule.grantee, "tag_name", type(rule.grantee).__name__),
            "grantee_id": getattr(rule.grantee, "id", None),
            "capabilities": dict(rule.capabilities),
        }
        for rule in rules
    ]


class TscInventorySource:
    """Enumerates one site through TSC. Use inside ``with TscReader(connection) as reader``."""

    def __init__(self, connection: TableauConnection, reader: TscReader) -> None:
        self.server = connection.server_url
        self.site = connection.site
        self._srv: Any = reader._server
        self._tsc: Any = reader._tsc

    def _pager(self, endpoint: Any) -> Iterator[Any]:
        yield from self._tsc.Pager(endpoint)

    def _item(self, object_type: str, obj: Any, extra: dict[str, Any] | None = None) -> Item:
        data = _plain(obj)
        data.update(extra or {})
        updated = getattr(obj, "updated_at", None)
        return Item(
            type=object_type,
            luid=str(obj.id),
            name=str(getattr(obj, "name", "") or ""),
            updated_at=updated.isoformat() if isinstance(updated, datetime) else updated,
            data=data,
        )

    def items(self, object_type: str) -> Iterable[Item]:
        srv = self._srv
        if object_type == "users":
            return (self._item("users", u) for u in self._pager(srv.users))
        if object_type == "groups":
            return self._groups()
        if object_type == "projects":
            return self._projects()
        if object_type in {"datasources", "workbooks"}:
            return self._content(object_type)
        if object_type == "views":
            return (self._item("views", v) for v in self._pager(srv.views))
        if object_type == "schedules":
            schedules, _ = srv.schedules.get()
            return (self._item("schedules", s) for s in schedules)
        if object_type == "tasks":
            tasks, _ = srv.tasks.get()
            return (self._item("tasks", t) for t in tasks)
        if object_type == "subscriptions":
            return (self._item("subscriptions", s) for s in self._pager(srv.subscriptions))
        raise ValueError(f"unknown object type {object_type!r}")

    def _groups(self) -> Iterator[Item]:
        for group in self._pager(self._srv.groups):
            self._srv.groups.populate_users(group)
            members = [u.id for u in group.users]
            yield self._item("groups", group, {"member_ids": members})

    def _projects(self) -> Iterator[Item]:
        endpoint = self._srv.projects
        for project in self._pager(endpoint):
            endpoint.populate_permissions(project)
            extra: dict[str, Any] = {"permissions": _permission_rules(project.permissions)}
            for kind in ("workbook", "datasource", "flow"):
                populate = getattr(endpoint, f"populate_{kind}_default_permissions", None)
                if populate is not None:
                    populate(project)
                    rules = getattr(project, f"default_{kind}_permissions", [])
                    extra[f"default_{kind}_permissions"] = _permission_rules(rules)
            yield self._item("projects", project, extra)

    def _content(self, object_type: str) -> Iterator[Item]:
        endpoint = getattr(self._srv, object_type)
        for obj in self._pager(endpoint):
            endpoint.populate_connections(obj)
            endpoint.populate_permissions(obj)
            connections = [
                {
                    "id": c.id,
                    "type": c.connection_type,
                    "server": c.server_address,
                    "port": c.server_port,
                    "username": c.username,
                    "embed_password": c.embed_password,
                }
                for c in obj.connections
            ]
            extra = {"connections": connections, "permissions": _permission_rules(obj.permissions)}
            yield self._item(object_type, obj, extra)
