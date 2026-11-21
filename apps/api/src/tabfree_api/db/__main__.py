"""Database administration: ``python -m tabfree_api.db <command>``.

The URL comes from ``TABFREE_DATABASE_URL`` (inject it, it carries a password), for example:

    secret run -e TABFREE_DATABASE_URL="AWS - Aurora URL - TabFree" -- uv run python -m tabfree_api.db upgrade
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from tabfree_api.db import connect, downgrade, upgrade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tabfree_api.db")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade", help="apply migrations")
    up.add_argument("revision", nargs="?", default="head")
    down = sub.add_parser("downgrade", help="roll migrations back")
    down.add_argument("revision")
    imp = sub.add_parser("import-site", help="import a Phase 1 site file as a new site")
    imp.add_argument("site_file", type=Path)
    site = sub.add_parser("create-site", help="create an empty site")
    site.add_argument("site_id")
    site.add_argument("--name")
    admin = sub.add_parser("create-admin", help="a local site administrator (password read from stdin)")
    admin.add_argument("site_id")
    admin.add_argument("username")
    args = parser.parse_args(argv)

    url = os.environ.get("TABFREE_DATABASE_URL")
    if not url:
        print("set TABFREE_DATABASE_URL", file=sys.stderr)
        return 2
    engine = connect(url)
    if args.command == "upgrade":
        upgrade(engine, args.revision)
    elif args.command == "downgrade":
        downgrade(engine, args.revision)
    elif args.command == "import-site":
        from tabfree_api.content import Site
        from tabfree_api.db.seed import import_site

        imported = import_site(engine, Site(args.site_file))
        print(
            f"imported site {imported.id!r}: {len(imported.dashboards)} dashboards, "
            f"{len(imported.models)} models"
        )
    elif args.command == "create-site":
        from tabfree_api.db.seed import create_site

        create_site(engine, args.site_id, args.name or args.site_id)
    elif args.command == "create-admin":
        from tabfree_api.authz import SiteRole
        from tabfree_api.db.store import DbSite, LocalAccounts

        password = (
            sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass("Password: ")
        )
        DbSite(engine, args.site_id).ensure_user(
            args.username, args.username, SiteRole.SITE_ADMINISTRATOR, auth_source="local"
        )
        LocalAccounts(engine).set_password(args.username, password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
