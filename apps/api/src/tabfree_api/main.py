"""FastAPI application factory."""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tabfree_api import __version__
from tabfree_api.auth import OidcClient, SessionStore
from tabfree_api.cache import MemoryResultCache, NoCache, ResultCache
from tabfree_api.content import Site, SiteContent
from tabfree_api.engines import DuckDBEngine, Engine, engine_from_connection
from tabfree_api.guardrails import GuardrailError, Guardrails, SiteLimits
from tabfree_api.service import QueryLimits, QueryService
from tabfree_api.settings import Settings

#: The sample data covers 2023 and 2024, so relative dates anchor at its end.
SAMPLE_TODAY = dt.date(2024, 12, 31)


@functools.cache
def _sample_engine() -> DuckDBEngine:
    """One read-only sample database per process."""
    from tabfree_api.sample import load_sample_data, load_superstore

    engine = DuckDBEngine.in_memory()
    load_sample_data(engine.connection)
    load_superstore(engine.connection)
    return engine


def _engines(settings: Settings, site: SiteContent) -> tuple[Any, dt.date | None]:
    if settings.engine == "sample":
        engine = _sample_engine()
        return (lambda _model: engine), SAMPLE_TODAY
    # One Athena workgroup per site (ADR-007): the site file's, else the settings' mapping.
    workgroup = site.query.athena_workgroup or settings.athena_workgroups.get(site.id)
    cache: dict[str, Engine] = {}

    def for_model(model: Mapping[str, Any]) -> Engine:
        key = repr(sorted(model["connection"].items()))
        if key not in cache:
            cache[key] = engine_from_connection(model["connection"], athena_workgroup=workgroup)
        return cache[key]

    return for_model, None


