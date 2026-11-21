"""FastAPI application factory."""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI

from tabfree_api import __version__
from tabfree_api.auth import OidcClient, SessionStore
from tabfree_api.content import Site
from tabfree_api.engines import DuckDBEngine, Engine, engine_from_connection
from tabfree_api.service import QueryService
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


def _engines(settings: Settings) -> tuple[Any, dt.date | None]:
    if settings.engine == "sample":
        engine = _sample_engine()
        return (lambda _model: engine), SAMPLE_TODAY
    cache: dict[str, Engine] = {}

    def for_model(model: Mapping[str, Any]) -> Engine:
        key = repr(sorted(model["connection"].items()))
        if key not in cache:
            cache[key] = engine_from_connection(model["connection"])
        return cache[key]

    return for_model, None


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

    site = Site(config.site_file)
    engine_for, today = _engines(config)
    service = QueryService(site.models, engine_for)

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
        )
    app.state.context = Context(config, site, SessionStore(config.session_db), service, oidc, today)
    app.include_router(create_router(app.state.context))

    if config.preview:
        from tabfree_api import preview

        app.include_router(preview.create_router(site, service, config.runtime_dir, today))

    return app


def app_factory() -> FastAPI:
    """For ``uvicorn --factory tabfree_api.main:app_factory``."""
    return create_app()
