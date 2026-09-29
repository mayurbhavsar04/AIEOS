# M7-C convergence remediation traceability

Scope: persistence remediation of the five independent findings against
`f7acaae0793d4eaf377f0ca27a97de46d9cea4ee`. This continues the retained implementation
at `47fe9283dcc5674cc90ab18ab3c112ca566d2148`; it does not reopen governance or
implement M7-D/E behavior or M7-F.

## Contract basis

The authoritative external artifacts are the 2026-09-03 M7 Executable Shared
Contract v1, Safe Command Value Domain CTO Decision, Revision-1 Final
Materialization and executable addendum, their Correction-1 successors, and
Shared Contract Agreement v1 Revision-1 Reaffirmation. Correction 1 governs
nullable `attempt_number`. Frozen M6 remains at
`governed-ai-workflow-v1.0`, `52271c4fe680c2e322201199dcd4f05a5de20379`.

Generic booleans are **excluded** by the reaffirmed safe algebra. Generic null
is allowed. Complete command evidence, rather than a fingerprint, controls
command identity. Source Result/Error digests retain their separate authority.

## Five findings and corrections

| Finding | Correction | Proof |
|---|---|---|
| Safe values accepted tuples, rejected null, and omitted bounds | Exact built-in null/int/string/list/map algebra, canonical inverse, UTF-8 key ordering, integer bounds, 32 container levels, 10,000 nodes including keys, 1,048,576 encoded bytes; typed nullable attempts | Codec tests cover accepted/rejected types, integer endpoints, aliases/cycles, exact resource limits, canonical syntax, nested determinism, attempts, and malformed versions |
| Reference evidence was incomplete and transient | Retain scope, kind/identity, owning contract/version, immutable pin, provenance/classification, purpose, and exact owner evidence; commit reference and command bindings; require owner revalidation on recovery | Reference commit/restart, missing owner, changed evidence, scope/purpose/pin/owner/version/provenance/classification failures, malformed bindings, and atomic reference rollback tests |
| Recovery reconstructed only attempt number | Explicit inverse for all 15 envelope, 10 metadata, and six authorization fields; retain original profile, path, ID/key association; reject incomplete/corrupt evidence | Complete typed roundtrip, required-field deletion and unknown-field tests, exact restart recovery, distinct Manager/Start keys, corrupted profile/path/basis tests |
| Response union was not durable | Write `response_tag` and conditional `workflow_id`, retain exact source Result/Error/proof, verify frozen Workflow receipt and Workflow record, close dispatch and preserve observation catch-up/quarantine | All four source response variants, forbidden/missing Workflow identity, duplicate/conflict/resolution, source identity collision, terminal dispatch closure, and observation confirmation tests |
| Atomic lifecycle operation and collision handling were absent | C-owned composed transaction binds admission, Manager receipt/decision, pending Start command, references where supplied, and handoff; look up both scoped ID and key for deterministic conflicts | Commit/restart, outer rollback, nested collision rollback, same-key same-command and different-command concurrency, isolation and CAS/fencing tests |

## SCB mapping

All methods below belong to `PostgresEmployeePersistence` in
`adapters/persistence_postgres/src/aieos/adapters/persistence_postgres/employee.py`.
Database tests are in `tests/integration/test_m7_postgres_remediation.py`; codec
tests are in `adapters/persistence_postgres/tests/test_m7_employee_persistence.py`.

