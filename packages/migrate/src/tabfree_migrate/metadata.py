"""Metadata API harvest (BUILD_PLAN P1.2): lineage, formulas, custom SQL and RLS flags.

The Tableau Metadata API is GraphQL, reached through TSC's ``server.metadata.query``. It is read
only: every request is a ``query`` (never a ``mutation``). Each entity type is fetched through its
``<entity>Connection`` with cursor pagination. Tableau aborts a response at 20,000 nodes, so page
sizes start well below that (nested lists count towards the limit) and halve automatically when the
server still reports the node limit.

What is collected, per site:

- workbooks → sheets/dashboards, embedded and published data sources they use
- sheets → upstream fields and data sources
- calculated fields with their **formulas** and the fields they reference
- custom SQL (the query text), its tables and database
- database tables and databases (connection type, host for database servers)
- published and embedded data sources, including ``hasUserReference`` (user functions such as
  ``USERNAME()``, i.e. row-level security) and ``hasExtracts``

Objects land in SQLite tables beside the P1.1 inventory (``metadata_objects``) and the references
between them become edges (``metadata_edges``) so lineage can be walked with plain SQL. Progress is
checkpointed per page, so ``resume=True`` continues an interrupted harvest from the last cursor.

**Unverified:** the GraphQL field names below follow Tableau's published Metadata API reference
(2024-2026) but have not been run against a live server. A field that a server version does not
know makes that entity's query fail; the failure is recorded and the other entities carry on.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

#: Tableau's hard per-response limit. Page sizes stay far below it.
NODE_LIMIT = 20_000


class MetadataClient(Protocol):
    """Runs one GraphQL query. Implemented with TSC; faked in tests."""

    server: str
    site: str

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...


class MetadataError(RuntimeError):
    """The Metadata API rejected a query or returned no data."""


@dataclass(frozen=True)
class Relation:
    """A nested reference inside a node that becomes a lineage edge."""

    path: str
    relation: str
    target: str


@dataclass(frozen=True)
class EntitySpec:
    """One GraphQL connection to page through."""

    name: str
    connection: str
    fields: str
    page_size: int
    relations: tuple[Relation, ...] = ()

    def query(self) -> str:
        return (
            f"query tabfree_{self.name}($first: Int, $afterToken: String) {{\n"
            f"  {self.connection}(first: $first, after: $afterToken) {{\n"
            f"    nodes {{ {' '.join(self.fields.split())} }}\n"
            "    pageInfo { hasNextPage endCursor }\n"
            "    totalCount\n"
            "  }\n"
            "}"
        )


_REF = "id luid name"

ENTITIES: tuple[EntitySpec, ...] = (
    EntitySpec(
        "databases",
        "databasesConnection",
        "id luid name connectionType isEmbedded __typename"
        " ... on DatabaseServer { hostName port service }"
        " ... on File { filePath }",
        page_size=500,
    ),
    EntitySpec(
        "databaseTables",
        "databaseTablesConnection",
        f"id luid name schema fullName connectionType isEmbedded database {{ {_REF} connectionType }}",
        page_size=500,
        relations=(Relation("database", "in_database", "databases"),),
    ),
    EntitySpec(
        "customSQLTables",
        "customSQLTablesConnection",
        "id name query isUnsupportedCustomSql connectionType"
        f" database {{ {_REF} connectionType }} tables {{ id name fullName }}"
        " downstreamWorkbooks { id luid name } downstreamDatasources { id name __typename }",
        page_size=100,
        relations=(
            Relation("database", "in_database", "databases"),
            Relation("tables", "reads_table", "databaseTables"),
            Relation("downstreamWorkbooks", "feeds_workbook", "workbooks"),
        ),
    ),
    EntitySpec(
        "publishedDatasources",
        "publishedDatasourcesConnection",
        "id luid name projectName createdAt updatedAt hasUserReference hasExtracts isCertified"
        " extractLastRefreshTime owner { luid username }"
        " upstreamTables { id name } upstreamDatabases { id name } downstreamWorkbooks { id luid name }",
        page_size=100,
        relations=(
            Relation("upstreamTables", "reads_table", "databaseTables"),
            Relation("upstreamDatabases", "reads_database", "databases"),
        ),
    ),
    EntitySpec(
        "embeddedDatasources",
        "embeddedDatasourcesConnection",
        "id name hasUserReference hasExtracts workbook { id luid name }"
        f" upstreamDatasources {{ {_REF} }} upstreamTables {{ id name }} upstreamDatabases {{ id name }}",
        page_size=100,
        relations=(
            Relation("workbook", "in_workbook", "workbooks"),
            Relation("upstreamDatasources", "uses_datasource", "publishedDatasources"),
            Relation("upstreamTables", "reads_table", "databaseTables"),
            Relation("upstreamDatabases", "reads_database", "databases"),
        ),
    ),
    EntitySpec(
        "workbooks",
        "workbooksConnection",
        "id luid name projectName projectLuid createdAt updatedAt owner { luid username }"
        f" sheets {{ {_REF} path }} dashboards {{ {_REF} path }} embeddedDatasources {{ id name }}"
        f" upstreamDatasources {{ {_REF} }}",
        page_size=50,
        relations=(
            Relation("sheets", "has_sheet", "sheets"),
            Relation("dashboards", "has_dashboard", "dashboards"),
            Relation("embeddedDatasources", "embeds_datasource", "embeddedDatasources"),
            Relation("upstreamDatasources", "uses_datasource", "publishedDatasources"),
        ),
    ),
    EntitySpec(
        "sheets",
        "sheetsConnection",
        f"id luid name path createdAt updatedAt workbook {{ {_REF} }}"
        f" upstreamFields {{ id name __typename }} upstreamDatasources {{ {_REF} }}",
        page_size=50,
        relations=(
            Relation("workbook", "in_workbook", "workbooks"),
            Relation("upstreamFields", "uses_field", "fields"),
            Relation("upstreamDatasources", "uses_datasource", "publishedDatasources"),
        ),
    ),
    EntitySpec(
        "calculatedFields",
        "calculatedFieldsConnection",
        "id name formula dataType role isHidden datasource { id name __typename }"
        " fields { id name __typename } downstreamSheets { id luid name }",
        page_size=200,
        relations=(
            Relation("datasource", "in_datasource", "datasources"),
            Relation("fields", "references_field", "fields"),
            Relation("downstreamSheets", "used_by_sheet", "sheets"),
        ),
    ),
)

ENTITY_NAMES = tuple(spec.name for spec in ENTITIES)


@dataclass
class EntityStats:
    count: int = 0
    pages: int = 0
    total: int | None = None
    page_size: int = 0
    complete: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class HarvestStats:
    entities: dict[str, EntityStats] = field(default_factory=dict)

    @property
    def errors(self) -> dict[str, str]:
        return {name: s.error for name, s in self.entities.items() if s.error}

    def to_dict(self) -> dict[str, Any]:
        return {name: s.__dict__ for name, s in self.entities.items()}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_objects (
  server TEXT NOT NULL, site TEXT NOT NULL, entity TEXT NOT NULL, id TEXT NOT NULL,
  luid TEXT, name TEXT, content_hash TEXT NOT NULL, data TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_run INTEGER NOT NULL,
  PRIMARY KEY (server, site, entity, id)
);
CREATE TABLE IF NOT EXISTS metadata_edges (
  server TEXT NOT NULL, site TEXT NOT NULL, src_entity TEXT NOT NULL, src_id TEXT NOT NULL,
  relation TEXT NOT NULL, dst_entity TEXT NOT NULL, dst_id TEXT NOT NULL,
  PRIMARY KEY (server, site, src_entity, src_id, relation, dst_id)
);
CREATE INDEX IF NOT EXISTS metadata_edges_dst ON metadata_edges (server, site, dst_id);
CREATE TABLE IF NOT EXISTS metadata_checkpoints (
  server TEXT NOT NULL, site TEXT NOT NULL, entity TEXT NOT NULL,
  cursor TEXT, complete INTEGER NOT NULL, run_id INTEGER NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (server, site, entity)
);
CREATE TABLE IF NOT EXISTS metadata_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, site TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, stats TEXT
);
"""


