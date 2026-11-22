"""``python -m tabfree_api.sites``: sites in one database (P6.9), and the built-in usage dashboard (P6.7).

    python -m tabfree_api.sites list
    python -m tabfree_api.sites create sales --name "Sales" [--default-role viewer]
    python -m tabfree_api.sites install-usage [--site sales] --by admin

A new site starts empty: add its first administrator with ``--admin`` (an identity-provider username),
then map a host name to it with ``TABFREE_SITE_HOSTS``. ``install-usage`` publishes the
``tabfree_usage`` model and the ``site-usage`` dashboard into an *Administration* project with no
permission rules, so only site administrators can open them, and schedules the daily usage export.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib import resources
from typing import Any

from sqlalchemy import select

from tabfree_api.authz import SiteRole
from tabfree_api.db import connect
from tabfree_api.db import schema as t
from tabfree_api.db.seed import create_site
from tabfree_api.db.store import DbSite
from tabfree_api.settings import Settings

PROJECT = "administration"


def builtin(path: str) -> str:
    return resources.files("tabfree_api").joinpath("builtin", *path.split("/")).read_text(encoding="utf-8")


def install_usage(db: DbSite, *, by: str, database: str | None = None) -> dict[str, Any]:
    """Publish the usage model and dashboard (admins only) and a daily usage_export schedule."""
    import yaml

    from tabfree_api.db.store import now
    from tabfree_api.jobs.scheduler import SchedulesStore

    model = yaml.safe_load(builtin("tabfree_usage.model.yaml"))
    if database:
        model["connection"]["database"] = database
    if PROJECT not in db.projects:
        db.create_project(PROJECT, "Administration", owner=by, asset_permissions="locked",
                          description="Site usage and operations; site administrators only.")  # fmt: skip
    model_rev = db.publish("model", "tabfree_usage", model, name="TabFree usage", project=PROJECT, owner=by,
                           message="built-in usage model", by=by)  # fmt: skip
    manifest = json.loads(builtin("site-usage/tabfree.json"))
    files = {"index.html": builtin("site-usage/index.html"), "README.md": builtin("site-usage/README.md")}
    dash_rev = db.publish("dashboard", "site-usage", manifest, files=files, project=PROJECT, owner=by,
                          message="built-in usage dashboard", by=by)  # fmt: skip
    # No rules anywhere on the project or its content: only site administrators pass.
    for kind in ("project", "dashboard", "model"):
        db.set_rules("project", PROJECT, (), content_type=kind)
    db.set_rules("dashboard", "site-usage", ())
    db.set_rules("model", "tabfree_usage", ())
    schedules = SchedulesStore(db.engine, db.id, clock=now)
    daily = next((s for s in schedules.list() if s["name"] == "Usage export"), None)
    if daily is None:
        schedules.create(
            "Usage export", {"frequency": "daily", "at": "01:00", "timezone": "UTC", "jobs": ["usage_export"]}
        )
    return {"model": model_rev, "dashboard": dash_rev}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tabfree_api.sites", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    p = commands.add_parser("create")
    p.add_argument("site")
    p.add_argument("--name")
    p.add_argument("--default-role", default="viewer", choices=[r.value for r in SiteRole])
    p.add_argument("--admin", help="the site's first administrator (a username)")
    p = commands.add_parser("install-usage")
    p.add_argument("--site")
    p.add_argument("--by", required=True)
    p.add_argument("--database", help="the Athena database of the usage table (default tabfree_usage)")
    args = parser.parse_args(argv)
    settings = Settings()
    if settings.database_url is None:
        print("set TABFREE_DATABASE_URL", file=sys.stderr)
        return 2
    engine = connect(settings.database_url.get_secret_value())
    if args.command == "list":
        with engine.connect() as conn:
            for sid, name in conn.execute(select(t.sites.c.id, t.sites.c.name).order_by(t.sites.c.id)):
                hosts = [h for h, s in settings.site_hosts.items() if s == sid]
                print(
                    f"{sid}\t{name}\t{', '.join(hosts) or ('(default)' if sid == settings.site_id else '')}"
                )
        return 0
    if args.command == "create":
        db = create_site(engine, args.site, args.name or args.site, default_site_role=args.default_role)
        if args.admin:
            db.ensure_user(args.admin, args.admin, SiteRole.SITE_ADMINISTRATOR)
        print(f"created site {args.site!r}")
        return 0
    db = DbSite(engine, args.site or settings.site_id)
    if args.by not in db.users:
        print(f"{args.by!r} is not a user of the site", file=sys.stderr)
        return 2
    print(json.dumps(install_usage(db, by=args.by, database=args.database)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
