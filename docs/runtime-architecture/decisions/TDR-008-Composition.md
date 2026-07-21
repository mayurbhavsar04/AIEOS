# TDR-008 — Explicit Constructor Injection and Composition Root

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

Runtime modules need visible dependencies, deterministic substitution, startup validation, and no framework coupling or global state.

## Options

Explicit constructors/factories; dependency-injection container; service locator; module globals.

## Decision and rationale

Use typed constructors and factories, wired only in `apps/api/composition.py`. Lifecycle ordering is explicit. No runtime reflection container is selected.

## Consequences and rejected alternatives

Composition code is verbose but reviewable. Containers hide graphs and add magic; service locators and globals violate dependency clarity and test isolation.

## Compatibility and revisit

Does not change service ownership. Revisit when the validated composition graph exceeds an adopted maintainability threshold (for example repeated wiring defects across releases), startup graph construction misses its objective, or modular factories cannot represent a required approved lifecycle. The runtime owner evaluates defect and startup data; any replacement preserves explicit dependencies, one composition root, deterministic substitution, and no service locator.