@dataclass(frozen=True)
class Edge:
    src_entity: str
    src_id: str
    relation: str
    dst_entity: str
    dst_id: str


class MetadataStore:
    """SQLite tables for the harvest; may share a file with :class:`InventoryStore`."""

    def __init__(self, path: Path | str) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def start_run(self, server: str, site: str) -> int:
        cur = self.db.execute(
            "INSERT INTO metadata_runs (server, site, started_at) VALUES (?, ?, ?)", (server, site, _now())
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, stats: HarvestStats) -> None:
        self.db.execute(
            "UPDATE metadata_runs SET finished_at = ?, stats = ? WHERE id = ?",
            (_now(), json.dumps(stats.to_dict()), run_id),
        )
        self.db.commit()

    def checkpoint(self, server: str, site: str, entity: str) -> tuple[str | None, bool] | None:
        row = self.db.execute(
            "SELECT cursor, complete FROM metadata_checkpoints WHERE server = ? AND site = ? AND entity = ?",
            (server, site, entity),
        ).fetchone()
        return None if row is None else (row[0], bool(row[1]))

    def save_checkpoint(
        self, server: str, site: str, entity: str, cursor: str | None, complete: bool, run_id: int
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO metadata_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            (server, site, entity, cursor, int(complete), run_id, _now()),
        )

    def reset_checkpoint(self, server: str, site: str, entity: str) -> None:
        self.db.execute(
            "DELETE FROM metadata_checkpoints WHERE server = ? AND site = ? AND entity = ?",
            (server, site, entity),
        )

    def upsert(self, server: str, site: str, spec: EntitySpec, node: dict[str, Any], run_id: int) -> None:
        node_id = str(node["id"])
        data = json.dumps(node, sort_keys=True)
        digest = hashlib.sha256(data.encode()).hexdigest()
        now = _now()
        self.db.execute(
            "INSERT INTO metadata_objects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (server, site, entity, id) DO UPDATE SET luid = excluded.luid, "
            "name = excluded.name, content_hash = excluded.content_hash, data = excluded.data, "
            "last_seen = excluded.last_seen, last_run = excluded.last_run",
            (
                server,
                site,
                spec.name,
                node_id,
                node.get("luid"),
                node.get("name"),
                digest,
                data,
                now,
                now,
                run_id,
            ),
        )
        self.db.execute(
            "DELETE FROM metadata_edges WHERE server = ? AND site = ? AND src_entity = ? AND src_id = ?",
            (server, site, spec.name, node_id),
        )
        self.db.executemany(
            "INSERT OR IGNORE INTO metadata_edges VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (server, site, e.src_entity, e.src_id, e.relation, e.dst_entity, e.dst_id)
                for e in edges_of(spec, node)
            ],
        )

    def objects(self, entity: str) -> Iterator[dict[str, Any]]:
        for (data,) in self.db.execute(
            "SELECT data FROM metadata_objects WHERE entity = ? ORDER BY name, id", (entity,)
        ):
            yield json.loads(data)

    def edges(self, relation: str | None = None) -> list[Edge]:
        sql = "SELECT src_entity, src_id, relation, dst_entity, dst_id FROM metadata_edges"
        params: tuple[str, ...] = ()
        if relation:
            sql += " WHERE relation = ?"
            params = (relation,)
        return [Edge(*row) for row in self.db.execute(sql + " ORDER BY 1, 2, 3, 5", params)]

    def rls_datasources(self) -> list[dict[str, Any]]:
        """Published and embedded data sources whose calculations reference the user (RLS)."""
        found = []
        for entity in ("publishedDatasources", "embeddedDatasources"):
            found += [
                dict(node, entity=entity) for node in self.objects(entity) if node.get("hasUserReference")
            ]
        return found

    def formulas(self) -> list[tuple[str, str, str]]:
        """``(datasource name, field name, formula)`` for every calculated field."""
        rows = []
        for node in self.objects("calculatedFields"):
            datasource = node.get("datasource") or {}
            rows.append((str(datasource.get("name") or ""), str(node.get("name")), str(node.get("formula"))))
        return sorted(rows)


