# Cadensy Phase 0.5 — Refined Implementation Roadmap

## Guardrails

Every PR is independently reviewable, testable, and reversible. A merge is not a cloud deployment; manual success is not a CI gate; a provider compatibility result is not a production rollout. No PR may weaken the existing deterministic constraint/decision boundary.

The actual dependency graph is deliberately a DAG, not the earlier linear story. `PR-01A` and `PR-01C` are separate P0 workstreams; Alembic and runtime evaluation have different prerequisites.

```text
PR-00 CI/release gate foundation ──────────────────────────────────────────────> PR-08 evidence-based release
PR-01A auth/guest characterization ──────────────────────────┬> PR-01B bounded guest identity ─> PR-08
PR-05A Alembic baseline ──────────────────────────────────────┘
PR-01C lifecycle/session isolation ─> PR-02 eval foundation ────────────┬> PR-04 typed adapter ─> PR-06A telemetry ─> PR-08
PR-03 Pydantic compatibility gate ────────────────────────────────────────┘
PR-05A Alembic baseline ─> PR-05B revision/idempotency ────────────────────────> PR-08
PR-05A Alembic baseline ─> PR-06B conversation-state decision ────────────────> PR-08
PR-07 optional LangGraph workflow depends on PR-05B + PR-06A + explicit product ADR.
```

Parallelism is allowed only where arrows do not connect: `PR-00` may run with `PR-01A`; `PR-01C` may run with `PR-01A` and `PR-05A`; `PR-03` is isolated and may prepare fake tests in parallel with `PR-02`, but **PR-04 requires both PR-02 and a passed PR-03 gate**. `PR-05A` may run alongside PR-03/04, but PR-01B's selected revocable guest credential, PR-05B, and persistent conversation work require it.

## PR-00 — CI and Release-Gate Foundation

**Priority: Must Have.**

| Field | Contract |
|---|---|
| Objective | Replace “manually ran once” with visible, repeatable PR/push checks while leaving cloud deployment manual and approved. |
| Preconditions | Confirm repository branch protection/Actions permissions with read-only GitHub inspection; no AWS credentials. |
| Files | `.github/workflows/build-validation.yml` and new CI workflow(s); test scripts; CI/runbook docs. No production task definition change. |
| Changes | Future triggers: `pull_request` to `main`, `push` to `main`, and retained `workflow_dispatch`. Split checks: frontend contract; frontend/trip build; backend isolated Postgres test; migration upgrade test once PR-05A exists; backend container health; eval-contract/fake-model test. Real-provider eval is manual/approved and never a required networked PR check. |
| Artifacts | JUnit/summary result, Node test output, migration log/schema check, fake-eval JSONL/report manifest, container health log, dependency/SBOM artifact if adopted. Artifacts must not contain secrets or raw private prompts. |
| Required checks design | Proposed required branch checks: `frontend-contract`, `frontend-build-trip-preview`, `backend-isolated-tests`, `backend-container-health`, `eval-contract`; add `migration-upgrade` after PR-05A. Whether GitHub branch protection actually enforces them is **Requires Verification** until inspected. |
| Bootstrap order for existing failures | The full Node characterization sweep currently has **7** failures, not the previously recorded 5: `plan-feature-characterization` (2), `session-runtime-request-identity-cutover` (1), `trip-navigation-characterization` (2), `trip-navigation-login-fact-availability` (1), and `trip-navigation-restoration-cutover` (1). PR-00 must first run and retain this job on every PR/push with its TAP artifact. It is a release-blocking red artifact until each assertion is repaired into a stable behavior/seam test; no assertion may be deleted, skipped, or weakened just to pass. After that exact sweep is green, promote it to a branch Required Check, then add the isolated backend/auth and migration checks in their dependency order. |
| Required-check startup order | 1) publish PR/push jobs and artifacts; 2) retain full frontend characterization as visible release-blocking evidence while repairing its seven failures; 3) promote green `frontend-characterization` and green `frontend-product-smoke` to required; 4) promote `backend-db-safety` before any destructive DB suite; 5) promote `backend-auth-characterization` after PR-01A; 6) add `migration-upgrade` only after PR-05A; 7) add fake-model `eval-contract` after PR-02/03. A manual success or a partial green suite never substitutes for a missing earlier gate. |
| Acceptance / rollback | A PR cannot be treated as green without all applicable checks; push `main` runs the same validation; deploy remains a separate manual, environment-protected workflow. Workflow change rollback is Git revert—no infrastructure rollback. |
| Dependencies | None. |

