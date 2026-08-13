# TDR-018 — Prompt, Context, and Versioning

- **Status:** In Review
- **Date:** 2026-08-03

## Context and options

Options were ad-hoc prompt strings/full history, provider-managed prompts, or immutable templates and staged minimum-sufficient context.

## Decision

After Gateway acceptance creates `AIInvocationId`, use implementation-local stable prompt references with immutable versions, typed inputs, output schema, evaluation evidence, and rollback. Assemble and budget context progressively from current input to focused excerpts and expand only on explicit signals. No full prompt/context assembly or context-dependent token estimation occurs during pre-acceptance admission. Memory remains an external untrusted evidence source. The first implementation is one static, allowlisted structured capability governed by ES-016; it begins at Stage 1 and does not authorize retrieval, model-assisted compression, tools, or product prompts.

## Consequences

Template/catalog management and evaluation become release concerns. Full history is prohibited by default, reducing cost and injection exposure. Prompt packages are implementation-local value references, not canonical Domain identities. Package lookup, typed binding, deterministic validation, rollback selection, and offline scoring do not call a model. A schema-repair invocation, if separately allowed by the capability policy, is metered and shares the original invocation's cumulative budget and bounds.

## Revisit evidence

Prompt owners review when protected evaluations show staged context misses required evidence, context compression causes a confirmed factuality regression, or prompt release rollback exceeds the adopted recovery objective. Migration preserves version traceability, instruction hierarchy, Memory ownership, and least-data rules.
