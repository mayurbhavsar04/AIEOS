# TDR-023 — OpenAI as the First Real AI Provider Adapter

- **Status:** Proposed
- **Date:** 2026-08-11

## Context and comparison

Milestone 6 Phase 3 needs one real adapter behind the frozen AI Gateway. Selection considered
OpenAI, Anthropic, and Google Gemini using only official API, pricing, SDK, privacy, and model
documentation current on the decision date. This selects implementation order, not a permanent or
exclusive provider.

| Criterion | OpenAI | Anthropic | Google Gemini |
| --- | --- | --- | --- |
| Capability tiers | Responses API; economical through frontier reasoning models | Messages API; Haiku through Opus | GenerateContent/Interactions; Flash through Pro |
| Structured output | Native strict JSON Schema | Native schema output | JSON Schema subset and streaming partial JSON |
| Streaming/tools | Typed incremental events and function calling | Typed message events and tool use | Incremental events, function calling, terminal usage |
| Usage | Input/output, cached-input, exposed reasoning detail | Input/output and cache write/read detail | Prompt/candidate/cache/thought detail by API/model |
| Replay key | No compatible documented generation replay key | No compatible documented generation replay key | No compatible documented generation replay key |
| Python/async | Official async/streaming client | Official async/streaming client | Official `google-genai` async/streaming client |
| Privacy/region | API controls, eligible ZDR/regional options subject to account/feature | API controls; cloud-platform regional options | Approved-project ZDR; separate Vertex AI regional controls |
| Stability/multimodal | Snapshots; broad text/image/tool support | Versioned IDs; text/image/tool support | Versioned IDs; broad native multimodal support |
| Cheapest cited capable tier | GPT-5 mini: $0.25 input / $2 output per MTok | Haiku 4.5: $1 input / $5 output per MTok | Flash pricing varies by model/tier |

Quality is not inferred from popularity. AIEOS must run representative evaluations before assigning
higher production quality tiers.

## Decision

Select OpenAI Responses API as the **first** real reference adapter. Native schema format,
incremental events, usage breakdown, snapshot versioning, multimodal/tool compatibility, and a
low-cost capable snapshot map cleanly to current AIEOS abstractions. Direct HTTP inside the adapter
keeps SDK objects and dependency churn behind the boundary; the official SDK remains an internal
replacement option.

Callers see only `economy-text-v1`; the provider snapshot exists only in the adapter catalog.
Pricing is versioned metadata, not business logic. Mock adapters remain default and live calls need
explicit configuration.

No compatible provider-side idempotency key is documented. The adapter therefore uses the frozen
durable opaque-effect boundary. Completed evidence can replay; an unknown dispatch fails closed as
`AI_PROVIDER_EFFECT_AMBIGUOUS`. AIEOS does not claim exactly-once after an unknown outcome.

## Reversibility and review triggers

A second adapter can implement the same port and catalog without changing callers, Results/Errors,
budgets, or workflow retry authority. Review when the snapshot is deprecated/repriced; measured
quality, p95 latency, availability, or cost misses an objective; a provider offers verifiable replay;
privacy/residency/regulation excludes OpenAI; two adapters expose a neutral-contract gap; an official
SDK materially reduces protocol risk; or an approved multimodal/tool need fits another provider.

## Official sources

- [OpenAI models and price/capability metadata](https://developers.openai.com/api/docs/models)
- [OpenAI model comparison](https://developers.openai.com/api/docs/models/compare)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)
- [Anthropic API overview](https://platform.claude.com/docs/en/api/overview)
- [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Anthropic privacy](https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-personal-data)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini streaming](https://ai.google.dev/gemini-api/docs/streaming)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini Zero Data Retention](https://ai.google.dev/gemini-api/docs/zdr)