Current evidence is that `build-validation.yml` is `workflow_dispatch` only (`.github/workflows/build-validation.yml:1-100`); it must not be described as already PR-triggered CI.

## PR-01A — Authentication / Guest Compatibility Characterization

**Priority: Must Have, first implementation PR.**

| Field | Contract |
|---|---|
| Objective | Establish the exact dependency of guest invite flows on `DEV_ALLOW_MEMBERSHIP_HEADER`, and freeze a safe replacement contract before changing the default or any cloud environment. |
| Preconditions | Dedicated disposable Postgres database, `MOCK_AI=1`, scheduler disabled; no AWS access and no production config read/write. |
| Files | Focused tests around `backend/app/api/main.py`, auth/invite services, `shared/session-runtime`, Trip invite flows; an ADR/runbook. Production source behavior need not change in this PR. |
| Steps | Map account login, guest invite preview/join/reopen/logout, header emission, token/session facts, and cross-trip cases. Add an environment matrix for unset/0/1 header setting. Record the current unsafe behavior as a failing-to-be-fixed security characterization, not as approved behavior. |
| Tests | Isolated API tests: bearer account, valid guest invite, missing/forged membership header, wrong trip, revoked invite, fresh/reopened invite. UI contract tests ensure guest semantics do not depend on arbitrary ID injection. |
| Acceptance | A reviewed ADR identifies what proves a guest identity, which routes accept it, TTL/revocation behavior, and the transition strategy. There is no production setting change and no claim that P0-1 is resolved. |
| Rollback | Tests/docs are additive; revert the PR if its characterization is incorrect. No runtime rollback is required because behavior has not changed. |
| Dependencies | PR-00 recommended; no dependency on lifecycle work. |

## PR-01B — Bounded Guest Identity and Secure Authentication Default

**Priority: Must Have.**

| Field | Contract |
|---|---|
| Objective | Remove arbitrary membership-header authentication from normal/production paths without breaking the guest workflow proven in PR-01A. |
| Preconditions | PR-01A accepted guest identity ADR; **PR-05A Alembic baseline**; isolated tests; approved deployment transition plan. PR-01A establishes that a revocable Guest credential needs durable server-side state, rather than an arbitrary browser-stored membership ID. |
| Files | `backend/app/api/main.py`, auth/invite/session domain code, a reviewed guest-session persistence model and Alembic revision, `shared/session-runtime` only if contract changes, environment example/workflow, tests/docs. |
| Steps | Introduce bounded guest credential/session tied to invite/membership/trip with expiry/revocation; make missing configuration secure by default; production fail-fast if unsafe compatibility mode is requested; migrate local demo compatibility to an explicit local-only profile. Do not infer a general user identity from an arbitrary membership ID. |
| Tests | PR-01A matrix plus negative forged/replayed/cross-trip/expired/revoked tests; existing account and invite flows; container config checks. |
| Acceptance | With production configuration, an unauthenticated `X-Membership-Id` cannot authenticate any route; authorized guest lifecycle remains green; rollout and backout smoke plans use only authorized test identities. |
| Rollback | Revert to a previous **safe bounded-guest** version or disable affected guest entry while investigating; never use global membership-header auth as the rollback mechanism. Cloud rollout happens only after separate approval. |
| Dependencies | PR-01A, PR-05A, PR-00. |

## PR-01C — Agent Deadline and Session-Isolation Repair

**Priority: Must Have; independent of guest identity.**

