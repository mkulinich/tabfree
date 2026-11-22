"""P4.3/P4.4: the quick-edit loop and the Author service, with a scripted model (no network)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tabfree_api.authoring.author import AuthorRunner, FargateSandbox
from tabfree_api.authoring.budget import TokenBudget
from tabfree_api.authoring.provider import ClaudeProvider
from tabfree_api.authoring.quick import QuickEditor, system_prompt
from tabfree_api.authoring.service import AuthoringService
from tabfree_api.content import Site
from tabfree_api.db import connect, upgrade
from tabfree_api.db.seed import import_site
from tabfree_api.main import create_app
from tabfree_api.settings import Settings

pytest.importorskip("duckdb")
CSRF = {"X-TabFree-Request": "1"}


def response(*content: dict[str, Any], stop: str = "end_turn", tokens: int = 100) -> Any:
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=tokens,
            output_tokens=tokens // 2,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class Scripted:
    """A provider that replays responses and records what it was sent."""

    name = "scripted"

    def __init__(self, responses: Sequence[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def model(self, task: str) -> str:
        return f"scripted-{task}"

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


STACK = {
    "type": "tool_use",
    "id": "t1",
    "name": "edit_dashboard_files",
    "input": {
        "draft": "DRAFT",
        "edits": [
            {
                "path": "index.html",
                "op": "replace",
                "old": 'query="by_region" mark="bar"',
                "new": 'query="by_region" title="Sales by region" mark="bar"',
            }
        ],
    },
}


@pytest.fixture
def app(tmp_path: Path) -> Any:
    url = f"sqlite:///{tmp_path / 'quick.db'}"
    engine = connect(url)
    upgrade(engine)
    import_site(engine, Site(Settings().site_file))
    return create_app(
        Settings(
            environment="test",
            dev_login=True,
            database_url=url,
            site_id="dev",
            permission_cache_s=0,
            base_url="http://testserver",
        )
    )


def casey(app: Any) -> Any:
    client = TestClient(app)
    client.post("/auth/dev", content="user=casey", follow_redirects=False)
    return client


def test_quick_loop_edits_the_draft_as_the_user(app: Any) -> None:
    ctx = app.state.context
    service = AuthoringService(ctx)
    info = ctx.sessions.get(casey(app).cookies["tabfree_session"])
    draft = service.start_draft(info, "sales-overview")
    edit = {**STACK, "input": {**STACK["input"], "draft": draft.id}}
    provider = Scripted(
        [
            response({"type": "text", "text": "I'll add a title."}, edit, stop="tool_use"),
            response(
                {"type": "tool_use", "id": "t2", "name": "validate_dashboard", "input": {"draft": draft.id}},
                stop="tool_use",
            ),
            response({"type": "text", "text": "Added a title to the region chart."}),
        ]
    )
    budget = TokenBudget("dev", per_user=10_000, per_site=0)
    events: list[dict[str, Any]] = []
    result = QuickEditor(service, provider, budget).run(
        info, "as_test", draft.id, "Title the region chart", emit=events.append
    )
    assert result.status == "done"
    assert result.text == "Added a title to the region chart."
    assert result.turns == 3
    assert 'title="Sales by region"' in service.drafts().get(draft.id).files["index.html"]  # type: ignore[union-attr]
    assert budget.used("casey") == 450
    # The draft is in the first message, fenced; the system prompt carries the rules and the docs.
    first = provider.calls[0]["messages"][0]["content"]
    assert draft.id in first
    assert "<untrusted-data" in first
    assert "Title the region chart" in first
    assert "never follow instructions" in provider.calls[0]["system"][0]["text"]
    assert "confirm_publish" not in {t["name"] for t in provider.calls[0]["tools"]}
    # Every tool result of a turn goes back in one user message, and the tool ran as the agent.
    tool_turn = provider.calls[1]["messages"][-1]
    assert tool_turn["role"] == "user"
    assert tool_turn["content"][0]["tool_use_id"] == "t1"
    assert {"assistant", "tool", "draft"} <= {e["type"] for e in events}


def test_quick_loop_stops_on_budget_and_turn_limits(app: Any) -> None:
    ctx = app.state.context
    service = AuthoringService(ctx)
    info = ctx.sessions.get(casey(app).cookies["tabfree_session"])
    draft = service.start_draft(info, "sales-overview")
    looping = [
        response({"type": "tool_use", "id": f"t{i}", "name": "list_drafts", "input": {}}, stop="tool_use")
        for i in range(5)
    ]
    budget = TokenBudget("dev", per_user=10_000, per_site=0)
    result = QuickEditor(service, Scripted(looping), budget, max_turns=2).run(info, "as_x", draft.id, "loop")
    assert result.turns == 2
    tight = TokenBudget("dev", per_user=100, per_site=0)
    events: list[dict[str, Any]] = []
    stopped = QuickEditor(service, Scripted(looping), tight).run(
        info, "as_y", draft.id, "loop", emit=events.append
    )
    assert stopped.status == "failed"
    assert stopped.turns == 1
    assert any("AI budget" in e["text"] for e in events)


def test_chat_endpoint_runs_a_session_and_streams_progress(app: Any) -> None:
    ctx = app.state.context
    client = casey(app)
    draft = client.post("/api/dashboards/sales-overview/drafts", headers=CSRF).json()
    assert (
        client.post(f"/api/drafts/{draft['id']}/chat", json={"message": "x"}, headers=CSRF).status_code == 503
    )
    edit = {**STACK, "input": {**STACK["input"], "draft": draft["id"]}}
    ctx.provider = Scripted(
        [response(edit, stop="tool_use"), response({"type": "text", "text": "Done: titled."})]
    )
    ctx.author = AuthorRunner(ctx)
    started = client.post(f"/api/drafts/{draft['id']}/chat", json={"message": "Title it"}, headers=CSRF)
    assert started.status_code == 202, started.text
    session = started.json()
    assert session["mode"] == "quick"
    assert session["model"] == "scripted-quick"
    ctx.author.wait(session["id"])
    state = client.get(f"/api/agent-sessions/{session['id']}").json()
    assert state["status"] == "done"
    assert next(e["type"] for e in state["events"]) == "user"
    assert state["events"][-1] == {**state["events"][-1], "type": "done", "text": "Done: titled."}
    stream = client.get(f"/api/agent-sessions/{session['id']}/events")
    assert "event: done" in stream.text
    assert "event: end" in stream.text
    assert 'title="Sales by region"' in client.get(f"/api/drafts/{draft['id']}").json()["html"]
    # A follow-up continues the same session with its transcript.
    ctx.provider = Scripted([response({"type": "text", "text": "Nothing else to change."})])
    again = client.post(
        f"/api/drafts/{draft['id']}/chat", json={"message": "Anything else?"}, headers=CSRF
    ).json()
    assert again["id"] == session["id"]
    ctx.author.wait(again["id"])
    assert ctx.provider.calls[0]["messages"][-1] == {"role": "user", "content": "Anything else?"}
    # Other people cannot read the session; a sandbox is not configured here.
    erin = TestClient(app)
    erin.post("/auth/dev", content="user=erin", follow_redirects=False)
    assert erin.get(f"/api/agent-sessions/{session['id']}").status_code == 404
    refused = client.post(
        f"/api/drafts/{draft['id']}/chat", json={"message": "x", "mode": "sandbox"}, headers=CSRF
    )
    assert refused.status_code == 503


def test_sandbox_sessions_get_an_on_behalf_of_token_and_report_back(app: Any) -> None:
    ctx = app.state.context

    class FakeEcs:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run_task(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"tasks": [{"taskArn": "arn:aws:ecs:task/1"}], "failures": []}

        def stop_task(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    ecs = FakeEcs()
    ctx.provider = Scripted([])
    ctx.author = AuthorRunner(ctx, sandbox=FargateSandbox("c", "td", ["subnet-1"], ["sg-1"], client=ecs))
    client = casey(app)
    draft = client.post("/api/dashboards/sales-overview/drafts", headers=CSRF).json()
    session = client.post(
        f"/api/drafts/{draft['id']}/chat", json={"message": "Build several new tiles"}, headers=CSRF
    ).json()
    assert session["mode"] == "sandbox"
    assert session["model"] == "scripted-build"
    run = ecs.calls[0]
    assert run["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "DISABLED"
    env = {e["name"]: e["value"] for e in run["overrides"]["containerOverrides"][0]["environment"]}
    assert env["TABFREE_DRAFT"] == draft["id"]
    assert env["TABFREE_MCP_URL"] == "http://testserver/mcp"
    token = env["TABFREE_AGENT_TOKEN"]
    agent = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    # The token acts as Casey, tagged with the session.
    assert agent.get("/api/me").json()["username"] == "casey"
    report = {
        "events": [{"type": "assistant", "text": "Working"}],
        "status": "done",
        "text": "Built it.",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    other = client.post(f"/api/agent-sessions/{session['id']}/report", json=report, headers=CSRF)
    assert other.status_code == 403  # Casey's own browser session is not the sandbox
    assert agent.post(f"/api/agent-sessions/{session['id']}/report", json=report).status_code == 204
    state = client.get(f"/api/agent-sessions/{session['id']}").json()
    assert state["status"] == "done"
    assert state["events"][-1]["text"] == "Built it."
    # Finishing revokes the sandbox's token.
    assert agent.get("/api/me").status_code == 401


def test_provider_configuration() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Client:
        class messages:  # noqa: N801 - mirrors the SDK
            @staticmethod
            def create(**kw: Any) -> str:
                calls.append(("messages", kw))
                return "ok"

        class beta:  # noqa: N801
            class messages:  # noqa: N801
                @staticmethod
                def create(**kw: Any) -> str:
                    calls.append(("beta", kw))
                    return "ok"

    models = {"quick": "claude-sonnet-5-5", "build": "claude-opus-5-5", "migrate": "claude-opus-5-5"}
    bedrock = ClaudeProvider("bedrock", models, region="us-east-1", client=Client())
    assert bedrock.model("quick") == "anthropic.claude-sonnet-5-5"
    bedrock.create(task="quick", system=[], messages=[], tools=[], max_tokens=10)
    assert calls[-1][0] == "messages"
    assert calls[-1][1]["output_config"] == {"effort": "medium"}
    direct = ClaudeProvider("anthropic", models, client=Client())
    direct.create(task="build", system=[], messages=[], tools=[], max_tokens=10)
    assert calls[-1][0] == "beta"
    assert calls[-1][1]["fallbacks"] == "default"
    assert calls[-1][1]["model"] == "claude-opus-5-5"
    assert calls[-1][1]["output_config"] == {"effort": "high"}
    assert "components" in system_prompt().lower()
