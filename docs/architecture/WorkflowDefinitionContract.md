---
title: Workflow Definition Contract
version: 2.0
status: In Review
owner: CTO / Architect
last_updated: 2026-08-24
---

# Workflow Definition Contract

## Authoritative boundary

The Domain Model remains authoritative for Workflow Definition identity, immutability, and Workflow
Engine ownership. This document is the smallest serialized activation contract for
`WorkflowDefinitionVersion` v2. Its normative schema is
[workflow-definition-v2.schema.json](schemas/workflow-definition-v2.schema.json). No previously
accepted legacy definition shape is amended or reinterpreted.

Version 2 adds one optional `WorkflowAIBudgetEnvelope` member at the immutable definition-version
boundary and requires every step to declare `ExecutionBoundary` as exactly `NON_AI` or `AI_GATEWAY`.
`AI_GATEWAY` is the only v2 AI-capable classification. It is determined from the accepted immutable
definition before dispatch, never inferred from free text, provider metadata, model output, or a
runtime fallback. An `AI_GATEWAY` step requires the envelope; a wholly `NON_AI` v2 definition may
omit it.

The envelope's `WorkflowDefinitionVersionId`, `PolicyId`, `PolicyVersionId`, `TenantId`, and
`WorkspaceId` MUST exactly equal the enclosing definition values. Any mismatch rejects the
definition. The exact accepted definition and envelope are content-immutable. Unknown definition,
envelope, or unit-registry versions reject without downgrade or default adoption.

## Compatibility and activation matrix

| Enclosing definition | Step classification | Envelope | Required behavior |
| --- | --- | --- | --- |
| Accepted legacy version | Proven non-AI under that version's approved semantics | Absent | Valid; existing non-AI behavior is unchanged. |
| Accepted legacy version | Resolves to or attempts an AI Gateway capability | Absent | Reject before Gateway/provider dispatch. Runtime discovery does not grant a default envelope. |
| v2 | All steps explicitly `NON_AI` | Absent or exact valid v1 | Accept subject to ordinary authorization and compatibility checks. |
| v2 | Any step explicitly `AI_GATEWAY` | Exact valid v1 with matching source and scope | Accept; each AI dispatch remains subject to current authorization and serialized admission. |
| v2 | Any `AI_GATEWAY` step | Absent, malformed, unknown, stale/incompatible, or source/scope/unit mismatched | Reject before Gateway/provider dispatch. No unlimited/default/fallback envelope. |
| Unknown future version | Any | Any | Reject without stripping fields, adopting v2, or downgrading. |

For a legacy definition, the Workflow Engine resolves the step's already-approved Skill Version and
Capability Contract before dispatch. If that immutable capability route is AI Gateway, or cannot be
proved non-AI, the step is AI-capable and fails closed without a valid governed enclosing version.
Legacy caller labels and generic metadata are not classification authority.

Immutable budget meaning does not freeze security authority. Before every AI-capable admission and
again at dispatch, the existing security architecture revalidates current caller/service authority,
Tenant/Workspace scope, and whether the exact `PolicyId`/`PolicyVersionId` is active and authorized.
Revocation, disablement, stale/incompatible policy, lost authority, or cross-scope evidence fails
closed. The accepted definition/envelope is neither mutated nor silently replaced.