def query_service(settings: Settings, site: SiteContent, engine_for: Any) -> QueryService:
    """The query service with the settings' caps, cache and the site's guardrails."""
    limits = QueryLimits(
        row_limit=settings.query_row_limit,
        max_bytes=settings.query_max_bytes,
        timeout_s=settings.query_timeout_s,
        athena_reuse_minutes=settings.athena_result_reuse_minutes,
        athena_reuse_with_parameters=settings.athena_reuse_with_parameters,
        redshift_scan_estimate_bytes=settings.redshift_scan_estimate_bytes,
    )
    site_limits = SiteLimits(
        daily_scan_budget_bytes=(
            site.query.daily_scan_budget_bytes
            if site.query.daily_scan_budget_bytes is not None
            else settings.daily_scan_budget_bytes
        ),
        max_concurrent_queries=site.query.max_concurrent_queries or settings.max_concurrent_queries,
        queue_timeout_s=settings.queue_timeout_s,
    )
    cache: ResultCache = (
        MemoryResultCache(settings.result_cache_entries, settings.result_cache_ttl_s)
        if settings.result_cache_entries
        else NoCache()
    )
    return QueryService(
        site.models,
        engine_for,
        site=site.id,
        limits=limits,
        cache=cache,
        guardrails=Guardrails(lambda _site: site_limits),
        data_version=site.data_version,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    dev = config.environment in ("dev", "test")
    for flag in ("preview", "dev_login"):
        if getattr(config, flag) and not dev:
            raise RuntimeError(f"{flag} has no authentication; enable it only in dev or test")
    if config.engine == "sample" and not dev:
        raise RuntimeError("the sample engine is for development; set TABFREE_ENGINE=aws")

    app = FastAPI(
        title="TabFree API",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe for load balancers and ECS health checks."""
        return {"status": "ok"}

    @app.get("/api/version", tags=["ops"])
    def version() -> dict[str, str]:
        return {"name": "tabfree-api", "version": __version__, "environment": config.environment}

    @app.exception_handler(GuardrailError)
    def guardrail_refused(_request: Request, exc: GuardrailError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after_s)} if exc.retry_after_s else None
        content = {"detail": exc.message, "code": exc.code}
        return JSONResponse(status_code=429, content=content, headers=headers)

    site: Any
    sessions: Any
    if config.database_url is not None:
        from tabfree_api.db import connect, upgrade
        from tabfree_api.db.store import AuditLog, DbSessionStore, DbSite, LocalAccounts

        if config.database_auto_migrate and not dev:
            raise RuntimeError(
                "database_auto_migrate is for dev and test; run the migrations as a deploy step"
            )
        db = connect(config.database_url.get_secret_value())
        if config.database_auto_migrate:
            upgrade(db)
        site = DbSite(db, config.site_id, snapshot_ttl_s=config.permission_cache_s)
        sessions = DbSessionStore(db, config.site_id)
        accounts: Any = LocalAccounts(db)
        audit: Any = AuditLog(db, config.site_id)
    else:
        site = Site(config.site_file)
        sessions = SessionStore(config.session_db)
        accounts = None
        audit = None
    engine_for, today = _engines(config, site)
    service = query_service(config, site, engine_for)

    from tabfree_api.portal import Context, create_router

    oidc = None
    if config.oidc_issuer and config.oidc_client_id:
        oidc = OidcClient(
            issuer=config.oidc_issuer,
            client_id=config.oidc_client_id,
            client_secret=config.oidc_client_secret.get_secret_value() if config.oidc_client_secret else None,
            redirect_uri=f"{config.base_url.rstrip('/')}/auth/callback",
            scopes=config.oidc_scopes,
            username_claim=config.oidc_username_claim,
            groups_claim=config.oidc_groups_claim,
            attribute_claims=tuple(config.oidc_attribute_claims),
        )
    signer = None
    if config.usercontent_origin:
        from tabfree_api import usercontent

        key = config.frame_signing_key.get_secret_value().encode() if config.frame_signing_key else None
        signer = usercontent.FrameSigner(key)
        portal_origin = usercontent.origin_of(config.base_url)
        if usercontent.origin_of(config.usercontent_origin) == portal_origin:
            raise RuntimeError("TABFREE_USERCONTENT_ORIGIN must differ from the portal's origin")
        app.middleware("http")(usercontent.host_guard(config.usercontent_origin))
    renderer = None
    if config.export_renderer == "playwright":
        from tabfree_api.exports import PlaywrightRenderer

        renderer = PlaywrightRenderer(config.export_base_url or config.base_url, sessions)
    app.state.context = Context(
        config,
        site,
        sessions,
        service,
        oidc,
        today,
        accounts=accounts,
        audit=audit,
        frame_signer=signer,
        renderer=renderer,
    )
    ctx = app.state.context
    from tabfree_api.authoring import mcp as mcp_server
    from tabfree_api.authoring.budget import TokenBudget

    ctx.budget = TokenBudget(
        site.id,
        per_user=config.ai_daily_tokens_per_user,
        per_site=config.ai_daily_tokens_per_site,
        engine=getattr(site, "engine", None),
        overrides=site.settings if hasattr(site, "settings") else None,
    )
    if config.database_url is not None:
        from tabfree_api.authoring import oauth

        ctx.oauth = oauth.OAuthStore(
            site.engine,
            site.id,
            sessions,
            access_ttl_s=config.oauth_access_ttl_s,
            refresh_days=config.oauth_refresh_days,
        )
        app.include_router(oauth.create_router(ctx, ctx.oauth))
    app.include_router(mcp_server.create_router(ctx))
    from tabfree_api.authoring import provider as ai

    ctx.provider = ai.from_settings(config)
    if ctx.provider is not None and config.database_url is not None:
        from tabfree_api.authoring.author import AuthorRunner, sandbox_from_settings

        ctx.author = AuthorRunner(ctx, sandbox=sandbox_from_settings(config))
    from tabfree_api.jobs import mail

    ctx.mailer = mail.from_settings(config)
    if config.database_url is not None:
        from tabfree_api.jobs.queue import JobQueue

        ctx.jobs = JobQueue(site.engine)
        if config.jobs_inline:
            import threading

            from tabfree_api.jobs.worker import run_forever

            stop = threading.Event()
            app.state.jobs_stop = stop
            threading.Thread(
                target=run_forever, args=(ctx,), kwargs={"scheduler": True, "stop": stop}, daemon=True,
                name="tabfree-jobs",
            ).start()  # fmt: skip
    app.include_router(create_router(ctx))
    from tabfree_api import scim

    async def scim_error(_request: Any, exc: Exception) -> Any:
        return scim.error_response(exc)  # type: ignore[arg-type]

    app.add_exception_handler(scim.ScimError, scim_error)
    if signer is not None:
        app.include_router(
            usercontent.create_router(app.state.context, signer, config.runtime_dir, portal_origin)
        )

    if config.preview:
        from tabfree_api import preview

        app.include_router(preview.create_router(site, service, config.runtime_dir, today))

    return app


def app_factory() -> FastAPI:
    """For ``uvicorn --factory tabfree_api.main:app_factory``."""
    return create_app()
