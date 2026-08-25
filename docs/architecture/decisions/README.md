---
title: Architecture Decision Records
version: 1.5
status: Accepted
owner: CTO / Architect
last_updated: 2026-08-25
---

# Architecture Decision Records

Architecture Decision Records (ADRs) record reviewed changes to contract interpretation or frozen
architecture that cannot be safely represented as an editorial clarification. A record is not an
implementation authorization. Proposed and In Review records require approval before implementation.

| ID | Decision | Status |
| --- | --- | --- |
| [ADR-001](ADR-001-Authoritative-Result-Reuse.md) | Govern authoritative-result reuse through `DispatchExecutionAttempt` v2 | Accepted |
| [ADR-002](ADR-002-Workflow-AI-Budget-Envelope-Contract.md) | Govern the typed immutable Workflow AI budget envelope v1 | Accepted |

The Runtime Technology Decision Records remain separately indexed in
[runtime decisions](../../runtime-architecture/decisions/README.md).

## Version history

| Version | Date | Author | Notes |
| --- | --- | --- |
| 1.5 | 2026-08-25 | CTO / Architect | Accepted ADR-002 after exact-SHA governance review of `8d7e55317818a4c4491dd985d1d639f6a7d956a5` with Blocking 0 / Major 0 / Minor 0 / Notes 0; Phase 6 implementation remains prohibited until PR #32 is merged and a separate implementation PR is explicitly authorized. |
| 1.4 | 2026-08-24 | CTO / Architect | Added ADR-002's fenced Workflow admission to Skill Runtime to Gateway binding, catalog-derived v2 classification, and hosted behavioral validation gate; status remains In Review. |
| 1.3 | 2026-08-24 | CTO / Architect | Completed ADR-002 alternatives/consequences and linked the canonical Workflow Definition v2 activation boundary; status remains In Review. |
| 1.2 | 2026-08-21 | CTO / Architect | Added ADR-002 and moved the index to In Review for the focused Workflow AI-budget contract amendment. |
| 1.1 | 2026-08-15 | CTO / Architect | Indexed accepted ADR-001 authoritative-result reuse governance. |
