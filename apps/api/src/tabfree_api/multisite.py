"""Several sites in one server (BUILD_PLAN P6.9): each request goes to its site by host name.

``TABFREE_SITE_HOSTS='{"sales.tabfree.example": "sales", "ops.tabfree.example": "ops"}'`` maps host
names to site ids; any other host gets ``TABFREE_SITE_ID``. Each site gets its own application
instance (``create_app`` with that site), created on its first request, so permissions, sessions,
caches and guardrails never cross sites: a session cookie is per host, and every database query is
already scoped by site id. Each instance keeps its own connection pool, which suits tens of sites;
beyond that, run one service per group of sites behind the load balancer.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from tabfree_api.settings import Settings

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def host_of(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"host":
            return str(value.decode("latin-1")).split(":", 1)[0].lower()
    server = scope.get("server")
    return str(server[0]).lower() if server else ""


class SiteDispatcher:
    def __init__(self, settings: Settings, factory: Callable[[Settings], Any] | None = None) -> None:
        if factory is None:
            from tabfree_api.main import create_app

            factory = create_app
        self.settings = settings
        self.factory = factory
        self.hosts = {h.lower(): s for h, s in settings.site_hosts.items()}
        self.apps: dict[str, Any] = {}
        self.lock = threading.Lock()

    def site_for(self, host: str) -> str:
        return self.hosts.get(host, self.settings.site_id)

    def app_for(self, site: str, host: str | None = None) -> Any:
        with self.lock:
            if site not in self.apps:
                update: dict[str, Any] = {"site_id": site, "site_hosts": {}}
                if host and host in self.hosts:
                    scheme = urlsplit(self.settings.base_url).scheme or "https"
                    port = urlsplit(self.settings.base_url).port
                    update["base_url"] = f"{scheme}://{host}" + (f":{port}" if port else "")
                self.apps[site] = self.factory(self.settings.model_copy(update=update))
            return self.apps[site]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Lifespan events go to the default site's app (the others start lazily).
            await self.app_for(self.settings.site_id)(scope, receive, send)
            return
        host = host_of(scope)
        await self.app_for(self.site_for(host), host)(scope, receive, send)


def create(settings: Settings | None = None) -> Any:
    """The application: a dispatcher when several sites are configured, else one site's app."""
    settings = settings or Settings()
    if not settings.site_hosts:
        from tabfree_api.main import create_app

        return create_app(settings)
    return SiteDispatcher(settings)
