# tabfree-api

The TabFree API service (FastAPI). Phase 0 ships only operational endpoints. Authentication, permissions, content, revisions and the governed query API follow in Phases 1–3 (see `docs/BUILD_PLAN.md`).

```bash
uv run uvicorn tabfree_api.main:app --reload   # http://127.0.0.1:8000/healthz, docs at /api/docs
uv run pytest apps/api
```

Configuration uses environment variables prefixed with `TABFREE_`, for example `TABFREE_ENVIRONMENT=dev`.
