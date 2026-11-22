"""Talking to a TabFree server: OAuth sign-in (PKCE, loopback redirect) and MCP tool calls.

Credentials are the person's own: an OAuth refresh and access token in
``~/.config/tabfree/credentials.json`` (mode 0600), or a personal access token in ``TABFREE_TOKEN``
(inject it from a secrets manager; it wins over the file).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CLIENT_NAME = "tabfree CLI"
SCOPE = "tabfree.read tabfree.author"


class CliError(RuntimeError):
    pass


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tabfree"


@dataclass
class Credentials:
    url: str
    client_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0

    @classmethod
    def load(cls, path: Path | None = None) -> Credentials | None:
        path = path or config_dir() / "credentials.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            **{
                k: data[k]
                for k in ("url", "client_id", "access_token", "refresh_token", "expires_at")
                if k in data
            }
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or config_dir() / "credentials.json"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Created with 0600 before anything is written: the tokens are the person's.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(self.__dict__, out, indent=2)
        return path


Http = Callable[[urllib.request.Request], tuple[int, Mapping[str, str], bytes]]


def _request(url: str, **kwargs: Any) -> urllib.request.Request:
    """A request to an http(s) URL only (never file: or other schemes)."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise CliError(f"not an http(s) URL: {url!r}")
    return urllib.request.Request(url, **kwargs)  # noqa: S310 - the scheme is checked above


def _http(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - the server the user named
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _form(url: str, values: Mapping[str, str], http: Http) -> dict[str, Any]:
    body = urllib.parse.urlencode(values).encode()
    request = _request(
        url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    status, _headers, raw = http(request)
    data: dict[str, Any] = json.loads(raw or b"{}")
    if status != 200:
        raise CliError(f"{data.get('error', status)}: {data.get('error_description', '')}".strip(": "))
    return data


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login(
    url: str,
    *,
    http: Http = _http,
    open_browser: Callable[[str], Any] = webbrowser.open,
    wait_for_code: Callable[[int, str], str] | None = None,
) -> Credentials:
    """OAuth 2.1 sign-in: register a public client, approve in the browser, exchange the code."""
    url = url.rstrip("/")
    status, _h, raw = http(_request(f"{url}/.well-known/oauth-authorization-server"))
    if status != 200:
        raise CliError(f"{url} is not a TabFree server with OAuth (HTTP {status})")
    meta = json.loads(raw)
    server = _Callback()
    redirect = f"http://127.0.0.1:{server.port}/callback"
    register = _request(
        meta["registration_endpoint"],
        data=json.dumps(
            {"client_name": CLIENT_NAME, "redirect_uris": [redirect], "token_endpoint_auth_method": "none"}
        ).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    status, _h, raw = http(register)
    if status != 201:
        raise CliError(f"client registration failed (HTTP {status})")
    client_id = json.loads(raw)["client_id"]
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": SCOPE,
            "resource": f"{url}/mcp",
        }
    )
    open_browser(f"{meta['authorization_endpoint']}?{query}")
    code = (wait_for_code or server.wait)(server.port, state)
    tokens = _form(
        meta["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_verifier": verifier,
            "resource": f"{url}/mcp",
        },
        http,
    )
    return Credentials(
        url,
        client_id,
        tokens["access_token"],
        tokens.get("refresh_token", ""),
        time.time() + float(tokens.get("expires_in", 3600)),
    )


class _Callback:
    """A one-shot loopback server for the OAuth redirect (RFC 8252)."""

    def __init__(self) -> None:
        self.result: dict[str, str] = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                outer.result = params
                ok = "code" in params
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"Signed in to TabFree. You can close this tab." if ok else b"Sign-in failed."
                )

            def log_message(self, *_args: Any) -> None:
                return

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.httpd.server_address[1])

    def wait(self, _port: int, state: str, timeout: float = 300.0) -> str:
        thread = threading.Thread(target=self.httpd.handle_request, daemon=True)
        self.httpd.timeout = timeout
        thread.start()
        thread.join(timeout)
        self.httpd.server_close()
        if self.result.get("state") != state or "code" not in self.result:
            raise CliError(f"sign-in did not complete: {self.result.get('error', 'no answer')}")
        return self.result["code"]


@dataclass
class Mcp:
    """A minimal MCP client over Streamable HTTP (JSON responses)."""

    url: str
    token: str
    credentials: Credentials | None = None
    http: Http = _http
    _next: int = field(default=1, init=False)

    def _refresh(self) -> bool:
        creds = self.credentials
        if creds is None or not creds.refresh_token:
            return False
        tokens = _form(
            f"{creds.url}/oauth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": creds.refresh_token,
                "client_id": creds.client_id,
            },
            self.http,
        )
        creds.access_token, creds.refresh_token = tokens["access_token"], tokens.get("refresh_token", "")
        creds.expires_at = time.time() + float(tokens.get("expires_in", 3600))
        creds.save()
        self.token = creds.access_token
        return True

    def rpc(self, method: str, params: Mapping[str, Any] | None = None, *, retry: bool = True) -> Any:
        body = {"jsonrpc": "2.0", "id": self._next, "method": method, "params": dict(params or {})}
        self._next += 1
        request = _request(
            f"{self.url.rstrip('/')}/mcp",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": "2025-11-25",
            },
        )
        status, _headers, raw = self.http(request)
        if status == 401 and retry and self._refresh():
            return self.rpc(method, params, retry=False)
        if status == 401:
            raise CliError("not signed in, or the token expired: run `tabfree login`")
        if status != 200:
            raise CliError(f"the server answered HTTP {status}")
        answer = json.loads(raw)
        if "error" in answer:
            raise CliError(str(answer["error"].get("message")))
        return answer["result"]

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result: dict[str, Any] = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise CliError(result["content"][0]["text"])
        return result


def connect(url: str | None = None) -> Mcp:
    token = os.environ.get("TABFREE_TOKEN")
    creds = Credentials.load()
    base = url or os.environ.get("TABFREE_URL") or (creds.url if creds else None)
    if not base:
        raise CliError("no server: run `tabfree login URL` or set TABFREE_URL")
    if token:
        return Mcp(base, token)
    if creds is None or creds.url.rstrip("/") != base.rstrip("/"):
        raise CliError(f"not signed in to {base}: run `tabfree login {base}`")
    return Mcp(base, creds.access_token, creds)
