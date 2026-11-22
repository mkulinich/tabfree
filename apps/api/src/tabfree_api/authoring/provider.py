"""Model providers for "Edit with Claude" (BUILD_PLAN P4.7).

- ``off``: AI is switched off. Drafts, validation and the MCP server keep working (ADR-008).
- ``anthropic``: the Claude API. The key comes from ``ANTHROPIC_API_KEY`` in the environment (inject it
  with ``secret run``), never from a file. Refusals fall back server-side (``fallbacks: "default"``).
- ``bedrock``: Claude in Amazon Bedrock (the Messages API endpoint), with the task role's credentials,
  so prompts and data stay in the AWS account. Model ids get Bedrock's ``anthropic.`` prefix.

The model is chosen per task: a fast one for quick edits, the strongest for builds and migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Task = Literal["quick", "build", "migrate"]
#: Effort per task: quick edits should come back in seconds; builds can think longer.
EFFORT: dict[str, str] = {"quick": "medium", "build": "high", "migrate": "high"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Provider(Protocol):
    name: str

    def model(self, task: Task) -> str: ...

    def create(
        self,
        *,
        task: Task,
        system: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        """One Messages API call; returns the SDK's message (content, stop_reason, usage)."""
        ...


@dataclass
class ClaudeProvider:
    """The Claude API or Amazon Bedrock, through the official ``anthropic`` SDK."""

    #: "anthropic" or "bedrock".
    name: str
    models: dict[str, str]
    region: str | None = None
    timeout_s: float = 120.0
    client: Any = None

    def __post_init__(self) -> None:
        if self.client is not None:
            return
        import anthropic  # the optional ``tabfree-api[ai]`` extra

        if self.name == "bedrock":
            if not self.region:
                raise RuntimeError("TABFREE_AI_BEDROCK_REGION is required with TABFREE_AI_PROVIDER=bedrock")
            self.client = anthropic.AnthropicBedrockMantle(aws_region=self.region, timeout=self.timeout_s)
        else:
            # Credentials come from the environment (ANTHROPIC_API_KEY), never from settings files.
            self.client = anthropic.Anthropic(timeout=self.timeout_s)

    def model(self, task: Task) -> str:
        model = self.models[task]
        if self.name == "bedrock" and not model.startswith("anthropic."):
            return f"anthropic.{model}"
        return model

    def create(
        self,
        *,
        task: Task,
        system: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        params: dict[str, Any] = {
            "model": self.model(task),
            "max_tokens": max_tokens,
            "system": list(system),
            "messages": list(messages),
            "tools": list(tools),
            "output_config": {"effort": EFFORT[task]},
            # Caches the stable prefix (tools, system prompt, docs) across the loop's turns.
            "cache_control": {"type": "ephemeral"},
        }
        if self.name == "anthropic":
            # A refusal is retried on a fallback model inside the same call.
            return self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
        return self.client.messages.create(**params)


def from_settings(settings: Any) -> Provider | None:
    if settings.ai_provider == "off":
        return None
    models = {
        "quick": settings.ai_model_quick,
        "build": settings.ai_model_build,
        "migrate": settings.ai_model_migrate,
    }
    provider: Provider = ClaudeProvider(settings.ai_provider, models, region=settings.ai_bedrock_region)
    return provider
