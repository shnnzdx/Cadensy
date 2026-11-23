# Architecture and Product Decisions

Status: current index
Last reviewed: 2026-11-23

This directory preserves meaningful product, architecture, security, cost and operating decisions: why a direction was selected, which alternatives were considered, what trade-offs were accepted, what remains unresolved, and what evidence should trigger reconsideration.

Decision records are point-in-time history. They are not implementation specifications unless explicitly stated, and they do not authorize code merge, artifact publication, database migration, paid services or production changes.

## Records

| Record | Scope | State recorded |
| --- | --- | --- |
| [2026-11-23 — Governed Real-Time Collaboration](2026-11-23-governed-realtime-collaboration-decision-log.md) | Business rationale for governed shared editing; SSE vs polling/WebSocket/managed services; PostgreSQL LISTEN/NOTIFY vs Redis; concurrency, privacy, cost and rollout trade-offs | Design direction selected for investigation; not implemented or production-authorized |

## Record a new decision

Create a dated Markdown record when a decision materially changes product behavior, architecture, security/privacy boundaries, cost, operational ownership or release strategy. Include:

- status and date, decision owner(s), and code/evidence baseline;
- problem and intended user/business outcome;
- options considered, benefits, drawbacks and rejected/deferred alternatives;
- chosen direction and why it wins under current constraints;
- costs, risks, unknowns and assumptions;
- implementation/QA/production gates that remain separate from the decision;
- revisit triggers and retrospective questions.

Do not rewrite an old record to make it agree with later reality. Add a dated follow-up or retrospective and link it here.
