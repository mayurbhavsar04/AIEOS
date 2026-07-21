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

Does not change service ownership. Revisit only if composition complexity remains high after modular factories and generated graph validation.
