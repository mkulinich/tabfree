"""Dashboard bundles on a separate usercontent origin (BUILD_PLAN P3.6, ADR-002).

The portal page never runs dashboard code. A dashboard's iframe loads a signed, short-lived URL on
another origin; that response carries its own CSP, including ``sandbox allow-scripts``, so the bundle
runs with an opaque origin even if someone opens the URL directly, and it can make no network
requests. Data still flows only through the postMessage bridge to the portal's governed query API.

The usercontent host serves nothing else: no API, no portal, no cookies (cookies are host-only, and
the session cookie is set on the portal's host).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

FRAME_TTL_S = 300
#: Mirrors FRAME_CSP in packages/runtime/src/host.ts, plus the sandbox directive and the framing rule.
FRAME_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'"
)
_HEAD = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)


def origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def build_frame(html: str, runtime: str) -> str:
    """The bundle's HTML with the CSP meta tag and the runtime first in <head> (as buildSrcdoc does)."""
    csp = f'<meta http-equiv="Content-Security-Policy" content="{FRAME_CSP.split("; ", 1)[1]}">'
    script = "<script>" + re.sub(r"</(script)", r"<\\/\1", runtime, flags=re.IGNORECASE) + "</script>"
    head = _HEAD.search(html)
    if head:
        return html[: head.end()] + csp + script + html[head.end() :]
    return f"<!doctype html><html><head>{csp}{script}</head><body>{html}</body></html>"


class FrameSigner:
    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    def sign(self, site: str, dashboard: str, expires: int) -> str:
        message = f"{site}\n{dashboard}\n{expires}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def url(self, origin: str, site: str, dashboard: str, *, now: float | None = None) -> str:
        expires = int((now or time.time()) + FRAME_TTL_S)
        query = urllib.parse.urlencode({"e": expires, "s": self.sign(site, dashboard, expires)})
        return f"{origin.rstrip('/')}/frame/{urllib.parse.quote(dashboard, safe='')}?{query}"

    def verify(
        self, site: str, dashboard: str, expires: int, signature: str, *, now: float | None = None
    ) -> bool:
        if expires < (now or time.time()) or expires > (now or time.time()) + FRAME_TTL_S + 60:
            return False
        return hmac.compare_digest(self.sign(site, dashboard, expires), signature)


def create_router(ctx: Any, signer: FrameSigner, runtime_dir: Path, portal_origin: str) -> APIRouter:
    router = APIRouter()

    @router.get("/frame/{dashboard}", include_in_schema=False)
    def frame(dashboard: str, e: int = 0, s: str = "") -> Response:
        if not signer.verify(ctx.site.id, dashboard, e, s[:128]):
            raise HTTPException(404)
        runtime = runtime_dir / "frame.js"
        if not runtime.is_file():
            raise HTTPException(503, "build the runtime first: npm run build -w @tabfree/runtime")
        bundle = ctx.site.bundle(dashboard)
        headers = {
            "Content-Security-Policy": f"{FRAME_CSP}; frame-ancestors {portal_origin}",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, max-age=60",
            "Cross-Origin-Opener-Policy": "same-origin",
        }
        return HTMLResponse(build_frame(bundle.html, runtime.read_text(encoding="utf-8")), headers=headers)

    return router


def host_guard(
    usercontent_origin: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Middleware: on the usercontent host, only /frame/ exists; elsewhere /frame/ does not."""
    expected = urllib.parse.urlsplit(usercontent_origin).netloc.lower()

    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = (request.headers.get("host") or "").lower()
        on_usercontent = host == expected
        if on_usercontent != request.url.path.startswith("/frame/"):
            return Response(status_code=404)
        return await call_next(request)

    return guard
