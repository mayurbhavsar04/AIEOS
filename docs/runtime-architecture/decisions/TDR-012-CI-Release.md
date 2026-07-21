# TDR-012 — GitHub Actions, Locked Builds, and Protected Releases

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

The GitHub source of truth needs reproducible validation, branch protection, contract compatibility, artifact provenance, and low initial operating burden.

## Options

GitHub Actions; external CI SaaS; self-hosted CI; local-only scripts.

## Decision and rationale

Use repository scripts as the canonical interface and GitHub Actions as the initial runner. Pin actions and tools, use least privilege, protected environments, immutable artifacts, and gated tags/releases.

## Consequences and rejected alternatives

Workflow security and hosted-minute limits require monitoring. Scripts remain portable to avoid lock-in. External/self-hosted CI adds administration now; local-only checks are insufficient.

## Compatibility and revisit

No runtime semantics change. Revisit when CI duration/cost misses an adopted objective for a sustained review window, compliance or data-residency requirements prohibit the runner model, or availability incidents prevent required release gates. Developer-experience and security owners evaluate run history, cost, incidents, and compliance evidence; migration preserves repository scripts, locked builds, least privilege, provenance, and protected releases.
