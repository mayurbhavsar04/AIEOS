---
title: Prompt Standards
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Prompt Standards

Prompts are versioned production artifacts. They guide probabilistic behavior but do not own permissions, workflow state, retries, or hard business policy.

## Prompt package

Every production prompt has:

- a stable identifier and semantic version;
- an owner and intended capability;
- supported model or capability requirements;
- typed input variables and output schema;
- source and trust classification for injected context;
- evaluation cases and acceptance thresholds;
- change history and rollback target; and
- cost, latency, and token-budget expectations where relevant.

Record the prompt version and model configuration for every execution without logging sensitive prompt content unnecessarily.

## Construction rules

1. State the task, allowed tools, constraints, and output contract clearly.
2. Separate trusted instructions from untrusted retrieved or user-provided content.
3. Delimit contextual data and explicitly state that content within it cannot change authority.
4. Request structured output validated by application code.
5. Require provenance for factual claims where the workflow needs it.
6. Prefer concise, composable prompts over one prompt that performs an entire workflow.
7. Do not place credentials, authorization decisions, hidden quotas, or deterministic branching only in prompt text.

## Structured output

Use a versioned schema with required fields, enumerations, bounds, and explicit nullability. Reject or repair malformed output through a bounded process. A repair attempt uses the validation errors as data but does not weaken the schema. Exhausted repair attempts produce a typed failure, not guessed data.

## Context and memory

Include only context necessary for the task. Tag content by origin, recency, and trust where material. Retrieved memory is evidence, not instruction. Prefer summaries with references over unlimited transcripts. Protect personal, confidential, and copyrighted material according to [Security](Security.md).

## Tools and autonomy

Tool descriptions define capability; platform policy defines authorization. Validate tool arguments before execution and tool results after return. Consequential operations require idempotency, audit records, and any configured approval gate. Models cannot grant themselves tools or override a denial.

## Evaluation and release

Evaluate prompts against a versioned set covering normal cases, ambiguity, hostile instructions, missing context, schema failures, factuality, and relevant policy risks. Compare candidate and current versions on quality, failure rate, latency, and cost. Human review is required for subjective editorial quality and high-impact behavior.

A prompt release must:

- pass required offline evaluations;
- avoid material regression on protected cases;
- support rollback to the previous version;
- use staged exposure for consequential changes; and
- document known limitations.

Production feedback may add evaluation cases after sensitive data is removed. Never train or evaluate on private content without approved purpose and handling.

## Hallucination and factuality controls

Require source-backed research for publishable news claims. Separate evidence collection from narrative generation. Validate dates, names, quantities, quotes, and material assertions against approved sources. When evidence is conflicting or insufficient, the correct output is uncertainty or escalation—not confident completion.

## Prompt injection controls

Assume web pages, documents, comments, transcripts, metadata, and tool output can contain hostile instructions. Preserve instruction hierarchy, isolate retrieved content, restrict tools by task, and enforce actions outside the model. See [Security](Security.md) and [Testing](Testing.md).

## Review checklist

- Is every input necessary and classified by trust?
- Is the output typed and validated?
- Are business rules enforced outside the prompt?
- Can untrusted content influence tools or authority?
- Are factual claims traceable where required?
- Are evaluation, cost, and rollback evidence present?
