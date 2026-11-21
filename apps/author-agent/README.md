# tabfree-author-agent

The sandbox behind "Edit with Claude" for larger changes (BUILD_PLAN P4.3, [ADR-008](../../docs/adr/0008-sandboxed-claude-agent-runtime.md)).
The Author service in the API starts one ECS Fargate task per session with this image. The agent:

- runs the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) on Claude in Amazon Bedrock (default) or the Claude API;
- has no built-in tools (no shell, files or web) and no filesystem settings (`setting_sources=[]`), only the TabFree MCP tools;
- authenticates with a 30-minute on-behalf-of token for the requesting user, so it can do exactly what that user can, and the server refuses `confirm_publish` for it;
- reports progress to `/api/agent-sessions/{id}/report` and stops at 40 turns or $3.

Small edits never start a sandbox: the API's in-process quick-edit loop (P4.4) handles them.

```bash
uv run --with pytest pytest apps/author-agent/tests   # orchestration, without the SDK
docker build -t tabfree-author-agent apps/author-agent
```

The image has not yet been built or run against AWS.
