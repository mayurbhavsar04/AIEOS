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

No runtime semantics change. Revisit for scale, compliance, data residency, cost, or GitHub availability needs.
