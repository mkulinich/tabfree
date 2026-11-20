"""Test doubles for code that talks to Tableau. Used by this package's tests and by later phases."""

from __future__ import annotations

from tabfree_migrate.tableau import ServerInfo

DEFAULT_COUNTS: dict[str, int | None] = {
    "projects": 12,
    "workbooks": 340,
    "views": 2100,
    "datasources": 85,
    "flows": 0,
    "users": 450,
    "groups": 31,
}


class FakeReader:
    """In-memory stand-in for a signed-in Tableau session (implements ``TableauReader``)."""

    def __init__(
        self,
        counts: dict[str, int | None] | None = None,
        sites: int | None = 2,
        metadata: bool = True,
        info: ServerInfo | None = None,
    ) -> None:
        self.counts = DEFAULT_COUNTS if counts is None else counts
        self.sites = sites
        self.metadata = metadata
        self.info = info or ServerInfo("2026.3.0", "20263.26.1015.0800", "3.30")

    def server_info(self) -> ServerInfo:
        return self.info

    def count(self, endpoint: str) -> int | None:
        return self.counts.get(endpoint)

    def site_count(self) -> int | None:
        return self.sites

    def metadata_api_available(self) -> bool:
        return self.metadata

    def sample_names(self, endpoint: str, limit: int) -> list[str]:
        return [f"{endpoint}-{i}" for i in range(1, limit + 1)]
