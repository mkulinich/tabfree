# tabfree-cli

The `tabfree` command (BUILD_PLAN P4.2): author TabFree dashboards from a folder, with Claude Code or
any editor. It uses only the standard library and talks to the server's MCP endpoint.

```bash
tabfree login https://tabfree.example.com    # browser sign-in (OAuth 2.1, PKCE, loopback redirect)
tabfree pull sales-overview                  # tabfree.json + index.html into ./sales-overview/
tabfree push sales-overview                  # your edits into your private draft, with validation
tabfree validate sales-overview              # compile on both dialects, run the queries, scan scripts
tabfree preview sales-overview               # the draft's live preview in the portal
tabfree publish sales-overview -m "message"  # shows the diff, asks, then publishes
tabfree kit .                                # CLAUDE.md, .mcp.json and the tabfree-dashboard skill
```

Credentials: `tabfree login` saves your OAuth tokens to `~/.config/tabfree/credentials.json` (mode
0600). Scripts can use a personal access token in `TABFREE_TOKEN` instead (inject it from a secrets
manager; never write it to a file in the repository).

**Claude Code:** `tabfree kit .` writes `.mcp.json` pointing at `https://<server>/mcp`. Claude Code
discovers the OAuth server from the MCP endpoint and signs you in; the skill in
`.claude/skills/tabfree-dashboard` teaches it the workflow.
