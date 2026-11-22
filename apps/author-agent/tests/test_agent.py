"""The sandbox agent's configuration: only TabFree MCP tools, no settings, capped (ADR-008)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "tabfree_author_agent" / "agent.py"
spec = importlib.util.spec_from_file_location("tabfree_author_agent_agent", SRC)
assert spec is not None
assert spec.loader is not None
agent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = agent
spec.loader.exec_module(agent)

ENV = {
    "TABFREE_MCP_URL": "https://tabfree.internal/mcp",
    "TABFREE_AGENT_TOKEN": "tfo_x",
    "TABFREE_DRAFT": "d" * 36,
    "TABFREE_REQUEST": "make the region chart stacked",
    "TABFREE_MODEL": "anthropic.claude-opus-5-5",
    "TABFREE_AI_PROVIDER": "bedrock",
    "AWS_REGION": "us-east-1",
}


def test_options_allow_only_tabfree_tools() -> None:
    options = agent.config(ENV)
    assert options["setting_sources"] == []
    assert all(t.startswith("mcp__tabfree__") for t in options["allowed_tools"])
    assert "mcp__tabfree__confirm_publish" not in options["allowed_tools"]
    assert {"Bash", "Read", "Write", "WebFetch", "WebSearch"} <= set(options["disallowed_tools"])
    assert options["mcp_servers"]["tabfree"]["headers"]["Authorization"] == "Bearer tfo_x"
    assert options["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert options["max_turns"]
    assert options["max_budget_usd"]
    with pytest.raises(SystemExit):
        agent.config({**ENV, "TABFREE_AGENT_TOKEN": ""})


def test_progress_events() -> None:
    assert agent.describe(SimpleNamespace(text="Done.")) == {"type": "assistant", "text": "Done."}
    assert agent.describe(SimpleNamespace(name="mcp__tabfree__edit_dashboard_files")) == {
        "type": "tool",
        "text": "edit dashboard files",
    }
    assert "make the region chart stacked" in agent.prompt(ENV)
