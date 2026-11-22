"""``python -m tabfree_api.migration``: import a factory folder, repair, verify (batch, on the server).

    python -m tabfree_api.migration import factory/ --by admin [--create]
    python -m tabfree_api.migration repair --wave 1 --by admin [--max-iterations 3] [--target 0.85]
    python -m tabfree_api.migration verify --wave 1 --by admin
    python -m tabfree_api.migration access --by admin
    python -m tabfree_api.migration rls ITEM SAMPLES_DIR
    python -m tabfree_api.migration summary

Settings come from the environment like the server's (``TABFREE_DATABASE_URL``, ``TABFREE_SITE_ID``,
``TABFREE_AI_PROVIDER``...). Repairs call the configured model and spend tokens from the ``--by``
user's budget; run ``summary`` first to see how many items a wave holds.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tabfree_api.settings import Settings


def _context(settings: Settings) -> Any:
    from tabfree_api.main import create_app

    return create_app(settings).state.context


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tabfree_api.migration", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("import", help="load a factory folder into the site")
    p.add_argument("folder", type=Path)
    p.add_argument("--by", required=True, help="the administrator who owns the drafts")
    p.add_argument("--create", action="store_true", help="create the site if it does not exist")
    for name in ("repair", "verify"):
        p = commands.add_parser(name)
        p.add_argument("--wave", type=int)
        p.add_argument("--item", action="append", default=[])
        p.add_argument("--by", required=True)
        if name == "repair":
            p.add_argument("--max-iterations", type=int, default=3)
            p.add_argument("--target", type=float, default=0.85)
    p = commands.add_parser("access", help="access parity for every user with a Tableau reference")
    p.add_argument("--by", required=True)
    p = commands.add_parser("rls", help="RLS parity: SAMPLES/<user>/<query>.csv against TabFree")
    p.add_argument("item")
    p.add_argument("samples", type=Path)
    commands.add_parser("summary")
    args = parser.parse_args(argv)

    settings = Settings()
    if not settings.database_url:
        print("set TABFREE_DATABASE_URL: the migration factory needs the database", file=sys.stderr)
        return 2
    if args.command == "import":
        from tabfree_api.db import connect
        from tabfree_api.migration.importer import import_factory

        engine = connect(settings.database_url.get_secret_value())
        imported = import_factory(
            engine, args.folder, by=args.by, site_id=settings.site_id, create=args.create
        )
        print(json.dumps(imported.to_dict(), indent=2))
        return 0 if not imported.errors else 1

    ctx = _context(settings)
    from tabfree_api.migration import verify
    from tabfree_api.migration.store import MigrationStore

    store = MigrationStore(ctx.site.engine, ctx.site.id)
    if args.command == "summary":
        print(json.dumps(store.summary(), indent=2))
        return 0
    if args.command == "rls":
        print(json.dumps(verify.rls_parity(ctx, store, args.item, args.samples), indent=2, default=str))
        return 0
    info = verify.session_for(ctx.site, args.by)
    if args.command == "access":
        report = verify.access_parity(ctx.site, store)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok", True) else 1
    items = args.item or [
        i["id"]
        for i in store.list_items(wave=args.wave)
        if i["status"] in ("converted", "review", "changes_requested") and i.get("draft_id")
    ]
    if args.command == "verify":
        for item_id in items:
            data = verify.data_parity(ctx, store, item_id, info)
            visual = verify.visual_parity(ctx, store, item_id, info)
            fidelity = verify.refresh_fidelity(
                ctx, store, item_id, info,
                data=data if data.get("status") == "done" else None,
                visual=visual if visual.get("status") == "done" else None,
            )  # fmt: skip
            print(f"{item_id}: {fidelity['grade']} {fidelity['score']}")
        return 0
    from tabfree_api.migration.repair import RepairAgent

    agent = RepairAgent(ctx, max_iterations=args.max_iterations, target=args.target)
    for item_id in items:
        result = agent.run(item_id, info)
        score = result.fidelity.get("score")
        print(f"{item_id}: {result.status} after {result.iterations} round(s), score {score}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
