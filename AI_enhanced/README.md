# Cadensy AI Engineering Evidence Index

This directory is organized by implementation phase. Historical filenames and report numbers are preserved so that the evidence remains traceable to its original review or pull request.

## Current release status — 2026-10-27

Cadensy v0.3.0 is released at
[`v0.3.0`](https://github.com/shnnzdx/Cadensy/releases/tag/v0.3.0), targeting
main commit `ea7c9e3c8c4da3c4a518e47ffc528df5ed199233`.

The production backend is running the Pydantic runtime on
`tripsync-backend:31` with `CHAT_AGENT_RUNTIME=pydantic` and the Durable
Conversation production latch enabled. The protected CD-03 activation record is
[run 40382252075](https://github.com/shnnzdx/Cadensy/actions/runs/40382252075).
The separately released frontend is `tripsync-frontend:19`.

Reports below are point-in-time engineering evidence. Statements such as
“not authorized”, “not merged”, or “Legacy active” describe the state when the
particular report was written; they are not the current production status.

| Area | Contents |
| --- | --- |
| [Phase 0 audit and roadmap](00_Phase-0_Audit-and-Roadmap/) | Initial architecture audit, gap analysis, roadmap, and executive summary. |
| [PR-00 — CI foundation](PR-00_CI-Foundation/) | CI foundation, hardening, remote verification, and required-check evidence. |
| [PR-01 — Auth and lifecycle](PR-01_Auth-Lifecycle/) | Guest/authentication, lifecycle isolation, and related ADR. |
| [PR-02 — Evaluation](PR-02_Evaluation/) | Evaluation foundation and regression remediation evidence. |
| [PR-03 / PR-03R — Pydantic and DeepSeek compatibility](PR-03_Pydantic-DeepSeek-Compatibility/) | Framework evaluation, compatibility PoC, and provider-smoke evidence. |
| [PR-04 — Runtime adapter and A/B](PR-04_Runtime-Adapter-and-AB/) | Runtime-contract work, symmetric A/B evaluation, and its result artifact. |
| [PR-04E — Controlled integration](PR-04E_Controlled-Integration/) | Controlled Pydantic application-composition design and implementation. |
| [PR-05A — Alembic baseline](PR-05A_Alembic-Baseline/) | Alembic baseline report. |
| [PR-05B — Revision and idempotency](PR-05B_Revision-and-Idempotency/) | Revision, durable idempotency, transaction kernel, writer coverage, generator, and R5 acceptance evidence. |
| [PR-06A — Safe telemetry](PR-06A_Unified-Safe-Telemetry/) | Unified privacy-safe telemetry contracts and verification evidence. |
| [PR-06B — Secure persistent conversations](PR-06B_Secure-Persistent-Conversation-State/) | Membership lifecycle, durable conversations, server-owned context, browser acceptance, production release, and closeout evidence. |
| [PR-07 — Real AI runtime activation](PR-07_Real-AI-Runtime-Activation/) | Stage 1–4B audit, Legacy/DeepSeek activation preparation, real-provider validation, Direct Candidate persistence fix, Chromium acceptance, RC assembly, and CD-01 release-safety contracts. |
| [PR-08 — Controlled DeepSeek production activation](PR-08_Controlled-DeepSeek-Production-Activation/) | Protected Release B activation, bounded real-provider acceptance, and Legacy production closeout evidence. |
| [PR-09 — Pydantic production migration](PR-09_Pydantic-Production-Migration/) | Trusted execution evidence, functional parity, durable lifecycle/concurrency acceptance, formal evaluation, protected Pydantic activation, and v0.3.0 migration evidence. |

The folders are an archival/navigation organization only; they do not alter the historical conclusions inside the reports. When a report contains a link to another report, its path has been updated where needed for this layout.
