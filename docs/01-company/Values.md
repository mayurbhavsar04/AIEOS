---
title: Values
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-17
---

# Values

These values are decision rules for how AEOS builds and operates AI Employees. Each value is actionable: expected behaviours show what it requires, and warning signs identify conduct that should trigger correction.

## 1. Responsible autonomy

**What it means:** Autonomy is granted within explicit boundaries and expanded only when evidence shows the system can handle it safely.

**Expected behaviours:** Define permissions, approval thresholds, escalation paths, and stop conditions. Keep consequential actions reversible where practical. Use deterministic controls to enforce rules around probabilistic AI behaviour.

**Warning signs:** Treating human removal as the primary success measure; allowing an AI Employee to approve its own exceptions; or increasing access after demonstrations instead of operational evidence.

## 2. Reliability before reach

**What it means:** An AI Employee must perform its promised work consistently, detect failures, and recover safely before its scope or volume expands.

**Expected behaviours:** Define service expectations, test failure modes, monitor workflow health, design idempotent recovery where possible, and reduce repeated incidents at their cause.

**Warning signs:** Celebrating successful examples while ignoring failure rates; adding features while known critical paths remain fragile; or relying on silent manual repair.

## 3. Simplicity with purpose

**What it means:** Use the smallest clear solution that meets current validated needs and preserves necessary safety.

**Expected behaviours:** Prefer understandable workflows, explicit state, and replaceable components. Remove unused abstractions. Require a concrete near-term use case for platform work.

**Warning signs:** Designing for imagined customers; confusing configurability with value; or hiding a straightforward process behind unnecessary layers and terminology.

## 4. Evidence-based decisions

**What it means:** Decisions should follow measured outcomes, observed operations, and clearly stated assumptions.

**Expected behaviours:** Define success measures before experiments, preserve decision evidence, distinguish facts from hypotheses, and change direction when results contradict expectations.

**Warning signs:** Using output volume as a proxy for value; selecting only favourable examples; making market or revenue claims without validation; or defending sunk costs.

## 5. Transparency by design

**What it means:** People responsible for an AI Employee must be able to understand its state, actions, inputs, outputs, and limits.

**Expected behaviours:** Record meaningful workflow events and decisions, expose confidence and provenance where relevant, document constraints, and make human interventions visible.

**Warning signs:** Untraceable publication decisions; hidden manual work; dashboards without actionable context; or presenting uncertain content as established fact.

## 6. Security is a product requirement

**What it means:** Protecting credentials, data, tools, and publishing authority is part of delivering the product, not a later hardening exercise.

**Expected behaviours:** Apply least privilege, isolate secrets, validate external inputs, audit consequential actions, review third-party access, and plan for credential and provider compromise.

**Warning signs:** Shared credentials, broad permanent permissions, secrets in content or logs, unreviewed tool access, or security controls deferred until external launch.

## 7. Customer outcomes over AI output

**What it means:** The value of an AI Employee is the business outcome it produces, not the novelty or quantity of generated content.

**Expected behaviours:** Start with the user's operating problem, measure useful results and total effort, protect content quality, and prioritize work that improves the complete workflow.

**Warning signs:** Optimizing token use, generation speed, or publishing count while quality or distribution declines; shipping features without a user problem; or equating automation with value.

## 8. Ownership end to end

**What it means:** Teams own the result of the workflows they build, including operation, failures, documentation, and follow-through.

**Expected behaviours:** Assign clear owners, close feedback loops, respond to incidents, document decisions, and carry work from proposal through measured operation.

**Warning signs:** Passing failures between components or providers; treating deployment as completion; unclear on-call responsibility; or leaving recurring manual work undocumented.

## 9. Replaceability preserves control

**What it means:** Critical operations should not depend unnecessarily on one model, provider, tool, or person.

**Expected behaviours:** Keep business rules outside model prompts, define clear capability boundaries, preserve portable data, and test replacement paths when the risk justifies it.

**Warning signs:** Provider-specific behaviour embedded throughout workflows; no exit path for stored data; or abstraction work that claims portability without testing an alternative.

## 10. Disciplined execution

**What it means:** Make explicit decisions, limit work in progress, and finish the highest-value validation before expanding scope.

**Expected behaviours:** Set milestones with owners and exit criteria, surface risks early, keep scope aligned to current strategy, and review outcomes after delivery.

**Warning signs:** Repeated priority changes without evidence; parallel initiatives without ownership; ambiguous completion criteria; or platform expansion used to avoid testing the product.
