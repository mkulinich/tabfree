"""Load test (BUILD_PLAN P7.2): many people opening dashboards at once, the way the portal does it.

    uv run python -m tabfree_api.loadtest https://staging.tabfree.example --tokens tokens.txt \
        --users 1000 --minutes 15 --burst 50 --admin-token-file admin.txt --report report.json

Each virtual person signs in with a bearer token from ``--tokens`` (one personal access token per
line; create them for test accounts on a staging site, never production people), then repeatedly:
opens a dashboard they can see (the bundle, then every query at once, as the browser does), changes a
filter half of the time (the filter's domain, then the queries again), and waits a few seconds. The
**burst** phase has ``--burst`` people open the same dashboard at the same moment (a meeting starts).

The report gives latency percentiles per step, the time to open a whole dashboard, errors by status,
throughput, and, with an administrator's token, the server's own figures for the run (cache hit rate,
query time) from ``/api/admin/usage``. It checks them against targets (``--targets``) and exits 1 when
one is missed, so it can gate a release.

Needs ``httpx2`` (in the development dependencies). Generates real load: point it at staging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CSRF = {"X-TabFree-Request": "1"}
DEFAULT_TARGETS = {
    "open_p95_s": 4.0,  # a whole dashboard, all its queries
    "burst_open_p95_s": 8.0,  # 50 people opening the same dashboard at once
    "query_p95_s": 2.5,
    "error_rate": 0.01,
    "cache_hit_rate": 0.6,  # from the server, when an admin token is given
}


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))]


@dataclass
class Stats:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: Counter[str] = field(default_factory=Counter)
    requests: int = 0
    opens: list[float] = field(default_factory=list)
    burst_opens: list[float] = field(default_factory=list)

    def record(self, step: str, seconds: float, status: int) -> None:
        self.requests += 1
        self.latencies[step].append(seconds)
        if status >= 400:
            self.errors[f"{step} {status}"] += 1

    def summary(self, elapsed: float) -> dict[str, Any]:
        steps = {
            step: {
                "count": len(v),
                "p50_s": round(percentile(v, 0.5), 3),
                "p95_s": round(percentile(v, 0.95), 3),
                "p99_s": round(percentile(v, 0.99), 3),
                "mean_s": round(statistics.fmean(v), 3) if v else 0.0,
            }
            for step, v in sorted(self.latencies.items())
        }
        failed = sum(self.errors.values())
        return {
            "requests": self.requests,
            "throughput_rps": round(self.requests / elapsed, 1) if elapsed else 0.0,
            "error_rate": round(failed / self.requests, 4) if self.requests else 0.0,
            "errors": dict(self.errors.most_common(20)),
            "steps": steps,
            "open": {
                "count": len(self.opens),
                "p50_s": round(percentile(self.opens, 0.5), 3),
                "p95_s": round(percentile(self.opens, 0.95), 3),
            },
            "burst_open": {
                "count": len(self.burst_opens),
                "p95_s": round(percentile(self.burst_opens, 0.95), 3),
            },
        }


class Person:
    def __init__(self, client: Any, token: str, stats: Stats, *, think_s: float, rng: random.Random) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}", **CSRF}
        self.stats = stats
        self.think_s = think_s
        self.rng = rng
        self.dashboards: list[str] = []

    async def call(self, step: str, method: str, path: str, body: Any = None) -> Any:
        started = time.perf_counter()
        try:
            response = await self.client.request(method, path, headers=self.headers, json=body)
            status = response.status_code
        except Exception:
            self.stats.record(step, time.perf_counter() - started, 599)
            return None
        self.stats.record(step, time.perf_counter() - started, status)
        if status >= 400:
            return None
        return response.json()

    async def discover(self) -> None:
        listed = await self.call("list", "GET", "/api/dashboards")
        self.dashboards = [d["id"] for d in listed or [] if isinstance(d, dict) and "id" in d]

    async def open(self, dashboard: str, *, burst: bool = False) -> None:
        started = time.perf_counter()
        bundle = await self.call("bundle", "GET", f"/api/dashboards/{dashboard}")
        if bundle is None:
            return
        manifest = bundle.get("manifest", {})
        queries = list(manifest.get("queries", {}))
        await asyncio.gather(
            *(
                self.call("query", "POST", f"/api/dashboards/{dashboard}/query", {"query": q, "state": {}})
                for q in queries
            )
        )
        (self.stats.burst_opens if burst else self.stats.opens).append(time.perf_counter() - started)
        filters = [n for n, f in (manifest.get("filters") or {}).items() if f.get("kind") == "categorical"]
        if filters and self.rng.random() < 0.5:
            name = self.rng.choice(filters)
            domain = await self.call(
                "domain", "POST", f"/api/dashboards/{dashboard}/domain", {"filter": name, "state": {}}
            )
            rows = (domain or {}).get("rows") or []
            if rows:
                member = self.rng.choice(rows)[0]
                state = {"filters": {name: {"members": [member]}}}
                targets = [
                    q
                    for q in queries
                    if manifest["filters"][name].get("appliesTo", "all") in ("all",)
                    or q in manifest["filters"][name]["appliesTo"]
                ]
                await asyncio.gather(
                    *(
                        self.call(
                            "query_filtered",
                            "POST",
                            f"/api/dashboards/{dashboard}/query",
                            {"query": q, "state": state},
                        )
                        for q in targets
                    )
                )

    async def run(self, until: float) -> None:
        await self.discover()
        await asyncio.sleep(self.rng.uniform(0, self.think_s))  # ramp: people do not all start at once
        while time.monotonic() < until and self.dashboards:
            await self.open(self.rng.choice(self.dashboards))
            await asyncio.sleep(self.rng.expovariate(1 / self.think_s))


async def run(
    client: Any,
    tokens: Sequence[str],
    *,
    users: int,
    seconds: float,
    burst: int,
    think_s: float = 8.0,
    seed: int = 7,
    admin_token: str | None = None,
) -> dict[str, Any]:
    stats = Stats()
    rng = random.Random(seed)  # noqa: S311 - load simulation, not security
    people = [
        Person(
            client,
            tokens[i % len(tokens)],
            stats,
            think_s=think_s,
            rng=random.Random(rng.random()),  # noqa: S311
        )
        for i in range(users)
    ]
    started = time.monotonic()
    if burst and people:
        # The burst first: a dashboard everyone in the burst can see, opened at the same moment.
        group = people[:burst]
        await asyncio.gather(*(p.discover() for p in group))
        shared = set.intersection(*(set(p.dashboards) for p in group)) if group else set()
        if shared:
            target = sorted(shared)[0]
            await asyncio.gather(*(p.open(target, burst=True) for p in group))
    steady = time.monotonic() + seconds  # the steady phase lasts `seconds` after the burst
    await asyncio.gather(*(p.run(steady) for p in people))
    report = stats.summary(time.monotonic() - started)
    report["users"] = users
    if admin_token:
        response = await client.get(
            "/api/admin/usage", params={"days": 1}, headers={"Authorization": f"Bearer {admin_token}"}
        )
        if response.status_code == 200:
            server = response.json()["queries"]
            report["server"] = {
                k: server.get(k) for k in ("count", "p50Ms", "p95Ms", "cacheHitRate", "scannedBytes")
            }
    return report


def check(report: dict[str, Any], targets: dict[str, float]) -> list[str]:
    misses = []
    query = report["steps"].get("query", {})
    measured = {
        "open_p95_s": report["open"]["p95_s"],
        "burst_open_p95_s": report["burst_open"]["p95_s"],
        "query_p95_s": query.get("p95_s", 0.0),
        "error_rate": report["error_rate"],
    }
    server = report.get("server") or {}
    for key, value in measured.items():
        if key in targets and value > targets[key]:
            misses.append(f"{key}: {value} > {targets[key]}")
    rate = server.get("cacheHitRate")
    if "cache_hit_rate" in targets and rate is not None and rate < targets["cache_hit_rate"]:
        misses.append(f"cache_hit_rate: {rate} < {targets['cache_hit_rate']}")
    return misses


def main(argv: Sequence[str] | None = None) -> int:
    import httpx2 as httpx

    parser = argparse.ArgumentParser(prog="python -m tabfree_api.loadtest", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument("base_url")
    parser.add_argument("--tokens", type=Path, required=True, help="one bearer token per line")
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--burst", type=int, default=50)
    parser.add_argument("--think", type=float, default=8.0, help="mean seconds between dashboards")
    parser.add_argument("--admin-token-file", type=Path)
    parser.add_argument("--targets", type=Path, help="JSON overriding the default targets")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    tokens = [line.strip() for line in args.tokens.read_text().splitlines() if line.strip()]
    if not tokens:
        print("no tokens", file=sys.stderr)
        return 2
    admin = args.admin_token_file.read_text().strip() if args.admin_token_file else None
    targets = {**DEFAULT_TARGETS, **(json.loads(args.targets.read_text()) if args.targets else {})}
    limits = httpx.Limits(
        max_connections=max(100, args.users), max_keepalive_connections=max(100, args.users)
    )

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=60, limits=limits) as client:
            return await run(client, tokens, users=args.users, seconds=args.minutes * 60, burst=args.burst,
                             think_s=args.think, admin_token=admin)  # fmt: skip

    report = asyncio.run(go())
    report["targets"] = targets
    report["misses"] = check(report, targets)
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(text + "\n")
    print(text)
    return 1 if report["misses"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