| Field | Contract |
|---|---|
| Objective | Remove request-Session ownership from agent background work and make deadline/cancellation observably safe. |
| Preconditions | Isolated DB, fake providers/tools, baseline trace contract; no real provider needed. |
| Files | `backend/app/domain/chat/service.py`, new scoped read capability/session factory seam, possibly `db/session.py`, focused chat/agent tests and safe telemetry. |
| Steps | Separate HTTP deadline, local task cancellation, and provider/worker completion. Use async provider execution where possible; any `to_thread` read gets a per-thread Session. Add cancellation marker and discard late result. Keep tools read-only; mutation remains in normal API transaction. |
| Tests | Cooperative async fake; non-cooperative blocking fake; request timeout; Session ownership; late completion; no durable DB write; connection/thread metric cleanup within local client deadline. |
| Acceptance | No request-scoped Session crosses into an agent task/thread; timeout response does not apply an action; late work cannot mutate and its result is discarded; traces distinguish deadline/cancel/late-complete. This does **not** promise a remote provider received no request or stopped computing. |
| Rollback | Forward-safe fallback that skips model execution and returns a defined degraded response; if code reversion is necessary, revert the whole PR rather than reintroducing shared Session/thread behavior. No `AI_CHAT_RUNTIME` flag is assumed here. |
| Dependencies | PR-00 recommended; can proceed in parallel with PR-01A/01B. |

## PR-02 — Evaluation Foundation and Test Contract Repair

**Priority: Must Have.**

| Field | Contract |
|---|---|
| Objective | Make legacy behavior measurable and repair the seven currently stale frontend source-characterization tests without deleting the boundaries they were meant to protect. |
| Preconditions | PR-01C deadline taxonomy for agent cases; dedicated test DB policy. |
| Files | `backend/evals/` planned dataset/manifest/grader/report modules; existing agent-server harness adapters; four frontend test files; backend tests/runbook. |
| Changes / tests | Freeze synthetic fixtures, `allowed_output_kinds`, deterministic domain oracle source, tool trajectory and failure taxonomy. Refactor source regex tests to stable public-behavior/boundary tests. Test fake provider, fake tool, report schema and fixtures; never call real DeepSeek in CI. |
| Acceptance | All frontend contracts green; golden cases declare sufficient ground truth or permit clarification; report is reproducible and contains no private data. |
| Rollback / dependencies | Additive data/tests; retain legacy harness. Depends on PR-01C; PR-00 supplies CI execution. |

## PR-03 — Pydantic AI Compatibility Gate (Isolated PoC)

**Priority: Must Have before adapter development.**

| Field | Contract |
|---|---|
| Objective | Prove version-specific library/provider behavior in isolation before touching the production Chat route. |
| Preconditions | Dependency review authorization; pin a reviewed `pydantic-ai-slim[openai]` version in a reproducible lock/requirements process; no real API by default. |
| Files | isolated compatibility package/tests, dependency manifest/lock, provider capability report, no `domain/chat/service.py` wiring. |
| Fake-model gate | Verify typed function tool schema and argument validation; structured output and output retry; `UsageLimits`; malformed/unknown tool calls; timeout/cancellation mapping; fake HTTP 401/429/5xx/network-timeout mapping; trace fields. Pin exact observed APIs/version. |
| Real-DeepSeek gate | **Separate, explicit user approval only**: dedicated DB, budget cap, test credential injection, no production traffic. Verify tool calling, `thinking` behavior, structured output, per-request timeout, whole-run cancellation semantics, usage, and error payload mapping. Record Pass/Fail/Blocked—never silently waive a failure. |
| Acceptance | All fake gates pass and report exact version; adapter cannot begin until approved real-provider result is Pass or an explicit product decision keeps real provider disabled. Compatibility claims are scoped to tested model/API/settings. |
| Rollback / dependencies | Remove isolated PoC/dependency in a revert; no user route has changed. May run alongside PR-02, but PR-04 hard-depends on PR-02 and PR-03. |

## PR-04 — Typed Pydantic AI Change-Preview Adapter

**Priority: Must Have.**

| Field | Contract |
|---|---|
| Objective | Add Pydantic AI only to the no-write Chat Change Preview seam, retaining legacy runtime as default control. |
| Preconditions | PR-01C, PR-02, PR-03 all accepted. |
| Files | planned contracts/scoped reader/Pydantic adapter/runtime selector; minimal chat service wiring; tests. |
| Changes | Model can produce only `SuggestedAction`; server creates preview metadata/revision/hash; orchestrator creates deterministic classification; apply remains transactional API work. Server-only runtime selection defaults legacy. |
| Tests / acceptance | Golden mock cases, scope/privacy negatives, no-write invariant, error/deadline/usage tests; legacy response contract unchanged. The adapter is not evidence that optimistic concurrency is done. |
| Rollback | Set newly introduced server-only runtime selector to legacy; retain legacy tests and runtime. |
| Dependencies | PR-01C, PR-02, PR-03, PR-00. |

