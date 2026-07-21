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

Capability Registry resolves; Skill Runtime executes. Revisit only when an approved third-party distribution requirement introduces code outside the trusted deployment boundary, a security assessment requires process isolation, or an extension requires independently governed deployment. Architecture and security owners evaluate the approved use case and threat model through ADR/security review; migration preserves registry resolution, runtime execution ownership, version/permission validation, and deny-by-default loading.
