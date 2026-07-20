# AIEOS

AIEOS (AI Employee Operating System) is a platform for building reliable autonomous AI Employees. It is intended to support specialized employees across business functions such as content operations, marketing, sales, restaurant operations, and customer support.

> **Project status:** Pre-execution / Sprint 0. The repository currently defines the company and product foundations; implementation has not started.

## The problem

Generative AI can produce useful reasoning and content, but an AI Employee must do more than generate an answer. It must pursue a business outcome repeatedly, operate within explicit permissions, validate its work, recover from failures, and make its performance understandable to people.

AIEOS addresses that gap by combining probabilistic AI capabilities with deterministic controls. AI will handle tasks such as reasoning and content generation. Software will enforce workflow state, permissions, validations, safety rules, and business constraints.

## Initial product

The first product is an autonomous AI YouTube Employee. Its first internal deployment will operate one fully AI-generated YouTube news channel, initially targeting three Shorts per day and one long-form video every alternate day.

The intended publishing workflow will become fully autonomous while maintaining strong factual verification, copyright controls, observability, and failure recovery. We will test the YouTube Employee internally before deciding how to convert the validated capability into a sellable SaaS product.

## Long-term direction

The YouTube Employee is the starting point, not the limit of AIEOS. The platform is expected to grow around reusable agents, skills, workflows, memory, tools, analytics, and provider-independent AI capabilities. Each addition must follow demonstrated product needs rather than speculative platform design.

## Repository scope

This engineering repository will serve as the source of truth for:

- company direction and product strategy;
- system architecture and engineering standards;
- API and database design;
- delivery roadmap and operating runbooks; and
- implementation as the project moves beyond Sprint 0.

## Repository roadmap

| Stage | Focus | Status |
| --- | --- | --- |
| Milestone 1 | Company foundation: vision, mission, values, and product strategy | Draft |
| Milestone 2 | Engineering handbook and operating standards | Draft |
| Milestone 3 | Architecture, ADRs, APIs, and data design | Planned |
| Milestone 4 | Delivery roadmap, runbooks, and implementation | Planned |

The sequence may change as internal operation produces evidence. Reliability and measurable outcomes take priority over completing a predetermined feature list.

## Company foundation

- [Company foundation overview](docs/01-company/README.md)
- [Vision](docs/01-company/Vision.md)
- [Mission](docs/01-company/Mission.md)
- [Values](docs/01-company/Values.md)
- [Product strategy](docs/01-company/ProductStrategy.md)

## Engineering handbook

- [Handbook overview](docs/02-engineering-handbook/README.md)
- [Engineering principles](docs/02-engineering-handbook/Principles.md)
- [Coding standards](docs/02-engineering-handbook/CodingStandards.md)
- [Prompt standards](docs/02-engineering-handbook/PromptStandards.md)
- [Git workflow](docs/02-engineering-handbook/GitWorkflow.md)
- [Security](docs/02-engineering-handbook/Security.md)
- [Testing](docs/02-engineering-handbook/Testing.md)
- [Observability](docs/02-engineering-handbook/Observability.md)
- [Deployment](docs/02-engineering-handbook/Deployment.md)
