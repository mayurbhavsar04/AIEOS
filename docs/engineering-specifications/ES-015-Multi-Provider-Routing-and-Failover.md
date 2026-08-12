# ES-015 — Multi-Provider Routing and Failover

- **Status:** Proposed
- **Milestone:** 6, Phase 4
- **Frozen inputs:** Architecture/Domain/Runtime v1.0, ES-004–ES-014, TDR-015–TDR-024

## Outcome

The frozen AI Gateway executes one canonical request through OpenAI or Gemini without provider
knowledge in Workflow, Skill, Manager, or product code. Provider switching changes only internal
catalog state and policy. It does not change `AIInvocationRequest`, `AIInvocationResponse`,
provider-neutral stream events, Result/Error semantics, or Workflow retry ownership.

## Adapter and catalog

Gemini GenerateContent is isolated in `adapters/ai_provider_gemini`. The adapter maps:

- canonical prompt and output limits to `contents` and `generationConfig`;
- native JSON Schema as a generation hint while AIEOS validation stays authoritative;
- incremental SSE text to `content_delta`, and usage metadata to `AIUsage`;
- `STOP` to successful terminal completion; EOF alone is incomplete;
- prompt/candidate/cache/thought counts to input/output/cached/reasoning counts;
- HTTP, finish, safety, timeout, cancellation, incomplete, and ambiguity outcomes to ES-007 codes.

The internal catalog records the provider model, only the implemented `{text, structured, stream}`
capabilities, context/output limits, conservative quality/latency tiers, versioned pricing source,
global data handling, health/availability, and review policy. Volatile price values are metadata,
never business-logic constants. No provider SDK or model identifier escapes the adapter.

## Deterministic routing

Eligibility is evaluated in this order:

1. required capability;
2. hard quality tier;
3. adapter/security/data-handling/residency policy;
4. latency/deadline feasibility;
5. health, availability, cooldown, and deprecation state;
6. context and output capacity;
7. invocation budget feasibility;
8. estimated cost ranking;
9. latency tier then internal model key as deterministic tie-breaks.

The durable `RouteDecision` includes considered models, exact exclusion reasons, estimated cost,
pricing-dependent decision reference, and explanation. A cheaper ineligible provider cannot win.

## Bounded failover and budgets

One `AIInvocationId` owns an ordered internal attempt sequence. Each distinct eligible candidate is
attempted at most once, capped by `max_provider_attempts`; there is no A→B→A loop. Cache/replay is
checked before a paid dispatch. Each attempt's actual or estimated usage is recorded, prior spend is
subtracted from remaining capacity, repair spend shares the same budget, and switching providers
never resets token or cost totals. Budget exhaustion stops before another dispatch.

Provider retry classification is advisory to the Gateway attempt policy. The Workflow Engine stays
the sole owner of workflow retry. Permanent policy/capability failures do not fall through unless an
explicit policy semantics makes them provider-local and safe; current Phase 4 behavior fails closed.

## Ambiguous effects and recovery

Neither selected API documents a compatible process-independent generation dedupe key. The Gateway
persists its opaque effect reservation before dispatch and replays completed evidence, but an unknown
provider dispatch outcome is `AI_PROVIDER_EFFECT_AMBIGUOUS`. It is non-retryable inside the Gateway
and blocks cross-provider failover because another call could duplicate a billable/business effect.

Durable PostgreSQL attempt rows retain model/adapter order, state, effect reference, per-attempt
usage/cost, and completed provider results. Invocation checkpoints retain the latest route and
cumulative reconciliation. Crash recovery cannot create a second terminal Result; terminal intent
and ambiguity remain fail-closed and immutable across restart.

## Observability and security

Telemetry records abstract selected model/adapter, route explanation and exclusions, attempt number,
failure reason, provider health eligibility, per-attempt usage/cost, cumulative totals, and failover
sequence. Raw prompts/responses/provider payloads and credentials are excluded. API keys are accepted
only through explicit live configuration, never caller contracts or repository defaults. Ordinary
CI uses deterministic mock transports and requires no network or provider credential.

Gemini Developer API entries are global-only. Named residency requests exclude them. Paid/no-training
and approved-project ZDR are separate hard data-handling flags. Free-tier handling is not advertised
for internal workloads. Tenant/workspace scope remains enforced at admission, cache, reservation,
usage, attempt, and terminal persistence boundaries.

## Governed live validation

`live-multi-provider-conformance.yml` is manual, protected, exact-ref, environment-secret scoped,
five-minute bounded, and performs no uncontrolled retry. It runs tiny Gemini text, structured, and
real streaming checks with usage/cost summaries. A second job injects a deterministic non-billable
OpenAI transient before dispatch, then calls Gemini live, proving one invocation ID, A→B evidence,
cumulative governance, and a provider-neutral terminal Result without manufacturing ambiguity.

Live success is required before approval. Missing environment credentials or authorization is the
only acceptable blocker after all offline and PostgreSQL gates pass.

## Provider #3 extension

A third provider supplies another `ProviderAdapter`, internal model mappings, explicit normalization,
and conformance tests. Composition adds its adapter/catalog entries. No caller contract, workflow,
skill, manager, or product change is permitted; a provider that cannot fit this boundary triggers a
new architectural review rather than baseline mutation.

## Non-goals

Tools/function execution, vision/multimodal behavior, provider #3, embeddings/vector cache, browser
execution, UI, product prompts, deployment infrastructure, and frozen contract changes remain out of
scope.
