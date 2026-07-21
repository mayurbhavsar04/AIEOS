---
title: ES-004 — Command Contract Model
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# ES-004 — Command Contract Model

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-004 |
| **Milestone** | Milestone 4 — Platform Contracts & Service Interfaces, Phase 1 |
| **Priority** | Critical |
| **Implementer** | Engineer (Codex) |
| **Architecture baseline** | Architecture v1.0, frozen at tag `architecture-v1.0` |
| **Domain baseline** | Domain v1.0, frozen at tag `domain-v1.0` plus governance commit on `main` |
| **Architecture status** | Conforms; no boundary change or ADR is introduced. |

## Related Documents

| Relationship | Document |
| --- | --- |
| **PRD** | Pending — no approved PRD exists; ES-004 authorizes documentation only. |
| **Architecture** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) and [System Architecture](../03-architecture/SystemArchitecture.md) |
| **Canonical execution architecture** | [Execution Flow](../architecture/ExecutionFlow.md) |
| **Canonical domain reference** | [Domain Model](../architecture/DomainModel.md) |
| **Prior specifications** | [ES-001 — Execution Core](ES-001-Execution-Core.md), [ES-002 — Execution Flow Architecture](ES-002-Execution-Flow-Architecture.md), and [ES-003 — Domain Model and Ubiquitous Language](ES-003-Domain-Model-and-Ubiquitous-Language.md) |
| **Canonical deliverable** | [Command Contract Model](../architecture/CommandContract.md) |
| **ADRs** | None required; this specification refines a deferred contract without changing frozen boundaries or semantics. |
| **Future specifications** | Event, Error, Idempotency, Policy, authorization, and component service-interface contracts remain pending. |
| **Related Pull Requests** | Pending — update after Draft Pull Request creation. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-21 | CTO / Architect | Initial Milestone 4 Phase 1 specification. |

## 1. Objective

ES-004 SHALL define the canonical Command envelope and transport-neutral Command-processing contract for AIEOS. It SHALL make creation, validation, authorization, routing, acknowledgement, execution, cancellation, expiry, retry, idempotency, security, versioning, and audit expectations explicit while preserving Architecture v1.0 and Domain v1.0.

This specification authorizes documentation only. It MUST NOT authorize product code, infrastructure, APIs, storage, deployment, transport, or provider selection.

## 2. Scope

ES-004 SHALL define:

- the canonical Command envelope and required versus optional fields;
- Command creation and immutable processing lifecycle;
- creator, owner, validator, dispatcher, consumer, and acknowledger responsibilities;
- the abstract command-dispatch contract and routing responsibilities;
- delivery redelivery, Workflow retry, and provider retry separation;
- ordering and idempotency expectations;
- schema, semantic, authorization, and invariant validation;
- Command versioning, compatibility, and deprecation;
- immutable fields, trusted metadata, payload ownership, and audit evidence;
- acknowledgement and completion distinctions;
- required Mermaid diagrams; and
- traceability and review criteria.

## 3. Frozen Constraints

The deliverable MUST preserve these constraints:

1. A Command is an immutable directed message record requesting an action from exactly one accountable target.
2. Commands do not pass through Event Bus.
3. Events remain separate immutable facts published through Event Bus.
4. No Command grants mutation authority beyond the target's frozen ownership boundary.
5. Workflow Engine owns Workflow and Workflow-step state and retry decisions.
6. Skill Runtime owns one Execution Attempt lifecycle and does not invent retries.
7. Each Workflow retry creates a new `ExecutionId` and incremented `AttemptNumber`; terminal attempts remain immutable.
8. `CorrelationId` and `WorkflowId` remain stable across Workflow retries; `CausationId` records the immediate cause.
9. Tenant and Workspace isolation remains explicit.
10. No component, aggregate, service, route, Command meaning, identity, or owner may be added or changed.

If satisfying ES-004 would violate a constraint, work MUST stop for architecture review rather than introduce an ADR or solution within this milestone.

## 4. Canonical Envelope Requirements

The canonical deliverable SHALL define these fields:

| Field | Required status |
| --- | --- |
| `CommandId` | Required |
| `CommandType` | Required |
| `CommandVersion` | Required |
| `CorrelationId` | Required |
| `CausationId` | Required |
| `WorkflowId` | Required when an existing Workflow is applicable |
| `WorkflowStepId` | Required when a Workflow Step is applicable |
| `ExecutionId` | Required when an Execution Attempt is applicable |
| `TargetComponent` | Required; exactly one accountable target |
| `Initiator` | Required |
| `Timestamp` | Required; UTC creation time |
| `TenantId` | Required for Tenant-scoped work |
| `WorkspaceId` | Required for Workspace-scoped work |
| `Payload` | Required; governed by `CommandType` and `CommandVersion` |
| `Metadata` | Required; structured and versioned |