## PR-05A — Alembic Baseline and Non-destructive Schema Gate

**Priority: Must Have.**

| Field | Contract |
|---|---|
| Objective | Establish a schema migration baseline before any new persistent Guest credential, conversation, revision, or idempotency storage. |
| Preconditions | Disposable DB snapshot strategy; no production migration. |
| Files | Alembic configuration/revisions, CI migration test, local/operator docs. |
| Changes / tests | Represent current schema as a reviewed baseline; test empty DB upgrade and upgrade from a captured pre-change fixture; test downgrade only where genuinely supported. |
| Acceptance | `alembic upgrade head` is repeatable in CI, schema diff is reviewed, and migration runtime/locking/rollback plan is documented. |
| Rollback / dependencies | Revert migration before deploy; post-deploy rollback follows per-revision plan, never `drop_all`. Can proceed in parallel with PR-02–04. |

## PR-05B — Preview Revision, Idempotency and Concurrent Apply

**Priority: Must Have.**

| Field | Contract |
|---|---|
| Objective | Add explicit server-generated plan revision/snapshot and transactionally safe apply semantics. |
| Preconditions | PR-05A; product contract for stale response; current API compatibility review. |
| Files | migration, plan/change models, API request/response DTOs, decision/apply domain code, race tests. |
| Changes | Server derives preview metadata from trusted snapshot; apply accepts revision/idempotency key, compare-and-swap/reclassifies in transaction, and returns machine-readable stale/duplicate results. LLM never creates revision or decision path. |
| Tests / acceptance | duplicate/retry/race/stale tests with isolated DB; same key safe replay; stale preview never silently applies; current domain paths preserve policy. This is the PR after which optimistic concurrency may be claimed. |
| Rollback | Additive API first; retain prior clients temporarily; revert undeployed migration or follow data-preserving migration rollback plan. |
| Dependencies | PR-05A; should be coordinated with PR-04 but does not require its rollout. |

## PR-06A — Unified Safe Telemetry

**Priority: Should Have.**

| Field | Contract |
|---|---|
| Objective | Connect request→runtime→tool→domain outcome without raw private content. |
| Preconditions | PR-01C event taxonomy; PR-04 adapter events if Pydantic path is enabled. |
| Changes / acceptance | Emit model-call/late-completion/fallback events, correlate trace/request IDs, test redaction, define CloudWatch/OTel export and retention. A model-call definition without a call site is insufficient. |
| Rollback / dependencies | Disable exporter or revert event code; no product semantic change. Depends on PR-01C; PR-04 recommended. |

## PR-06B — Conversation State Product Decision

**Priority: Should Have.**

| Field | Contract |
|---|---|
| Objective | Either explicitly retain short-lived chat UX or add scoped persistent conversation with privacy/retention discipline. |
| Preconditions | Product decision, PR-05A, security review. |
| Changes / acceptance | If persistent: migration, membership/trip scope, retention/deletion, redaction and tests. If not: UI/API copy accurately state bounded history. Do not label either outcome long-term semantic memory without evidence. |
| Rollback / dependencies | Feature disable/export/delete per policy; depends on PR-05A and optionally PR-06A. |

## PR-07 — Optional Recoverable Whole-Trip Replanning (LangGraph decision)

**Priority: Optional.**

Only begin after PR-05B and PR-06A plus a product ADR proving need for durable checkpoint/resume/human review. It must keep database/domain state authoritative and demonstrate failure/resume/idempotency/privacy tests. No portfolio baseline is blocked on it.

## PR-08 — Portfolio Validation and Approved Release Evidence

**Priority: Should Have after Must Haves.**

Map claims to code/test/eval/deploy artifacts; distinguish team history, personal contribution, new work and planned work. After explicit deployment authority only, collect read-only commit→image→task-definition→health/rollback evidence. It depends on all PRs actually claimed, not on aspirational roadmap entries.

## Stop conditions

Stop and seek direction if the guest replacement requires an unreviewed authentication design, a DB cannot be shown disposable, provider PoC needs spend/credentials, migration requires destructive backfill, a cloud task source conflicts with the repository, or a requested framework weakens deterministic decision authority.
