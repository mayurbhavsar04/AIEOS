# TDR-018 — Prompt, Context, and Versioning

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were ad-hoc prompt strings/full history, provider-managed prompts, or immutable templates and staged minimum-sufficient context.

## Decision

Use stable prompt identities with immutable versions, typed inputs, output schema, evaluation evidence, and rollback. Assemble context progressively from current input to focused excerpts and expand only on explicit signals. Memory remains an external untrusted evidence source.

## Consequences

Template/catalog management and evaluation become release concerns. Full history is prohibited by default, reducing cost and injection exposure.

## Revisit evidence

Prompt owners review when protected evaluations show staged context misses required evidence, context compression causes a confirmed factuality regression, or prompt release rollback exceeds the adopted recovery objective. Migration preserves version traceability, instruction hierarchy, Memory ownership, and least-data rules.