def edges_of(spec: EntitySpec, node: dict[str, Any]) -> list[Edge]:
    src = str(node["id"])
    found = []
    for rel in spec.relations:
        value = node.get(rel.path)
        targets = value if isinstance(value, list) else [value]
        for target in targets:
            if isinstance(target, dict) and target.get("id"):
                found.append(Edge(spec.name, src, rel.relation, rel.target, str(target["id"])))
    return found


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_node_limit(errors: Iterable[Any]) -> bool:
    for error in errors:
        text = json.dumps(error).lower()
        if "node limit" in text or "node_limit" in text:
            return True
    return False


def _messages(errors: Iterable[Any]) -> list[str]:
    return [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors]


def pages(
    client: MetadataClient, spec: EntitySpec, cursor: str | None = None, page_size: int | None = None
) -> Iterator[tuple[list[dict[str, Any]], str | None, bool, int | None, list[str], int]]:
    """Yield ``(nodes, end_cursor, has_next, total, warnings, page_size)`` per page.

    Halves the page size and retries the same cursor whenever the node limit is reported.
    """
    size = min(page_size or spec.page_size, NODE_LIMIT // 10)
    query = spec.query()
    while True:
        result = client.query(query, {"first": size, "afterToken": cursor})
        errors = result.get("errors") or []
        if errors and _is_node_limit(errors):
            if size == 1:
                raise MetadataError(f"{spec.name}: a single node exceeds the node limit")
            size = max(1, size // 2)
            continue
        data = (result.get("data") or {}).get(spec.connection)
        if data is None:
            raise MetadataError("; ".join(_messages(errors)) or f"{spec.name}: no data in response")
        info = data.get("pageInfo") or {}
        nodes = [n for n in data.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
        has_next = bool(info.get("hasNextPage"))
        cursor = info.get("endCursor")
        yield nodes, cursor, has_next, data.get("totalCount"), _messages(errors), size
        if not has_next or not cursor:
            return


def harvest(
    client: MetadataClient,
    store: MetadataStore,
    entities: Iterable[str] = ENTITY_NAMES,
    resume: bool = False,
    page_size: int | None = None,
) -> HarvestStats:
    """Page every entity into ``store``. A failing entity is recorded and the next one starts.

    With ``resume``, entities completed in an earlier run are skipped and an interrupted one
    restarts from its last saved cursor; otherwise every entity starts from the beginning.
    """
    specs = {spec.name: spec for spec in ENTITIES}
    unknown = [name for name in entities if name not in specs]
    if unknown:
        raise ValueError(f"unknown metadata entities: {', '.join(unknown)}")
    server, site = client.server, client.site
    stats = HarvestStats()
    run_id = store.start_run(server, site)
    for name in entities:
        spec = specs[name]
        entity_stats = stats.entities.setdefault(name, EntityStats(page_size=page_size or spec.page_size))
        cursor: str | None = None
        saved = store.checkpoint(server, site, name) if resume else None
        if saved is not None:
            cursor, complete = saved
            if complete:
                entity_stats.complete = True
                continue
        elif not resume:
            store.reset_checkpoint(server, site, name)
        try:
            for nodes, end, has_next, total, warnings, size in pages(client, spec, cursor, page_size):
                for node in nodes:
                    store.upsert(server, site, spec, node, run_id)
                entity_stats.count += len(nodes)
                entity_stats.pages += 1
                entity_stats.total = total
                entity_stats.page_size = size
                entity_stats.warnings += warnings[:5]
                store.save_checkpoint(server, site, name, end, not has_next, run_id)
                store.db.commit()
            entity_stats.complete = True
        except Exception as exc:
            store.db.commit()
            entity_stats.error = f"{type(exc).__name__}: {exc}"
    store.finish_run(run_id, stats)
    return stats


class TscMetadataClient:
    """:class:`MetadataClient` over TSC. Use inside ``with TscReader(connection) as reader``.

    Unverified against a live server. Retries 429/5xx with backoff.
    """

    def __init__(self, server_url: str, site: str, tsc_server: Any) -> None:
        self.server = server_url
        self.site = site
        self._srv = tsc_server

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        from tabfree_migrate.retry import with_retries

        if query.lstrip().startswith("mutation"):
            raise ValueError("the Metadata API harvest only runs queries")
        result = with_retries(lambda: self._srv.metadata.query(query, variables))
        if not isinstance(result, dict):
            raise MetadataError("unexpected Metadata API response")
        return result
