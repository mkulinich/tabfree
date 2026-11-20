# Security policy

TabFree hosts business data and runs AI-written code, so security reports are taken seriously.

## Supported versions

The project is pre-alpha (Phase 0). No release is supported for production use yet. Once v1.0 ships, this section will list the supported release lines.

## Reporting a vulnerability

Please do not open a public issue. When the repository is public, use GitHub's private vulnerability reporting ("Report a vulnerability" on the Security tab). Until then, contact the maintainers privately. Include steps to reproduce, the impact, and any suggested fix. We aim to acknowledge reports within 3 working days.

## Areas we especially care about

- **Permission bypass:** seeing or changing content without the required capability, or evading Tableau-compatible deny rules.
- **Row-level security bypass:** obtaining rows that a user's policy should filter out, including through caches, exports, subscriptions or the MCP server.
- **Query injection:** any path from user or AI input to SQL text other than through the semantic compiler's allow-listed identifiers and bound parameters.
- **Dashboard sandbox escape:** a dashboard frame reaching cookies, tokens or the network, or acting outside its message channel.
- **AI authoring:** prompt injection through data values or workbook text that leads to unauthorised actions, data exfiltration, or publishing without approval.
- **Secrets:** credentials appearing in logs, files, revisions, or responses.

## Known, documented risks

- **Custom dashboard JavaScript.** Custom JS in a dashboard can leak data that the viewer is allowed to see by navigating its own frame to another URL. Browsers cannot block this from the embedding page. Dashboards built only from `<tf-*>` components contain no custom JS. Custom JS must pass a static scan and a human review before publishing (ADR-0002).