| SCB obligation / port | Repository method | Table / constraint / transaction | Test |
|---|---|---|---|
| B admission and E pending command | `commit_admission_with_pending_command` | `employee_admissions`, receipt, command and handoff writes in one nested transaction under the outer C commit | `test_atomic_commit_restart_same_command_and_fingerprint_non_authority`, `test_outer_transaction_rollback_leaves_no_lifecycle_rows`, `test_inner_collision_rolls_back_admission_and_receipt` |
| D/E immutable scoped identity | `open_manager_receipt`, `save_start_workflow_command` | Scoped primary/unique keys, exact complete evidence and profile/path comparison | `test_manager_key_collision_is_deterministic`, `test_changed_command_conflicts_without_replacement`, `test_concurrent_same_key_different_command_collision`, `test_concurrent_same_command_converges` |
| E authoritative Manager decision | `commit_manager_decision`, `commit_manager_decision_and_pending_command` | Receipt row lock, revision/fence, decision evidence, unique scoped Start association and foreign key | `test_manager_decision_remains_distinct_from_workflow_rejection`, atomic tests |
| E exact recovery | `load_recovery` | Receipt joined by explicit Start ID; complete basis, profile/path, recovery block | `test_corrupted_recovery_never_synthesizes_a_command`, `test_unproven_response_and_terminal_dispatch_closure` |
| B/E governed references | `commit_durable_reference`, `revalidate_durable_reference`, `save_start_workflow_with_references` | `employee_durable_references` and command `reference_evidence`; compose with lifecycle transaction | `test_durable_reference_commit_restart_and_owner_revalidation`, `test_reference_fail_closed`, `test_recovery_revalidates_command_bound_references`, `test_malformed_reference_bindings_block_recovery`, `test_atomic_admission_includes_governed_reference_records` |
| E/H response capture and observation | `capture_response`, `read_response`, `confirm_employee_observation` | Existing response columns and source evidence; receipt state and observation progress updated locally | `test_response_union_persists_and_observation_catches_up`, `test_forbidden_workflow_identity_rejected`, `test_missing_workflow_proof_rejected` |
| I protected conflict and append-only resolution | `append_observation`, `capture_response`, `resolve_conflict` | Old/new bodies in `employee_observation_conflicts`, quarantine, appended resolutions, recovery block | `test_response_conflict_retains_both_evidences_and_blocks_observation`, `test_competing_command_under_same_source_result_quarantines_both` |
| G/L command evidence authority | `register_source` | Exact command payload/profile comparison in source registry; protected conflicting bodies | `test_source_registration_cannot_reintroduce_command_digest_authority` |
| J/K stale writer exclusion | `advance_administrative_head`, `commit_checkpoint` | Scoped revision/fence predicates | `test_checkpoint_admin_and_receipt_cas_fencing` |
| All touched scope boundaries | Scoped methods above | Tenant and workspace keys on all associated rows | `test_exact_tenant_and_workspace_isolation` |

## Migration and compatibility

Additive migration `20260909_0007_m7_remediation.py` follows `20260903_0006`.
It adds command profile/reference evidence, explicit Manager-to-Start association,
decision/observation/recovery-block evidence, reference storage, and protected
conflicting observation bodies. It adds scoped execution/receipt uniqueness and
receipt/Start uniqueness plus a foreign key. Existing `response_tag` and
`workflow_id` columns are reused. Readiness and test schema inventory track the
new head.

Historical migrations, frozen M6 command/runtime behavior and replay hashes are
unchanged. Incomplete historical evidence is rejected; no command, source path,
reference, default or Workflow identity is synthesized. No new business reference
consumer or project schema is introduced. Current access/retention/integrity
remain the reference owner's responsibility, rechecked through its registered
validator before recovery dispatch.

## Validation gate

Run `scripts/check`, the focused codec tests, and the mandatory hosted
`pytest -m postgres_required` suite against PostgreSQL 17.5. Local database skips
are not durability proof. The final delivery report records the exact candidate
SHA, hosted run and counts. The earlier candidate's hosted run
[34601722289, attempt 2](https://github.com/mayurbhavsar04/AIEOS/actions/runs/34601722289/attempts/2)
passed 486 workspace and 127 mandatory durability tests; that result does not
certify subsequent changes.

No semantic/governance ambiguity has been identified in these corrections.
Repeat independent Terra validation is the next acceptance gate; this developer
traceability record is not independent approval of M7 convergence.
