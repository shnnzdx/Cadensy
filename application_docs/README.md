# Cadensy Application Documentation Index

Status: current application documentation map
Last reviewed: 2026-11-23

This directory contains application product contracts, active limitations, handoff guidance, architecture decisions, task delivery evidence, and scoped investigations. It is not the home for AWS operations or AI-agent engineering evidence: those remain in [AWS README](../AWS/README.md) and [AI engineering evidence](../AI_enhanced/README.md).

## Start here

| Need | Current source |
| --- | --- |
| Repository setup and architecture | [Repository README](../README.md) |
| Current operational handoff | [HANDOFF](../HANDOFF.md) |
| Integration status and next work | [Integration roadmap](../INTEGRATION-ROADMAP.md) |
| Product behavior and decision rules | [PRODUCT.md](PRODUCT.md) |
| Activation funnel events and metrics | [activation-funnel.md](activation-funnel.md) |
| Project description | [PROJECT_DESCRIPTION.md](PROJECT_DESCRIPTION.md) |
| Known navigation limitations | [navigation-known-wrong-behavior.md](navigation-known-wrong-behavior.md) |
| Trip-independent routes and unused `X-Trip-Id` behavior | [trip-independent-x-trip-id.md](trip-independent-x-trip-id.md) |
| General continuation prompt | [HANDOFF_PROMPT_CURRENT.md](HANDOFF_PROMPT_CURRENT.md) |
| Backend-refactor continuation prompt | [HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md](HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md) |
| Documentation-specific agent rules | [AGENTS.md](AGENTS.md) |
| Architecture decisions and trade-offs | [decisions/README.md](decisions/README.md) |
| Completed task, PR, QA and release evidence | [delivery-records/README.md](delivery-records/README.md) |
| Dated technical investigations and audit evidence | [investigations/README.md](investigations/README.md) |
| Historical Backend handoffs | [backend/README.md](backend/README.md) |
| Historical Frontend handoffs | [frontend/README.md](frontend/README.md) |
| Superseded plans and materials | [_archive/README.md](_archive/README.md) |

## Documentation domains

### Current contracts and active guidance — application_docs root

The root contains documents that current contributors may need as active product or operational guidance:

- `PRODUCT.md` — product behavior and decision contract.
- `activation-funnel.md` — event schema, metric definitions and privacy constraints.
- `navigation-known-wrong-behavior.md` — current known limitations, not desired policy.
- `trip-independent-x-trip-id.md` — scoped behavior note with a pinned code baseline.
- `PROJECT_DESCRIPTION.md` — product overview and positioning.
- `HANDOFF_PROMPT_*.md` — current continuation prompts.
- `AGENTS.md` — documentation-specific working rules.

Keep this root small. Do not put dated task reports, investigations, or architecture decision logs here.

### Decisions — `decisions/`

Records why a meaningful product, architecture, security, cost or operating direction was selected, what alternatives were considered, what was deferred, and what evidence would cause reconsideration. These are point-in-time decision records, not proof of implementation. Start at [decisions/README.md](decisions/README.md).

### Delivery records — `delivery-records/`

Preserves what happened during a bounded task: PRs, merges, QA, artifact publication, migrations, deployments and verification. Keep these evidence levels separate. A merge is not a deployment; an artifact publication is not a production release; a reported release is not independent verification. Start at [delivery-records/README.md](delivery-records/README.md).

### Investigations — `investigations/`

Preserves dated, scoped technical research, audits, caller searches, debugging and coverage reviews. Each record should state its code pin, search scope, findings and residual limitations. An investigation is not automatically runtime proof or QA. Start at [investigations/README.md](investigations/README.md).

### Historical area records — `backend/` and `frontend/`

The dated Backend and Frontend handoffs remain at their original paths because older documents and external handoffs may link to them. The directory-level READMEs mark them historical and point to current source-of-truth documents. Do not bulk-move these files just to make the tree look uniform.

### Superseded material — `_archive/`

Contains replaced proposals, temporary deployment assets, obsolete handoffs and historical release material. Retained for traceability; not current implementation guidance.

## Evidence lifecycle

- **Current contracts:** update in the same change that makes a statement stale.
- **Decision records:** preserve the decision context and assumptions. Do not rewrite a historical decision to match a later implementation; add a follow-up decision or retrospective.
- **Delivery records:** preserve exact PRs, commits, workflow runs, evidence level and unknowns. Do not promote reported evidence to verified evidence.
- **Investigations:** preserve the scope and limitations of the search. A negative search result is not proof outside the inspected scope.
- **Historical area records:** `backend/` and `frontend/` are point-in-time handoffs; current source and current contracts take precedence.
- **Archived material:** `_archive/` is retained for history, not active guidance.

## Maintenance rules

1. Label status claims with dates and distinguish design intent, implementation, automated test evidence, protected deployment evidence and live production observation.
2. Do not call a local default a production state. The backend local default may remain `legacy` while recorded production runs `pydantic`.
3. Do not infer live AWS state from an old delivery record. Use current protected-run evidence or explicitly perform a new read-only verification.
4. Put new decisions, completed-task evidence and technical investigations in their respective directories, not the application_docs root.
5. Preserve stable paths for heavily cross-referenced historical material; verify all relative links after any move.
6. Never store credentials, provider keys, access tokens, or unredacted user content in documentation or evidence.
