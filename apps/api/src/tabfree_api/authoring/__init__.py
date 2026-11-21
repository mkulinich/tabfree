"""AI authoring (BUILD_PLAN Phase 4): drafts, validation, the MCP server, OAuth, and "Edit with Claude".

- :mod:`files` turns a revision into bundle files and applies patch-style edits.
- :mod:`validation` is the pipeline every draft passes before publishing (P4.5, shared with migration).
- :mod:`drafts` keeps drafts and the two-step publish in the metadata database.
- :mod:`service` is where permissions are checked: the MCP tools, the portal and the agents all call it.
- :mod:`tools` and :mod:`mcp` expose it as an MCP server (P4.1); :mod:`oauth` issues its tokens.
- :mod:`budget`, :mod:`provider`, :mod:`quick` and :mod:`author` run Claude (P4.3, P4.4, P4.6, P4.7).

AI is optional (ADR-008): with ``TABFREE_AI_PROVIDER=off`` the drafts, validation and MCP server
still work for people who author from Claude Code on their own machine.
"""