The contract SHALL define conditional presence, field relationships, immutability, identity behavior on redelivery, and identity behavior for a new Workflow Execution Attempt. It SHALL place idempotency, authorization, time constraints, Request context, trace context, and attempt context in structured metadata without treating metadata as self-authenticating authority.

## 5. Lifecycle Requirements

The canonical deliverable SHALL specify:

- creation before dispatch;
- complete target validation;
- execution-time authorization;
- dispatch to one declared target;
- execution within the target's existing boundary;
- acceptance or rejection acknowledgement;
- normalized terminal completion;
- cancellation by a distinct authorized Command; and
- expiry before acceptance and its relationship to existing timeout/cancellation ownership after acceptance.

The lifecycle MUST describe processing observations associated with an immutable Command. It MUST NOT redefine Command as a mutable Domain Entity or override Execution Attempt, Workflow, Workflow Step, Human Approval, or Artifact lifecycles.

## 6. Ownership Requirements

The deliverable SHALL state:

- the component owning the decision creates the Command;
- the one `TargetComponent` owns acceptance, rejection, processing, and terminal disposition for that Command;
- the target performs authoritative validation at its trust boundary;
- the creator owns the dispatch decision;
- delivery infrastructure, if any, acts only on behalf of the creator;
- exactly one logical target consumes the Command; and
- the target acknowledges acceptance or rejection.

Infrastructure delivery confirmation MUST NOT be described as target acknowledgement or business completion.

## 7. Routing, Retry, Ordering, and Idempotency

The abstract command-dispatch contract SHALL preserve the immutable envelope, resolve only the declared target, refuse ambiguity, propagate context, distinguish delivery failure from target failure, and expose delivery observations.

It MUST NOT select business targets from payload, fan out accountability, change authority or scope, route through Event Bus, make Workflow decisions, or infer completion.

The contract SHALL specify that:

- global ordering and ordering across unrelated Commands are not guaranteed;
- targets enforce invariants from authoritative state rather than delivery order alone;
- any required ordering scope is explicit and versioned;
- bounded delivery redelivery retains the same `CommandId` and is not a Workflow retry;
- Workflow Engine creates a new `CommandId` and `ExecutionId` for an approved new Execution Attempt;
- Skill Runtime never creates the next attempt; and
- AI Gateway provider retry remains within its approved invocation boundary.

Every Command SHALL have a target-owned idempotency strategy. Duplicate handling MUST avoid uncontrolled effects, expose in-progress or terminal disposition safely, and reject identity reuse with changed immutable content. Retention and storage details remain deferred.

## 8. Validation Requirements

The deliverable SHALL distinguish:

1. **Schema validation:** presence, type, structure, version, and payload form.
2. **Semantic validation:** field relationships, resource meaning, lifecycle applicability, and payload meaning.
3. **Authorization validation:** identity, delegated scope, Policy Version, approval evidence, Tenant/Workspace access, and current authority.
4. **Invariant validation:** exactly-one-target, ownership, aggregate rules, state validity, retry identities, and idempotency scope.

Unknown versions, targets, fields, and authority claims SHALL fail closed unless a supported compatibility rule explicitly permits them.

## 9. Versioning Requirements

`CommandVersion` SHALL version the complete interpretation of one `CommandType`. Breaking changes require a new version. Targets SHALL declare supported versions and reject unsupported versions deliberately. Compatibility MUST NOT weaken validation, authorization, isolation, ownership, routing, or idempotency.

Deprecation guidance SHALL name the owner, replacement, affected participants, migration guidance, compatibility window, and removal criteria. Historical Commands remain immutable and interpretable for required audit purposes.

## 10. Security Requirements

The canonical deliverable SHALL define:

- all envelope fields as immutable after creation;
- corrections or changed scope, target, payload, authority, or time constraints as new Commands;
- metadata trust as producer-, integrity-, field-, and version-specific;
- target-side revalidation of authorization and scope;
- payload contract ownership by Command type and interpretation by the target;
- data minimization and credential exclusion; and
- durable, protected audit evidence for consequential Commands.

Audit expectations SHALL cover identity, type, version, target, initiator, scope, timing, authorization references, Policy Version, idempotency, duplicate handling, cancellation, expiry, failures, correlation, causation, and result or external-effect evidence where applicable.

## 11. Required Mermaid Diagrams

The canonical deliverable SHALL include GitHub-compatible Mermaid diagrams for:

1. Command lifecycle;
2. Command routing;
3. ownership;
4. validation;
5. acknowledgement;
6. retry flow; and
7. idempotency.

Diagrams MUST agree with prose and MUST NOT imply a transport, queue, database, serialization format, deployment unit, new component, or Commands passing through Event Bus.

## 12. Non-Goals

ES-004 explicitly excludes:

