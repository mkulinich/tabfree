<!-- Title: the task ID first, e.g. "P7.7: delta migration for cutover". -->

## What and why

## How it was tested

- [ ] Tests added or updated (conversion and compiler bugs also get a corpus case)
- [ ] `make check` passes
- [ ] Docs updated (commands, schemas, behaviour)

## Rules

- [ ] No SQL, credentials or network calls in dashboards; data only through named queries
- [ ] No secrets, customer workbooks or extracts in the change
- [ ] Tableau files parsed as untrusted; migration stays read-only against Tableau Server
- [ ] Data sent to an LLM is fenced
- [ ] An ADR or RFC when this changes a decision or the security model
