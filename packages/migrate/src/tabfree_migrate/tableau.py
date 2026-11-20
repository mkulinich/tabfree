"""Read-only access to Tableau Server and Tableau Cloud through tableauserverclient (TSC)."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol, Self

#: REST endpoints (TSC attribute names) whose totals the smoke test reports.
COUNTED_ENDPOINTS = ("projects", "workbooks", "views", "datasources", "flows", "users", "groups")

_METADATA_PROBE = "{ workbooksConnection(first: 1) { totalCount } }"


class ConfigError(ValueError):
    """Missing or invalid connection settings."""


class TableauAccessError(RuntimeError):
    """Signing in to Tableau failed, or the server refused a request."""


@dataclass(frozen=True)
class TableauConnection:
    """How to reach one Tableau site with a personal access token."""

    server_url: str
    site: str
    pat_name: str
    pat_secret: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> TableauConnection:
        required = ("TABLEAU_SERVER_URL", "TABLEAU_PAT_NAME", "TABLEAU_PAT_SECRET")
        missing = [name for name in required if not environ.get(name)]
        if missing:
            raise ConfigError(f"missing environment variables: {', '.join(missing)}")
        server_url = environ["TABLEAU_SERVER_URL"].rstrip("/")
        if not server_url.startswith(("https://", "http://")):
            raise ConfigError("TABLEAU_SERVER_URL must start with https:// (or http:// for labs)")
        return cls(
            server_url=server_url,
            site=environ.get("TABLEAU_SITE", ""),
            pat_name=environ["TABLEAU_PAT_NAME"],
            pat_secret=environ["TABLEAU_PAT_SECRET"],
        )


@dataclass(frozen=True)
class ServerInfo:
    product_version: str | None
    build_number: str | None
    rest_api_version: str | None


class TableauReader(Protocol):
    """The read-only operations the smoke test needs (TSC-backed in production, faked in tests)."""

    def server_info(self) -> ServerInfo: ...

    def count(self, endpoint: str) -> int | None:
        """Total items behind a REST endpoint, or None if this account may not list them."""
        ...

    def site_count(self) -> int | None:
        """Number of sites, or None when the account is not a server administrator."""
        ...

    def metadata_api_available(self) -> bool: ...

    def sample_names(self, endpoint: str, limit: int) -> list[str]: ...


class TscReader:
    """:class:`TableauReader` backed by tableauserverclient; use it as a context manager."""

    def __init__(self, connection: TableauConnection) -> None:
        self._connection = connection
        self._tsc: Any = None
        self._server: Any = None

    def __enter__(self) -> Self:
        import tableauserverclient

        # TSC's type hints are incomplete, so treat the library as dynamically typed.
        tsc: Any = tableauserverclient
        self._tsc = tsc
        auth = tsc.PersonalAccessTokenAuth(
            self._connection.pat_name, self._connection.pat_secret, site_id=self._connection.site
        )
        self._server = tsc.Server(self._connection.server_url, use_server_version=True)
        try:
            self._server.auth.sign_in(auth)
        except tsc.ServerResponseError as exc:
            raise TableauAccessError(f"sign-in failed: {exc}") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._server is not None:
            # A failed sign-out is harmless: the session expires on the server anyway.
            with suppress(self._tsc.ServerResponseError):
                self._server.auth.sign_out()

    def server_info(self) -> ServerInfo:
        info = self._server.server_info.get()
        return ServerInfo(
            product_version=getattr(info, "product_version", None),
            build_number=getattr(info, "build_number", None),
            rest_api_version=getattr(info, "rest_api_version", None),
        )

    def count(self, endpoint: str) -> int | None:
        try:
            _items, pagination = getattr(self._server, endpoint).get(
                self._tsc.RequestOptions(pagesize=1)
            )
        except self._tsc.ServerResponseError:
            return None
        return int(pagination.total_available)

    def site_count(self) -> int | None:
        return self.count("sites")

    def metadata_api_available(self) -> bool:
        try:
            result = self._server.metadata.query(_METADATA_PROBE)
        except self._tsc.ServerResponseError:
            return False
        return isinstance(result, dict) and not result.get("errors")

    def sample_names(self, endpoint: str, limit: int) -> list[str]:
        if limit <= 0:
            return []
        try:
            items, _pagination = getattr(self._server, endpoint).get(
                self._tsc.RequestOptions(pagesize=min(limit, 1000))
            )
        except self._tsc.ServerResponseError:
            return []
        return [str(item.name) for item in items[:limit]]
