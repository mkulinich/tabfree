# ADR-008: Claude Agent SDK in per-session Fargate sandboxes; AI stays optional

## Status

Accepted, 2026-11-20.

## Context

"Edit with Claude" and migration repair run a coding agent that writes executable dashboard code while reading untrusted data. Fencing that data "doesn't guarantee" safety; permission scoping is what limits the damage. Headless Claude Code without `--bare` runs a project's hooks and connects its `.mcp.json` servers even in an untrusted folder. Anthropic notes that tokens typically cost an order of magnitude more than the agent's container. Claude in Amazon Bedrock runs with zero operator access but lacks the Files API, server-side tools, the MCP connector and Managed Agents. The TypeScript Agent SDK is under Anthropic's Commercial Terms of Service, not an OSI license; the Python SDK repository is MIT, but either way enabling AI means accepting Anthropic's or AWS's terms.

## Decision

- **One sandbox per session:** the Author service starts an ECS Fargate task per authoring session, running the Agent SDK on the bundle checked out in its workspace. Settings sources, auto memory, web tools and general shell access are off; turns and budget are capped.
- **No general egress:** the task reaches only the model endpoint (Amazon Bedrock by default, or the Anthropic API) and the TabFree MCP endpoint. The model is chosen per task (P4.7).
- **Quick edits:** an in-process tool loop in the API handles small edits (target under 30 seconds) through the same MCP tools, without starting a sandbox.
- **On-behalf-of tokens:** the MCP connection carries a short-lived token minted for the requesting user, so every tool call is authorised and RLS-filtered as that user and logged to `audit_event` with the agent session id.
- **Fenced untrusted data** in every tool result that carries data values.
- **Two-step publish:** `request_publish`, then `confirm_publish`, gated by the Web Edit or Overwrite capability and an optional approval policy.
- **AI is optional:** the module sits behind a provider interface, and TabFree runs fully with it switched off.

## Consequences

### Positive

- An injected instruction cannot exceed the user's own permissions or send data anywhere except the model endpoint and MCP.
- On the Bedrock path, data stays in AWS.
- The core stays fully open source.

### Negative

- Task start adds latency (not yet measured), hence the quick-edit path, which is a second path to secure.
- Tokens are the largest variable cost (risk R6), so per-user and per-site budgets are required.
- CI must also test the AI-off configuration.

## Alternatives considered

- **Claude Code unsandboxed on the API host:** runs repository hooks and MCP configurations, shares the host's credentials and gives no per-session isolation.
- **Anthropic Managed Agents:** not available on Bedrock; possible later for Anthropic API deployments.
- **AI as a required component:** would make a non-OSI dependency mandatory.

## Validation and revisit triggers

- **M4:** a Web Edit user's change is reviewed and published in under two minutes, and a developer builds a dashboard from Claude Code over MCP.
- **Security tests (§10, P7.3):** sandbox escape attempts, prompt-injection fixtures in data values, permission-bypass attempts on every MCP tool.
- **Pilot:** measure tokens per session with `total_cost_usd`; the cost model assumes $0.5–1.5.
- **Revisit if:** the Agent SDK's license changes, Bedrock gains the missing features, or sandbox start latency is unacceptable.

## References

- [BUILD_PLAN.md §3](../BUILD_PLAN.md#3-architecture-decisions-convert-each-to-an-adr-in-phase-0) (ADR-008), [§7](../BUILD_PLAN.md#7-phases-and-milestones) (P4.1–P4.7) and [§11](../BUILD_PLAN.md#11-risks-and-mitigations) (R5, R6, R13)
- Research report: [agent sandbox](../research/research-report.md#a-thin-governed-core-a-json-spec-and-an-owned-compiler-anchor-the-build), [licensing](../research/research-report.md#the-name-the-license-and-tableaus-roadmap-carry-the-non-technical-risk); notes: [AI authoring](../research/notes/aws_architecture_ai_authoring.md#5-ai-authoring-inside-the-product-server-side-claude-agents-bedrock-the-tabfree-mcp-server-versioning-and-validation-prompt-injection)
- Agent SDK: [hosting](https://code.claude.com/docs/en/agent-sdk/hosting), [secure deployment](https://code.claude.com/docs/en/agent-sdk/secure-deployment); [headless mode](https://code.claude.com/docs/en/headless)
- [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock), [Metabase MCP lessons](https://www.metabase.com/blog/mcp-tool-design-lessons)
- [TypeScript Agent SDK](https://github.com/anthropics/claude-agent-sdk-typescript), [Python Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
