"""``python -m tabfree_api.jobs``: the worker and scheduler service, and job housekeeping.

python -m tabfree_api.jobs worker [--scheduler]   # run jobs until stopped (SIGTERM-safe)
python -m tabfree_api.jobs scheduler              # only fire schedules
python -m tabfree_api.jobs drain                  # run every due job once, then exit
python -m tabfree_api.jobs prune                  # delete finished jobs past the retention
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import signal
import sys
import threading
from collections.abc import Sequence

from tabfree_api.settings import Settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tabfree_api.jobs", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--scheduler", action="store_true", help="also fire schedules")
    worker.add_argument("--all-sites", action="store_true", help="serve every site in the database (P6.9)")
    commands.add_parser("scheduler")
    commands.add_parser("drain")
    commands.add_parser("prune")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = Settings()
    if settings.database_url is None:
        print("set TABFREE_DATABASE_URL: jobs live in the metadata database", file=sys.stderr)
        return 2
    from tabfree_api.jobs.queue import JobQueue
    from tabfree_api.jobs.scheduler import Scheduler
    from tabfree_api.jobs.worker import Worker, run_forever
    from tabfree_api.main import create_app

    ctx = create_app(settings.model_copy(update={"jobs_inline": False})).state.context
    queue = JobQueue(ctx.site.engine)
    if args.command == "prune":
        from tabfree_api.lifecycle import purge_expired

        removed = queue.prune(queue.clock() - dt.timedelta(days=settings.job_retention_days))
        purged = purge_expired(ctx.site)
        print(f"pruned {removed} finished jobs; purged {purged} items from the recycle bin")
        return 0
    if args.command == "drain":
        Scheduler(ctx.site.engine, queue, clock=queue.clock).tick()
        print(f"ran {Worker(ctx, queue).drain()} jobs")
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if args.command == "scheduler":
        ticker = Scheduler(ctx.site.engine, queue, clock=queue.clock)
        while not stop.is_set():
            ticker.tick()
            stop.wait(15)
        return 0
    if args.all_sites:
        # One worker thread per site; the scheduler (sites share the schedules table) runs once.
        from tabfree_api.db import schema as t

        with ctx.site.engine.connect() as conn:
            site_ids = [r[0] for r in conn.execute(t.sites.select().with_only_columns(t.sites.c.id))]
        threads = []
        for index, site_id in enumerate(site_ids):
            site_ctx = (
                ctx
                if site_id == ctx.site.id
                else create_app(
                    settings.model_copy(update={"jobs_inline": False, "site_id": site_id})
                ).state.context
            )
            thread = threading.Thread(
                target=run_forever,
                args=(site_ctx,),
                kwargs={"scheduler": args.scheduler and index == 0, "stop": stop},
                name=f"jobs-{site_id}", daemon=True,
            )  # fmt: skip
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        return 0
    run_forever(ctx, scheduler=args.scheduler, stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
