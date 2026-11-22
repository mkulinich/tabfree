"""Workers (P6.1): claim a job, run its handler, record the outcome, retry or notify on failure.

Handlers are plain functions ``(ctx, job) -> result`` registered by job kind. A handler raises
:class:`Retryable` for transient trouble (an engine timeout, a mail server refusing for now) and any
other exception for a failure that retrying will not fix; both are recorded on the job. After the
last attempt the job's owner is emailed and the ``job.failed`` webhook event fires.
"""

from __future__ import annotations

import logging
import socket
import threading
import traceback
from collections.abc import Callable, Mapping
from typing import Any

from tabfree_api.jobs.queue import Job, JobQueue
from tabfree_api.jobs.scheduler import Scheduler

log = logging.getLogger("tabfree.jobs")

Handler = Callable[[Any, Job], Mapping[str, Any] | None]


class Retryable(RuntimeError):  # noqa: N818 - raised as `raise Retryable(...)`
    """A failure worth trying again later."""


class Permanent(RuntimeError):  # noqa: N818
    """A failure retrying will not fix (bad settings, missing content)."""


def default_handlers() -> dict[str, Handler]:
    from tabfree_api.jobs import alerts, refresh, subscribe, warm, webhooks

    return {
        "refresh": refresh.handle,
        "subscription": subscribe.handle,
        "alert": alerts.handle,
        "warm": warm.handle,
        "webhook": webhooks.deliver,
    }


class Worker:
    def __init__(
        self,
        ctx: Any,
        queue: JobQueue,
        *,
        handlers: Mapping[str, Handler] | None = None,
        worker_id: str | None = None,
        lease_s: int = 600,
    ) -> None:
        self.ctx = ctx
        self.queue = queue
        self.handlers = dict(handlers if handlers is not None else default_handlers())
        self.worker_id = worker_id or f"{socket.gethostname()}:{threading.get_ident()}"
        self.lease_s = lease_s

    def run_once(self) -> bool:
        """Run one due job of this site; False when there was none."""
        job = self.queue.claim(
            self.worker_id, site_id=self.ctx.site.id, kinds=list(self.handlers), lease_s=self.lease_s
        )
        if job is None:
            return False
        handler = self.handlers[job.kind]
        try:
            result = handler(self.ctx, job)
        except Permanent as exc:
            self._failed(job, str(exc), retry=False)
        except Retryable as exc:
            self._failed(job, str(exc), retry=True)
        except Exception as exc:  # a bug or an unexpected error: recorded, retried
            log.warning("job %s (%s) failed: %s", job.id, job.kind, traceback.format_exc(limit=3))
            self._failed(job, f"{type(exc).__name__}: {exc}", retry=True)
        else:
            self.queue.complete(job.id, result)
        return True

    def _failed(self, job: Job, error: str, *, retry: bool) -> None:
        status = self.queue.fail(job.id, error, retry=retry)
        if status != "failed":
            return
        self.ctx.audit(
            "job_failed", None, "error", resource_kind="job", resource_id=str(job.id), kind=job.kind
        )
        try:
            from tabfree_api.jobs import webhooks
            from tabfree_api.jobs.notify import job_failed

            job_failed(self.ctx, job, error)
            webhooks.emit(self.ctx, "job.failed", {"job": job.id, "kind": job.kind, "error": error[:500]})
        except Exception:
            log.warning("could not notify about failed job %s", job.id, exc_info=True)

    def drain(self, limit: int = 1000) -> int:
        """Run due jobs until none is left (tests, the inline mode); returns how many ran."""
        ran = 0
        while ran < limit and self.run_once():
            ran += 1
        return ran


def run_forever(ctx: Any, *, scheduler: bool, stop: threading.Event, idle_s: float = 5.0) -> None:
    """The worker (and optionally scheduler) loop; returns when ``stop`` is set."""
    queue = JobQueue(ctx.site.engine)
    worker = Worker(ctx, queue)
    ticker = Scheduler(ctx.site.engine, queue, clock=queue.clock) if scheduler else None
    while not stop.is_set():
        if ticker is not None:
            try:
                ticker.tick()
            except Exception:
                log.exception("scheduler tick failed")
        if not worker.run_once():
            stop.wait(idle_s)
