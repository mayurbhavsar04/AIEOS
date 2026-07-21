# TDR-013 — Trusted Static Plugin and Capability Registration

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

Future skills/capabilities need extension without changing frozen boundaries, while remote dynamic code creates major authority and supply-chain risk.

## Options

Explicit local registration; Python entry points; signed static manifests; remote dynamic code; separate extension processes.

## Decision and rationale

V1 loads only trusted, installed, allowlisted packages through explicit composition and optional signed static manifests. Registration validates identities, versions, permissions, required capabilities, and conformance tests. No remote code download/evaluation.

## Consequences and rejected alternatives

Adding extensions requires deployment, which is acceptable initially. Entry-point discovery may be added behind allowlisting. Remote loading and process plugins are unjustified complexity/risk.

## Compatibility and revisit

Capability Registry resolves; Skill Runtime executes. Revisit for third-party marketplace, untrusted code, independent deployment, or stronger isolation requirements through ADR/security review.
