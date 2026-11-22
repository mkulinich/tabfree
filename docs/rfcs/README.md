# RFCs

An RFC is for a change big enough that people should agree on it before code is written: a new public
API or schema version, a change to the security model, a new external service, or a change to the
project's rules (`GOVERNANCE.md` at the repository root).

1. Copy [0000-template.md](0000-template.md) to `NNNN-short-title.md` (the next number) and open a
   pull request with it. Mark it *Draft* while you are still writing.
2. Discussion happens on the pull request. Update the RFC as it changes; keep the alternatives
   section honest.
3. After at least 10 days of review, a maintainer proposes a decision: **accepted**, **rejected** or
   **postponed**, with reasons. If no maintainer objects within 5 more days, it stands.
4. Accepted RFCs are merged with their status set and become tasks in the roadmap; the design decisions
   inside them become ADRs as they are implemented.

Small, clearly scoped changes do not need an RFC: open an issue or a pull request.
