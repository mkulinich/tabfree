"""VizQL Data Service oracle (BUILD_PLAN P2.9): ask Tableau itself what a calculation returns.

Tableau Server 2025.1+ and Tableau Cloud expose ``POST /api/v1/vizql-data-service/query-datasource``:
given a published data source, fields (captions with an aggregation) and ad-hoc calculations, it
returns rows computed by VizQL. Running the same calculation through TabFree on the converted data and
comparing the two is the calc oracle; it needs a live server and is read-only.

Unverified against a live server: request and response shapes follow Tableau's published API
reference (fields with ``fieldCaption``/``function`` or ``calculation``/``fieldAlias``; rows in
``data``). Check them on our server before trusting a mismatch.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tabfree_migrate.tableau import TableauAccessError

QUERY_PATH = "/api/v1/vizql-data-service/query-datasource"
MAX_ROWS = 100_000


class Transport(Protocol):
    def post_json(
        self, url: str, body: Mapping[str, Any], headers: Mapping[str, str]
    ) -> Mapping[str, Any]: ...


class UrllibTransport:
    timeout_s = 120.0

    def post_json(self, url: str, body: Mapping[str, Any], headers: Mapping[str, str]) -> Mapping[str, Any]:
        if not url.startswith("https://"):
            raise TableauAccessError("the VizQL Data Service must be reached over https")
        request = urllib.request.Request(  # noqa: S310 - https enforced above
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise TableauAccessError(f"VizQL Data Service refused the query ({exc.code})") from None
        if not isinstance(data, dict):
            raise TableauAccessError("unexpected VizQL Data Service response")
        return data


@dataclass(frozen=True)
class Field:
    """A dimension (``function`` None), an aggregated field, or an ad-hoc calculation."""

    caption: str | None = None
    function: str | None = None  # SUM, AVG, COUNTD, MIN, MAX, ...
    calculation: str | None = None
    alias: str | None = None

    def to_json(self) -> dict[str, Any]:
        if self.calculation is not None:
            return {"calculation": self.calculation, "fieldAlias": self.alias or "calc"}
        if not self.caption:
            raise ValueError("a field needs a caption or a calculation")
        out: dict[str, Any] = {"fieldCaption": self.caption}
        if self.function:
            out["function"] = self.function
        if self.alias:
            out["fieldAlias"] = self.alias
        return out


class VizqlDataService:
    """Read-only client. ``auth_token`` is the REST session token (X-Tableau-Auth) from signing in
    with a personal access token; it is never logged."""

    def __init__(self, server_url: str, auth_token: str, transport: Transport | None = None) -> None:
        self._url = server_url.rstrip("/") + QUERY_PATH
        self._headers = {"X-Tableau-Auth": auth_token, "Accept": "application/json"}
        self._transport = transport or UrllibTransport()

    def query(
        self,
        datasource_luid: str,
        fields: Sequence[Field],
        filters: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "datasource": {"datasourceLuid": datasource_luid},
            "query": {"fields": [f.to_json() for f in fields]},
            "options": {"returnFormat": "OBJECTS", "debug": False},
        }
        if filters:
            body["query"]["filters"] = list(filters)
        response = self._transport.post_json(self._url, body, self._headers)
        rows = response.get("data")
        if not isinstance(rows, list):
            raise TableauAccessError(f"VizQL Data Service error: {response.get('message') or 'no data'}")
        if len(rows) > MAX_ROWS:
            raise TableauAccessError(f"VizQL Data Service returned more than {MAX_ROWS} rows")
        return [r for r in rows if isinstance(r, dict)]

    def evaluate(self, datasource_luid: str, formula: str, by: Sequence[str] = ()) -> list[dict[str, Any]]:
        """Tableau's answer for ``formula``, optionally broken down by dimension captions."""
        fields = [Field(caption=d) for d in by] + [Field(calculation=formula, alias="value")]
        return self.query(datasource_luid, fields)


def compare_calc(
    tableau_rows: Sequence[Mapping[str, Any]],
    tabfree_rows: Sequence[Mapping[str, Any]],
    by: Sequence[str] = (),
    rel_tol: float = 1e-6,
) -> list[str]:
    """Differences between Tableau's and TabFree's values for one calculation (empty when equal)."""
    from tabfree_compiler.parity import normalise

    def keyed(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, ...], Any]:
        return {tuple(normalise(r.get(d)) for d in by): normalise(r.get("value")) for r in rows}

    expected, actual = keyed(tableau_rows), keyed(tabfree_rows)
    problems = []
    for key in sorted(set(expected) | set(actual), key=str):
        e, a = expected.get(key), actual.get(key)
        same = (
            e == a
            if not (isinstance(e, float) and isinstance(a, float))
            else abs(e - a) <= rel_tol * max(abs(e), abs(a), 1.0)
        )
        if not same:
            problems.append(f"{key}: Tableau {e!r}, TabFree {a!r}")
    return problems
