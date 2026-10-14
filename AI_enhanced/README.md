# Cadensy AI Engineering Evidence Index

This directory is organized by implementation phase. Historical filenames and report numbers are preserved so that the evidence remains traceable to its original review or pull request.

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

The folders are an archival/navigation organization only; they do not alter the historical conclusions inside the reports. When a report contains a link to another report, its path has been updated where needed for this layout.
