---
title: Coding Standards
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Coding Standards

These standards are language-agnostic until the architecture selects a stack. A language-specific guide may add rules without contradicting this document.

## Structure and dependencies

- Organize code by business capability, with explicit public interfaces.
- Keep domain rules independent of HTTP, queues, model SDKs, storage SDKs, and UI frameworks.
- Direct dependencies inward: delivery and infrastructure adapt to application and domain contracts.
- Do not access another module's private persistence model. Use its public contract.
- Wrap external providers at the boundary. Provider responses do not become internal domain models.
- Avoid shared utility collections that conceal ownership. Promote shared code only after repeated, stable use.

## Naming and readability

- Use names that describe business meaning and units; avoid unexplained abbreviations.
- Name commands with verbs, events in past tense, and boolean values as questions.
- Prefer small functions with one visible responsibility and explicit inputs.
- Comments explain constraints and reasons, not syntax. Remove stale commentary.
- Public contracts and non-obvious invariants require documentation.

## Contracts and validation

- Type and validate data at every trust boundary.
- Reject unknown or unsupported contract versions deliberately.
- Distinguish missing, empty, invalid, and unavailable states.
- Include stable error codes for programmatic handling; human messages must not expose secrets.
- Make identifiers, timestamps, time zones, currency, and measurement units explicit.
- Treat model responses and externally sourced content as untrusted input.

## State and side effects

- Keep business logic outside prompts and transport handlers.
- Make state transitions explicit and validate permitted transitions.
- Require idempotency keys for retried operations with external effects.
- Use transactions for invariants that must change atomically; use durable workflow state across remote effects.
- Store timestamps in UTC and render them in the user's configured zone.
- Do not silently swallow partial failure. Persist enough state to retry or compensate.

## Errors and resilience

Classify errors as validation, authorization, conflict, transient dependency, permanent dependency, or internal failure. Retry only failures expected to recover, with bounded exponential backoff and jitter. Set timeouts on every remote call. Use circuit breaking or provider fallback only after measured need.

Never retry a non-idempotent effect blindly. Preserve the original cause when translating errors and log the boundary where responsibility changes.

## Configuration and secrets

- Configuration is typed, validated at startup, and separated by environment.
- Secrets never appear in source, prompts, fixtures, logs, traces, or client bundles.
- Defaults must be safe. Production-impacting configuration requires explicit values.
- Feature flags have owners, intended removal conditions, and safe fallback behavior.

## Logging and instrumentation

Emit structured records with correlation identifiers, workflow and task identifiers, outcome, duration, and stable error code where relevant. Do not log raw credentials, tokens, private model context, or unnecessary personal data. Follow [Observability](Observability.md).

## Quality controls

- Format, lint, type-check, and test through reproducible commands.
- New behavior includes tests at the lowest effective layer.
- Fixes include a regression test when practical.
- Generated code receives the same review as human-written code.
- Dependencies require a clear purpose, maintained provenance, compatible license, and security review proportional to risk.
- Avoid speculative performance optimization; measure first and record the benchmark.

## Pull-request expectations

A reviewable change is focused, explains the outcome and trade-offs, includes validation evidence, and updates affected contracts and documentation. Large changes should be separated by coherent behavior, not arbitrary file counts. See [Git Workflow](GitWorkflow.md) and [Testing](Testing.md).

## Prohibited patterns

- hidden business rules in prompts;
- provider SDK calls from domain logic;
- broad exception catches that report success;
- indefinite retries;
- mutable global state;
- production credentials in developer environments;
- unversioned public contracts; and
- TODOs without an owner or tracking reference when they represent accepted risk.
