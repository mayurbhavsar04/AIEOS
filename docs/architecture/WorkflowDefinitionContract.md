---
title: Workflow Definition Contract
version: 2.1
status: Approved
owner: CTO / Architect
last_updated: 2026-08-25
---

# Workflow Definition Contract

## Version history

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 2.1 | 2026-08-25 | CTO / Architect | Approved v2 activation under the documented compatibility and activation matrix after exact-SHA governance review of `8d7e55317818a4c4491dd985d1d639f6a7d956a5` with Blocking 0 / Major 0 / Minor 0 / Notes 0; Phase 6 implementation remains prohibited until PR #32 is merged and a separate implementation PR is explicitly authorized. |
| 2.0 | 2026-08-24 | CTO / Architect | Added the In Review v2 enclosing shape, catalog-derived AI classification, and Workflow AI budget envelope activation boundary; no implementation was authorized. |

## Authoritative boundary

The Domain Model remains authoritative for Workflow Definition identity, immutability, and Workflow
Engine ownership. This document is the smallest serialized activation contract for
`WorkflowDefinitionVersion` v2. Its normative schema is
[workflow-definition-v2.schema.json](schemas/workflow-definition-v2.schema.json). No previously
accepted legacy definition shape is amended or reinterpreted.

Version 2 adds one optional `WorkflowAIBudgetEnvelope` member at the immutable definition-version
boundary and requires each step to bind its exact immutable `SkillVersionId`, `CapabilityId`, and
`CapabilityContractVersionId`. It has no caller-controlled AI/non-AI marker. At definition
acceptance, Workflow Engine resolves each exact binding through the authoritative immutable
Skill/Capability catalog. The catalog's closed route outcome determines whether the step is
AI-capable; free text, generic metadata, model output, provider metadata, and a Workflow-definition
claim are not classification authority. A retained extension named `ExecutionBoundary`, if present
for compatibility, is ignored for this decision and cannot disable an envelope. An unknown,
incompatible, or changed route fails closed.

The same exact binding is resolved again before an AI Gateway handoff. A v2 definition whose resolved
route is AI Gateway requires the envelope, and every later Gateway request requires the matching
committed Workflow admission binding. A definition whose every resolved route is non-AI may omit an
envelope. Schema shape validates the immutable binding; the hosted behavioral compatibility gate
performs the required catalog-derived classification and envelope rule because JSON Schema cannot
resolve a Capability Registry record.

The envelope's `WorkflowDefinitionVersionId`, `PolicyId`, `PolicyVersionId`, `TenantId`, and
`WorkspaceId` MUST exactly equal the enclosing definition values. Any mismatch rejects the
definition. The exact accepted definition and envelope are content-immutable. Unknown definition,
envelope, or unit-registry versions reject without downgrade or default adoption.

## Compatibility and activation matrix

| Enclosing definition | Step classification | Envelope | Required behavior |
| --- | --- | --- | --- |
| Accepted legacy version | Proven non-AI under that version's approved semantics | Absent | Valid; existing non-AI behavior is unchanged. |
| Accepted legacy version | Resolves to or attempts an AI Gateway capability | Absent | Reject before Gateway/provider dispatch. Runtime discovery does not grant a default envelope. |
| v2 | Every exact immutable binding resolves `NON_AI` | Absent or exact valid v1 | Accept subject to ordinary authorization and compatibility checks. |
| v2 | Any exact immutable binding resolves `AI_GATEWAY` | Exact valid v1 with matching source and scope | Accept; each AI dispatch remains subject to current authorization, serialized admission, and the matching Workflow admission binding. |
| v2 | Any exact immutable binding resolves `AI_GATEWAY` | Absent, malformed, unknown, stale/incompatible, or source/scope/unit mismatched | Reject before Gateway/provider dispatch. No unlimited/default/fallback envelope. |
| Unknown future version | Any | Any | Reject without stripping fields, adopting v2, or downgrading. |

For a legacy definition, Workflow Engine resolves the step's already-approved immutable Skill
Version and Capability Contract before dispatch. If that route is AI Gateway, or cannot be proved
non-AI, the step is AI-capable and fails closed without a valid governed enclosing version. Legacy
caller labels and generic metadata are not classification authority.

Immutable budget meaning does not freeze security authority. Before every AI-capable admission and
again at dispatch, the existing security architecture revalidates current caller/service authority,
Tenant/Workspace scope, and whether the exact `PolicyId`/`PolicyVersionId` is active and authorized.
Revocation, disablement, stale/incompatible policy, lost authority, or cross-scope evidence fails
closed. The accepted definition/envelope is neither mutated nor silently replaced.
