"""The ``smoke`` check: what can this token see on Tableau Server? (BUILD_PLAN P0.6)"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from tabfree_migrate.tableau import COUNTED_ENDPOINTS, ServerInfo, TableauConnection, TableauReader

SAMPLED_ENDPOINTS = ("projects", "workbooks")


@dataclass(frozen=True)
class SmokeReport:
    server_url: str
    site: str
    server: ServerInfo
    counts: dict[str, int | None]
    site_count: int | None
    metadata_api: bool
    samples: dict[str, list[str]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_smoke(reader: TableauReader, connection: TableauConnection, sample: int = 0) -> SmokeReport:
    return SmokeReport(
        server_url=connection.server_url,
        site=connection.site,
        server=reader.server_info(),
        counts={endpoint: reader.count(endpoint) for endpoint in COUNTED_ENDPOINTS},
        site_count=reader.site_count(),
        metadata_api=reader.metadata_api_available(),
        samples={ep: reader.sample_names(ep, sample) for ep in SAMPLED_ENDPOINTS} if sample else {},
    )


def _show(value: object) -> str:
    return "n/a (not permitted)" if value is None else str(value)


def format_report(report: SmokeReport) -> str:
    site = report.site or "(Default)"
    server = report.server
    metadata = "available" if report.metadata_api else "NOT available (enable it for P1.2)"
    lines = [
        f"Server       {report.server_url}",
        f"Site         {site}",
        f"Version      {_show(server.product_version)} (build {_show(server.build_number)})",
        f"REST API     {_show(server.rest_api_version)}",
        f"Sites        {_show(report.site_count)}",
        f"Metadata API {metadata}",
        "",
    ]
    lines += [f"{endpoint.capitalize():<12} {_show(n)}" for endpoint, n in report.counts.items()]
    for endpoint, names in report.samples.items():
        lines += ["", f"First {len(names)} {endpoint}:"]
        lines += [f"  - {name}" for name in names]
    return "\n".join(lines)
