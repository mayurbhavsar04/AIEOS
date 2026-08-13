# TDR-018 — Prompt, Context, and Versioning

- **Status:** In Review
- **Date:** 2026-08-03

## Context and options

Options were ad-hoc prompt strings/full history, provider-managed prompts, or immutable templates and staged minimum-sufficient context.

## Decision

Prompt Pipeline owns the implementation-local static prompt-package catalog, immutable template/package construction and versioning, declared-variable binding, accepted-lifecycle assembly artifacts, output-schema lookup, and rollback selection. Capability Registry owns only capability contracts and catalog metadata; Skill Runtime executes the capability attempt; AI Gateway does not own product/business prompt logic.

Before Gateway acceptance, the capability owner resolves the approved contract and package references, validates typed input, and performs deterministic/authoritative-result bypass. It calls no model and creates no `AIInvocationId` when bypassed. Gateway acceptance then atomically creates `AIInvocationId`; within the existing additive `accept -> execute` seam, Prompt Pipeline performs Stage 1 binding and assembly during the frozen Gateway-internal accepted lifecycle before provider preparation/dispatch. This preserves scoped request identity, admission replay, and the `Requested -> PolicyValidated -> ProviderSelected` sequencing. No new component or public contract is introduced.

Use stable prompt references with immutable versions, typed inputs, exact output schema, evaluation evidence, and rollback. Phase 5 permits Stage 1 only: minimum sufficient approved instruction, typed current input, and schema. Any future progressive expansion requires explicit bounded signals and separate approved scope. Memory remains external untrusted evidence; retrieval, model-assisted compression, tools, and product prompts are not authorized.

## Consequences

Template/catalog management and evaluation become Prompt Pipeline release concerns. Full history is prohibited by default, reducing cost and injection exposure. Prompt packages are implementation-local value references, not canonical Domain identities. Package lookup, typed binding, deterministic validation/bypass, rollback selection, capability acceptance, and offline scoring do not call a model.

The capability supplies only the immutable output-schema reference and existing repair policy. AI Gateway alone validates provider structured output and may perform at most one schema repair through its existing `AIInvocationId`-scoped durable effect, idempotency, provider-attempt/failover, and cumulative-budget/accounting machinery. A capability may deterministically accept or reject the canonical Gateway Result for its domain contract; it MUST NOT duplicate provider parsing/schema validation, create an LLM repair/retry/fallback loop, or call a model after Gateway completion.

Verified Tenant/Workspace scope and authorization, purpose, classification, residency, retention,
privacy/redaction, and cache constraints propagate fail-closed through selection, accepted-lifecycle
assembly, Gateway policy, persistence, replay, evaluation evidence, and telemetry. Existing Gateway
observability/accounting provides per-execution bypass/invocation, token-category, governed-cost,
route, cache, repair/fallback/attempt, and budget evidence. No duplicate ledger is created; raw
sensitive prompt/context/output is not logged by default; identifiers are not metric labels.

## Revisit evidence

Prompt owners review when protected evaluations show staged context misses required evidence, context compression causes a confirmed factuality regression, or prompt release rollback exceeds the adopted recovery objective. Migration preserves version traceability, instruction hierarchy, Memory ownership, and least-data rules.

## Approval transition

CTO approval changes this record from `In Review` to the repository's accepted decision status
`Accepted` and updates the decision index before Phase 5 runtime implementation is authorized.
