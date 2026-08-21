---
title: Architecture Decision Records
version: 1.2
status: In Review
owner: CTO / Architect
last_updated: 2026-08-21
---

# Architecture Decision Records

Architecture Decision Records (ADRs) record reviewed changes to contract interpretation or frozen
architecture that cannot be safely represented as an editorial clarification. A record is not an
implementation authorization. Proposed and In Review records require approval before implementation.

| ID | Decision | Status |
| --- | --- | --- |
| [ADR-001](ADR-001-Authoritative-Result-Reuse.md) | Govern authoritative-result reuse through `DispatchExecutionAttempt` v2 | Accepted |
| [ADR-002](ADR-002-Workflow-AI-Budget-Envelope-Contract.md) | Govern the typed immutable Workflow AI budget envelope v1 | In Review |

The Runtime Technology Decision Records remain separately indexed in
[runtime decisions](../../runtime-architecture/decisions/README.md).

## Version history

| Version | Date | Author | Notes |
| --- | --- | --- |
| 1.2 | 2026-08-21 | CTO / Architect | Added ADR-002 and moved the index to In Review for the focused Workflow AI-budget contract amendment. |
| 1.1 | 2026-08-15 | CTO / Architect | Indexed accepted ADR-001 authoritative-result reuse governance. |
