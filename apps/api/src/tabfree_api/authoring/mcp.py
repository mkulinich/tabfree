"""The TabFree MCP server (BUILD_PLAN P4.1): Streamable HTTP at ``/mcp``, stateless, JSON responses.

Each POST carries one JSON-RPC message. Requests get a JSON response; notifications get 202. The
server keeps no session (``Mcp-Session-Id`` is not used) and offers no server-to-client stream, so
``GET /mcp`` answers 405, as the transport allows.

**Authorization** follows the MCP authorization spec: the endpoint is an OAuth 2.1 protected resource.
Without a valid bearer token it answers 401 with ``WWW-Authenticate: Bearer resource_metadata=...``,
which leads clients to TabFree's authorization server (:mod:`tabfree_api.authoring.oauth`). Personal
access tokens are accepted as bearer tokens too, for clients configured with a header.

Requests with an ``Origin`` header from another site are refused (DNS rebinding defence).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from tabfree_api import __version__
from tabfree_api.auth import SessionInfo
from tabfree_api.authoring import tools as authoring_tools
from tabfree_api.authoring.fence import NOTE

#: Newest first; the server answers with the client's version when it supports it.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_BODY = 2 * 1024 * 1024

INSTRUCTIONS = (
    "TabFree is a governed analytics server. Use these tools to read dashboards and semantic models, run "
    "governed queries, and change dashboards through drafts. Call learn('overview') before your first "
    "edit. Every tool runs as the signed-in person, with their permissions and row-level security. "
    "Publishing takes two steps and the person must confirm. " + NOTE
)


def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def preview_app(runtime_dir: Any) -> str:
    """The MCP Apps page for ``preview``: the dashboard runtime with the tool result's snapshot."""
    page = resources.files("tabfree_api.authoring").joinpath("preview_app.html").read_text(encoding="utf-8")
    host = runtime_dir / "host.js"
    frame = runtime_dir / "frame.js"
    if not (host.is_file() and frame.is_file()):
        return page.replace("/*HOST*/", "").replace('"__FRAME__"', json.dumps(""))
    frame_js = frame.read_text(encoding="utf-8")
    host_js = _classic(host.read_text(encoding="utf-8")).replace("</script", "<\\/script")
    return page.replace("/*HOST*/", host_js).replace(
        '"__FRAME__"', json.dumps(frame_js).replace("</", "<\\/")
    )


_EXPORT = re.compile(r"export\s*\{([^}]*)\};?")


def _classic(module: str) -> str:
    """host.js is an ES module; as a classic script its exports become ``window.__tabfreeHost``."""
    module = re.sub(r"//# sourceMappingURL=\S+", "", module)

    def to_object(match: re.Match[str]) -> str:
        pairs = []
        for part in match.group(1).split(","):
            local, _, exported = part.strip().partition(" as ")
            pairs.append(f"{(exported or local).strip()}: {local.strip()}")
        return "window.__tabfreeHost = {" + ", ".join(pairs) + "};"

    return "(function () {\n" + _EXPORT.sub(to_object, module) + "\n})();"


def create_router(ctx: Any) -> APIRouter:
    from tabfree_api.authoring.budget import RateLimiter
    from tabfree_api.authoring.oauth import resolve_bearer, resource_metadata_url
    from tabfree_api.authoring.service import AuthoringService
    from tabfree_api.usercontent import origin_of

    router = APIRouter()
    settings = ctx.settings
    service = AuthoringService(ctx)
    limiter = RateLimiter(settings.mcp_calls_per_minute)
    own_origin = origin_of(settings.base_url)
    allowed_origins = {own_origin, *(origin_of(o) for o in settings.mcp_allowed_origins)}

    def unauthorized(description: str) -> Response:
        header = (
            f'Bearer resource_metadata="{resource_metadata_url(settings)}", '
            f'scope="{" ".join(authoring_tools.SCOPES)}"'
        )
        if description:
            header += f', error="invalid_token", error_description="{description}"'
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": header})

    def handle(info: SessionInfo, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        id_ = message.get("id")
        params = message.get("params") or {}
        if not isinstance(method, str):
            return _error(id_, -32600, "invalid request")
        if "id" not in message:
            return None  # a notification (initialized, cancelled): nothing to answer
        if not isinstance(params, dict):
            return _error(id_, -32602, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _result(
                id_,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                    "serverInfo": {"name": "tabfree", "title": "TabFree", "version": __version__},
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _result(id_, {})
        if method == "tools/list":
            tools = [t.definition() for t in authoring_tools.TOOLS if authoring_tools.allowed(t, info)]
            return _result(id_, {"tools": tools})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in authoring_tools.BY_NAME:
                return _error(id_, -32602, f"unknown tool {name!r}")
            try:
                result = authoring_tools.call(
                    service, info, name, params.get("arguments") or {}, limiter=limiter
                )
            except authoring_tools.ToolInputError as exc:
                return _error(id_, -32602, str(exc))
            return _result(id_, result.mcp())
        if method == "resources/list":
            return _result(
                id_,
                {
                    "resources": [
                        {
                            "uri": authoring_tools.PREVIEW_UI,
                            "name": "dashboard-preview",
                            "title": "Dashboard preview",
                            "mimeType": "text/html;profile=mcp-app",
                        }
                    ]
                },
            )
        if method == "resources/read":
            if params.get("uri") != authoring_tools.PREVIEW_UI:
                return _error(id_, -32002, "resource not found")
            return _result(
                id_,
                {
                    "contents": [
                        {
                            "uri": authoring_tools.PREVIEW_UI,
                            "mimeType": "text/html;profile=mcp-app",
                            "text": preview_app(settings.runtime_dir),
                        }
                    ]
                },
            )
        if method in ("resources/templates/list",):
            return _result(id_, {"resourceTemplates": []})
        if method == "prompts/list":
            return _result(id_, {"prompts": []})
        return _error(id_, -32601, f"method not found: {method}")

    @router.post("/mcp", include_in_schema=False)
    async def mcp(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin_of(origin) not in allowed_origins:
            return JSONResponse({"error": "forbidden origin"}, status_code=403)
        version = request.headers.get("mcp-protocol-version")
        if version and version not in PROTOCOL_VERSIONS:
            return JSONResponse({"error": f"unsupported MCP-Protocol-Version {version}"}, status_code=400)
        authorization = request.headers.get("authorization") or ""
        if not authorization.lower().startswith("bearer "):
            return unauthorized("")
        info = resolve_bearer(ctx, authorization[7:].strip())
        if info is None:
            return unauthorized("the token is invalid or expired")
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse(_error(None, -32600, "request too large"), status_code=413)
        try:
            message = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return JSONResponse(
                _error(None, -32600, "send one JSON-RPC 2.0 message per request"), status_code=400
            )
        if "method" not in message:
            return Response(status_code=202)  # a response to a server request: we send none
        answer = await _run(handle, info, message)
        if answer is None:
            return Response(status_code=202)
        return JSONResponse(answer)

    @router.get("/mcp", include_in_schema=False)
    @router.delete("/mcp", include_in_schema=False)
    def no_stream() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    return router


async def _run(handle: Any, info: SessionInfo, message: dict[str, Any]) -> dict[str, Any] | None:
    # Tools call the database and the query engines synchronously: run them off the event loop.
    from starlette.concurrency import run_in_threadpool

    result: dict[str, Any] | None = await run_in_threadpool(handle, info, message)
    return result
