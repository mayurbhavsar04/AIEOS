---
title: Git Workflow
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Git Workflow

GitHub is the engineering source of truth. Discussion becomes binding when reflected in reviewed repository artifacts.

## Branches

`main` is protected and releasable. Work occurs on short-lived branches created from the current target branch:

- `docs/<scope>` for documentation;
- `feature/<scope>` for product behavior;
- `fix/<scope>` for defects;
- `chore/<scope>` for maintenance; and
- `hotfix/<scope>` for urgent production recovery.

Stacked work may target an unmerged prerequisite branch, but the pull request must state the dependency. Delete branches after merge. A long-lived `develop` branch is not required unless an ADR establishes it.

## Commits

Each commit should represent one coherent change and leave the branch understandable. Use an imperative summary, optionally following Conventional Commits:

```text
docs: define engineering handbook
feat: persist workflow checkpoints
fix: prevent duplicate publication retries
```

Do not commit secrets, generated credentials, dependency caches, or unrelated formatting. Preserve history; do not rewrite a shared branch without coordination.

## Pull requests

Open a draft pull request early for substantial work. The description includes:

- the outcome and reason;
- scope and explicit non-scope;
- linked requirement, issue, or ADR;
- security, data, migration, cost, and operational impact;
- validation performed and notable results;
- rollout and rollback approach; and
- assumptions, limitations, and follow-up work.

Keep diffs focused. AI-generated changes must be disclosed only when organizational policy requires it, but they always receive human-accountable review and the same quality gates.

## Review and approval

Authors self-review the final diff before requesting review. Reviewers evaluate correctness, boundaries, security, tests, observability, operability, and unnecessary complexity. Comments distinguish blocking issues from suggestions.

The author resolves feedback with code, documentation, or a reasoned response. Material architectural changes require an accepted ADR. Authors do not approve their own protected changes. Required checks and approvals must pass before merge.

## Merge and release history

Prefer squash merge for a focused pull request unless preserving individual commits materially helps operation or review. The final title should describe the shipped change. Release tags use semantic versioning once releases begin; documentation milestones may use annotated tags such as `docs-v1.0` when approved.

## Hotfixes and reverts

A hotfix starts from the production branch, is narrowly scoped, and follows expedited but explicit review. Restore safety first, then add regression coverage and a follow-up analysis. Prefer a revert when a release causes unknown or broad harm. Do not repair production only by editing live state without capturing the change in Git.

## Repository hygiene

- Require reviews and status checks on protected branches.
- Use CODEOWNERS when ownership becomes stable.
- Reference issues for accepted debt and follow-up tasks.
- Never include secrets in commits; if exposed, rotate them even if history is rewritten.
- Sign commits or releases when the deployment model requires provenance.

See [Testing](Testing.md), [Deployment](Deployment.md), and [Security](Security.md).
