"""The tabfree CLI against a stub server (no network)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tabfree_cli import client, main
from tabfree_cli.client import CliError, Credentials, Mcp


class Server:
    """Answers the OAuth and MCP requests the CLI makes."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, Any]] = []
        self.files = {"tabfree.json": '{"id": "d"}\n', "index.html": "<tf-dashboard></tf-dashboard>\n"}
        self.expired = False

    def __call__(self, request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        url = request.full_url
        body = request.data.decode() if isinstance(request.data, bytes) else None
        self.requests.append((url, body))
        if url.endswith("/.well-known/oauth-authorization-server"):
            root = "https://tf.example"
            return (
                200,
                {},
                json.dumps(
                    {
                        "authorization_endpoint": f"{root}/oauth/authorize",
                        "token_endpoint": f"{root}/oauth/token",
                        "registration_endpoint": f"{root}/oauth/register",
                    }
                ).encode(),
            )
        if url.endswith("/oauth/register"):
            return 201, {}, json.dumps({"client_id": "tfc_1"}).encode()
        if url.endswith("/oauth/token"):
            form = dict(urllib.parse.parse_qsl(body or ""))
            assert form["client_id"] == "tfc_1"
            return (
                200,
                {},
                json.dumps(
                    {"access_token": "tfo_new", "refresh_token": "tfr_2", "expires_in": 3600}
                ).encode(),
            )
        if url.endswith("/mcp"):
            if self.expired and request.headers.get("Authorization") == "Bearer tfo_old":
                return 401, {}, b"{}"
            message = json.loads(body or "{}")
            name, args = message["params"]["name"], message["params"]["arguments"]
            result = self.tool(name, args)
            return 200, {}, json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        return 404, {}, b"{}"

    def tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        def ok(text: str, structured: Any = None) -> dict[str, Any]:
            out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
            if structured is not None:
                out["structuredContent"] = structured
            return out

        if name == "get_dashboard":
            return ok("", {"dashboard": args["dashboard"], "files": self.files, "openDrafts": []})
        if name == "edit_dashboard_files":
            for edit in args["edits"]:
                self.files[edit["path"]] = edit["content"]
            return ok("", {"draft": {"id": "draft-1"}, "validation": {"ok": True, "checks": []}})
        if name == "request_publish":
            return ok("Draft passes.\nConfirmation code: AB-CD", {"confirmation": "AB12-CD34-EF56"})
        if name == "diff_revisions":
            return ok("+ a change")
        if name == "confirm_publish":
            assert args["confirmation"] == "AB12-CD34-EF56"
            return ok("Published", {"revision": 7})
        return {"content": [{"type": "text", "text": f"unknown {name}"}], "isError": True}


def test_login_registers_a_public_client_with_pkce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    server = Server()
    opened: list[str] = []
    creds = client.login(
        "https://tf.example/",
        http=server,
        open_browser=opened.append,
        wait_for_code=lambda _port, _state: "code-1",
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(opened[0]).query))
    assert query["code_challenge_method"] == "S256"
    assert query["resource"] == "https://tf.example/mcp"
    assert query["redirect_uri"].startswith("http://127.0.0.1:")
    token_form = dict(urllib.parse.parse_qsl(server.requests[-1][1]))
    assert token_form["code_verifier"]
    assert token_form["code"] == "code-1"
    path = creds.save()
    assert path.stat().st_mode & 0o777 == 0o600
    assert Credentials.load() == creds


def test_pull_push_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = Server()
    mcp = Mcp("https://tf.example", "tfp_x", http=server)
    monkeypatch.setattr(main, "connect", lambda url=None: mcp)
    folder = tmp_path / "d"
    main.pull(mcp, "sales-overview", folder)
    assert (folder / "index.html").read_text() == "<tf-dashboard></tf-dashboard>\n"
    (folder / "index.html").write_text("<tf-dashboard><tf-text>hi</tf-text></tf-dashboard>\n")
    assert main.main(["push", str(folder)]) == 0
    assert server.files["index.html"].startswith("<tf-dashboard><tf-text>")
    assert json.loads((folder / ".tabfree.json").read_text())["draft"] == "draft-1"
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main.main(["publish", str(folder)]) == 1
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert main.main(["publish", str(folder), "-m", "x"]) == 0
    assert json.loads((folder / ".tabfree.json").read_text())["draft"] is None


def test_expired_access_tokens_are_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    server = Server()
    server.expired = True
    creds = Credentials("https://tf.example", "tfc_1", "tfo_old", "tfr_1", 0)
    mcp = Mcp("https://tf.example", "tfo_old", creds, http=server)
    assert mcp.call("diff_revisions", draft="d")["content"][0]["text"] == "+ a change"
    assert Credentials.load() is not None
    assert Credentials.load().access_token == "tfo_new"
    with pytest.raises(CliError, match="unknown"):
        mcp.call("nope")


def test_kit_writes_claude_code_files(tmp_path: Path) -> None:
    written = main.write_kit(tmp_path, "https://tf.example/")
    assert {p.name for p in written} == {"CLAUDE.md", ".mcp.json", "SKILL.md"}
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["tabfree"] == {
        "type": "http",
        "url": "https://tf.example/mcp",
    }
    skill = (tmp_path / ".claude" / "skills" / "tabfree-dashboard" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: tabfree-dashboard\n")
