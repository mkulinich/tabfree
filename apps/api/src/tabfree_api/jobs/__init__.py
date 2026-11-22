"""Scheduled and background work (BUILD_PLAN P6.1-P6.4, P6.9, P6.10; ADR-013).

- :mod:`.schedule` and :mod:`.scheduler`: shared schedules in a time zone, and the loop that fires them;
- :mod:`.queue`: the job queue and job history in the metadata database;
- :mod:`.worker`: claims jobs and runs their handlers, with retries and failure notices;
- handlers: :mod:`.refresh` (P6.2), :mod:`.subscribe` (P6.3), :mod:`.alerts` (P6.4), :mod:`.warm`
  (P6.10) and :mod:`.webhooks` (P6.9);
- :mod:`.mail`: Amazon SES or the log; :mod:`.routes`: the portal API.

Run ``python -m tabfree_api.jobs worker --scheduler`` as its own service, or set
``TABFREE_JOBS_INLINE=1`` to run both inside the API process in development.
"""
