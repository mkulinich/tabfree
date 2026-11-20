"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from tabfree_api import __version__
from tabfree_api.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
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

    if config.preview:
        if config.environment not in ("dev", "test"):
            raise RuntimeError("the preview has no authentication; enable it only in dev or test")
        from tabfree_api.preview import PreviewEngine, PreviewPaths, create_router

        app.include_router(create_router(PreviewEngine(PreviewPaths.default())))

    return app


app = create_app()
