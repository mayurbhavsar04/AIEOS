# TDR-024 — Gemini as the Second Provider and Bounded Cross-Provider Failover

- **Status:** Proposed
- **Date:** 2026-08-12

## Context and official-source comparison

Phase 4 must prove provider neutrality with a second real adapter, not establish a permanent
preferred vendor. Anthropic and Google Gemini were compared against the existing OpenAI adapter
using official sources current on the decision date. Quality tiers remain conservative until AIEOS
has representative evaluations; marketing claims and popularity are not selection evidence.

| Criterion | OpenAI (integrated baseline) | Anthropic | Google Gemini |
| --- | --- | --- | --- |
| Phase 4 parity | Responses text, strict schema, typed SSE | Messages text/SSE; schema-constrained output | GenerateContent text, JSON Schema subset, incremental SSE |
| Usage detail | Input/output, cached input, reasoning detail | Input/output, cache creation/read | Prompt/candidate, cached-content, thoughts |
| Future tools/multimodal | Tools and image inputs available | Tool use and vision available | Function calling and broad native multimodal available |
| Python/async | Official async SDK; direct HTTP adapter today | Official async SDK | Official `google-genai` async SDK; direct HTTP is stable and small |
| Errors/cancellation | HTTP plus typed terminal response events | HTTP plus typed message events | Google RPC-style HTTP errors; explicit finish reasons; client cancellation |
| Model stability | Snapshot IDs available | Versioned model IDs available | Stable aliases and model metadata; aliases require review discipline |
| Idempotent generation | No compatible replay key documented | No compatible replay key documented | No compatible replay key documented |
| Data use/retention | API controls; ZDR eligibility varies by feature/account | Commercial API privacy controls; cloud paths for regional needs | Paid content not used for improvement; approved-project ZDR has exclusions |
| Residency | Account/feature-dependent regional controls | Cloud-platform regional options | Developer API is global; Vertex AI is required for explicit regional control |
| Cost complement | GPT-5 mini metadata remains auditable | Haiku is a capable alternative | Flash-Lite creates a distinct low-cost capable tier with explicit cache/thought usage |

Anthropic is a valid future provider and may be preferable where its measured quality, privacy
terms, regional delivery path, or versioned model behavior wins. Gemini best complements OpenAI for
this phase because its native schema mode, explicit finish reasons, usage detail, real incremental
streaming, and cost profile exercise more of the already-frozen neutral abstractions.

## Decision

Select **Google Gemini Developer API** as provider #2, implemented as
`gemini-generate-content` strictly behind `ProviderAdapter`. Callers use the internal
`economy-text-gemini-v1` key; `gemini-3.5-flash-lite`, credentials, HTTP payloads, finish reasons,
and Google error types remain adapter-private. Only text, structured output, and streaming are
advertised. Tools and multimodal capabilities are deliberately not advertised in Phase 4.

This choice is reversible and non-exclusive. Neither Gemini nor OpenAI is permanently preferred.
The Gateway first filters hard capability, quality, policy/security/residency, latency/health,
capacity, and budget constraints; it then ranks eligible models by estimated cost with a stable
tie-break. Catalog state can therefore route the same canonical request through either adapter
without caller changes.

Gemini does not document a generation request key that proves process-independent deduplication.
The opaque AIEOS effect key is retained for durable local replay evidence but is not misrepresented
as provider exactly-once. A provider-confirmed transient response may fail over. Transport
uncertainty after possible dispatch fails closed as `AI_PROVIDER_EFFECT_AMBIGUOUS`; no second
provider is called. Provider attempts stay internal, share one `AIInvocationId`, consume one
cumulative budget, are bounded by `max_provider_attempts`, and never transfer workflow retry
ownership away from the Workflow Engine.

## Health, privacy, and operational limits

Health is Gateway-owned catalog state, not a new component. `healthy`, `available`, and
`deprecated` fields represent healthy/degraded routing eligibility, explicit unavailability,
cooldown/rate-limit exclusion, model removal, and deprecation. A degraded but allowed model can be
given a higher latency/cost tier through replaceable catalog configuration; unavailable/cooldown
models are excluded before price ranking.

The Developer API catalog advertises only global/`any` residency. Requests requiring a named
region cannot use it. A future Vertex AI transport may add verified regional entries behind the
same adapter boundary. Paid service/no-training and ZDR eligibility are distinct policy flags:
free-tier data handling is not acceptable for internal data, and ZDR must not be claimed unless the
project is approved and the requested features are compatible. Raw prompts, responses, and provider
payloads are not logged by default; diagnostics are allow-listed.

## Objective revisit triggers

Revisit this decision when any of these occurs:

1. Gemini 3.5 Flash-Lite is deprecated, materially repriced, or changes schema/stream semantics.
2. Representative AIEOS evaluations change its required quality tier.
3. p95 latency, availability, error rate, or cost misses an approved objective.
4. residency, retention, regulation, tenant policy, or ZDR eligibility excludes the Developer API.
5. Anthropic or another provider offers materially better measured capability/cost/privacy.
6. a provider documents a compatible process-independent generation replay key.
7. a third provider reveals a genuine neutral-contract gap.
8. tools, vision, audio, or regional Vertex AI become approved scope.

## Official sources

- [Gemini models](https://ai.google.dev/gemini-api/docs/models)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini streaming](https://ai.google.dev/gemini-api/docs/streaming)
- [Gemini API reference](https://ai.google.dev/api/generate-content)
- [Gemini token counting](https://ai.google.dev/gemini-api/docs/tokens)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini troubleshooting and retry semantics](https://ai.google.dev/gemini-api/docs/troubleshooting)
- [Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key)
- [Gemini Zero Data Retention](https://ai.google.dev/gemini-api/docs/zdr)
- [Gemini API terms and paid-service data use](https://ai.google.dev/gemini-api/terms)
- [Gemini available regions](https://ai.google.dev/gemini-api/docs/available-regions)
- [Vertex AI locations](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations)
- [Anthropic API overview](https://platform.claude.com/docs/en/api/overview)
- [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic errors](https://platform.claude.com/docs/en/api/errors)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Anthropic data retention](https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-personal-data)