- transport protocols;
- REST;
- gRPC;
- databases and persistence schemas;
- queues, brokers, and messaging products;
- implementation languages and frameworks;
- serialization formats;
- deployment architecture;
- product-specific Workflow behavior;
- complete Event, Error, Policy, authorization, or Idempotency standards; and
- changes to Architecture v1.0 or Domain v1.0.

## 13. Acceptance Criteria

- [ ] `docs/engineering-specifications/ES-004-Command-Contract-Model.md` exists and follows the ES process.
- [ ] `docs/architecture/CommandContract.md` exists as the canonical deliverable.
- [ ] Every required envelope field is defined as required or conditionally required.
- [ ] Every Command has exactly one accountable target.
- [ ] Creation, validation, authorization, dispatch, execution, acknowledgement, completion, cancellation, and expiry are specified.
- [ ] Creator, owner, validator, dispatcher, consumer, and acknowledger responsibilities are explicit.
- [ ] Routing remains abstract and technology-neutral.
- [ ] Delivery redelivery is distinct from Workflow and provider retry.
- [ ] Ordering and idempotency expectations are explicit.
- [ ] Schema, semantic, authorization, and invariant validation are distinct.
- [ ] Versioning, compatibility, and deprecation are defined.
- [ ] Immutable fields, trusted metadata, payload ownership, and audit expectations are defined.
- [ ] All seven required Mermaid diagrams are present and valid.
- [ ] Commands are never shown passing through Event Bus.
- [ ] Architecture v1.0 ownership and boundaries remain unchanged.
- [ ] Domain v1.0 meaning, identities, aggregates, Commands, Events, and invariants remain unchanged.
- [ ] No excluded implementation or technology choice is introduced.
- [ ] Terminology is consistent with the frozen sources.
- [ ] Relative links resolve.
- [ ] `git diff --check` passes.

## 14. Review Checklist

Reviewers SHALL verify:

- exactly one accountable target and no fan-out of responsibility;
- no new logical component, aggregate, service, route, or state owner;
- no Command/Event semantic overlap;
- no Command transported by Event Bus;
- target validation and execution-time authorization cannot be bypassed;
- acknowledgement is distinct from delivery and completion;
- Workflow Engine alone owns Workflow retry decisions;
- Skill Runtime does not revive or create attempts;
- redelivery and retry identity behavior matches Domain v1.0;
- Tenant and Workspace scope is explicit without claiming exposed multi-tenant SaaS behavior;
- metadata does not become self-asserted authority;
- transport, persistence, language, serialization, and deployment remain deferred; and
- prose, tables, and Mermaid diagrams agree.

## 15. Traceability

| Source | ES-004 trace |
| --- | --- |
| [Architecture v1.0 — Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) | Refines explicit typed/versioned Command contracts and one-owner responsibilities within existing logical components. |
| [Architecture v1.0 — System Architecture](../03-architecture/SystemArchitecture.md) | Preserves trust-boundary validation, correlated Commands, safe failure, audit, and infrastructure neutrality. |
| [Architecture v1.0 — Execution Flow](../architecture/ExecutionFlow.md) | Refines the deferred complete Command envelope and abstract dispatch contract without changing execution or retry ownership. |
| [Domain v1.0](../architecture/DomainModel.md) | Preserves Command classification, identity, exactly-one-target invariant, scope, canonical ownership, correlation, causation, and immutable retries. |
| [ES-001](ES-001-Execution-Core.md) | Delivers the shared Command Envelope documentation requirements without implementing technology. |
| [ES-002](ES-002-Execution-Flow-Architecture.md) | Refines required Command fields, dispatch, retry, timeout/cancellation, idempotency, and traceability behavior. |
| [ES-003](ES-003-Domain-Model-and-Ubiquitous-Language.md) | Preserves frozen Command and Event meaning, names, identities, owners, aggregate boundaries, and invariants. |

## 16. Definition of Done

- [ ] Only the two intended documentation files are created.
- [ ] All acceptance criteria and review checks are satisfied.
- [ ] No architecture or domain conflict requires an ADR.
- [ ] Validation evidence is recorded in the Draft Pull Request.
- [ ] A Draft Pull Request is opened against `main` and is not merged.

## 17. Implementation Instructions

The Engineer SHALL work from current `main`, create `docs/es-004-command-contract-model`, change only the two ES-004 documentation files, validate the deliverables, commit with `docs: add ES-004 Command Contract Model`, push the branch, and open a Draft Pull Request titled `docs: add ES-004 Command Contract Model`.

If implementation would alter an Architecture v1.0 boundary or Domain v1.0 semantic, the Engineer MUST stop and report the conflict. The Engineer MUST NOT resolve it by inventing a component, owner, route, technology, or domain meaning.

Return to the [Engineering Specifications process](README.md).
