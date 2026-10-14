# Cadensy R5 — B.3 Revision-Bracketed Preview / Apply

## 1. Executive Summary and Verdict

**B.3 — CONDITIONAL PASS.** The default-off, non-production Preview/Apply
vertical slice is implemented and its focused PostgreSQL, protected-writer,
and isolated-Pydantic tests pass.  A full Legacy regression run is not green:
it reproducibly fails an existing `test_upsert_fixed_accounts_is_idempotent`
test even when the new B.3 test file is excluded.  No unrelated test or seed
code was changed in this phase, so B.3 must not be marked PASS until that
pre-existing full-suite isolation failure is independently resolved.

## 2. Verified B.2E Entry Gate

Report 61's final section records **B.2E UNIFIED — PASS** and **B.3
IMPLEMENTATION READINESS — READY FOR DEFAULT-OFF NON-PRODUCTION
IMPLEMENTATION**.  B.3 code was started only after that finding was checked.

## 3. Exact Source and Frozen Contract References

- Frozen read lifecycle: Report 43 §5 and Report 49; capture `r0`, bounded
  independent reads, deterministic classification, then verify `r1 == r0`.
- Aggregate versions: `backend/app/db/models.py:111-141` (`Trip.planning_facts_revision`) and `:250-260` (`Plan.revision`).
- PR-04 runtime interfaces: `backend/app/agents/runtime_contract.py:63-93`.
- Existing authoritative protected submit operation:
  `backend/app/domain/transactions/change_proposal.py:42,92-184`.
- Canonical durable root/replay protocol:
  `backend/app/domain/transactions/kernel.py:209-311` and
  `backend/app/domain/transactions/durable.py:291-415`.

## 4. Changed File Inventory

- `backend/app/domain/preview_apply.py` — new B.3 read capability and Preview orchestration.
- `backend/app/domain/chat/service.py` — primitive-only B.3 Chat adapter.
- `backend/app/agents/legacy_runtime.py` — preserves private Legacy replacement provenance for a compatible alternate capability.
- `backend/app/api/main.py` — default-off B.3 effective-profile gate and Chat/Apply selection.
- `backend/tests/test_b3_revision_bracketed_preview.py` — PostgreSQL and HTTP evidence.
- This report.

No migration, schema, HMAC envelope, UUID namespace, deterministic classifier,
frontend, provider, or production configuration was changed.

## 5. Existing Chat/Runtime/Decision Call Graph

Legacy Chat remains `chat_with_trip` → `respond_to_trip_chat`
(`backend/app/api/main.py:1154`, `backend/app/domain/chat/service.py:119`) →
selected PR-04 Runtime → read-only tools → `orch.classify_change`.  It remains
the default when B.3 is off.

Protected Apply remains `submit_change` → `_protected_submit_change`
(`backend/app/api/main.py:1884,1942`) →
`execute_protected_change_proposal` → `orch.propose_change`.  That is the
existing authoritative NOTICE/ROUND/REOPEN_ROUND/CONFIRM state machine.

## 6. B.3 Preview/Apply Integration Choice

No separate public Apply operation was added.  A confirmed Preview has exactly
the semantics of existing change submission, so B.3 composes
`plan.change.submit.v1` rather than inventing a new Ledger/HMAC identity.

The existing `/api/trips/{trip_id}/chat` endpoint becomes a B.3 Preview only
when `CADENSY_B3_PROTECTED_PREVIEW_APPLY=1` and the complete effective protected
profile is admitted.  Existing `/api/plans/items/{item_id}/changes` is the
Apply endpoint under the same admission.  Both routes preserve legacy behavior
when B.3 is off.

## 7. Revision-Bracketed Read Capability Design

`RevisionBracketedReadTripCapability`
(`backend/app/domain/preview_apply.py:127-247`) implements the existing
`ReadTripCapability` shape.  It exposes existing Legacy-shaped read tools, but
builds descriptions in a short read transaction and opens a new short Session
for every actual tool invocation.  PostgreSQL read transactions execute
`SET TRANSACTION READ ONLY`; models receive no Session, ORM entity, SQL string,
or write tool.

## 8. Preview Capture and Final Verification

`RevisionBracketedPreview.preview` (`:267-331`) captures exactly one
Trip/Plan/membership scope, rejects missing or duplicate Plans, and retains
only scalar IDs plus `(Plan.revision, Trip.planning_facts_revision)`.  Every
tool and classifier read verifies that pair.  Classification occurs in a new
bounded Session (`:333-374`), followed by a separate final verification
(`:375-377`).  Any mismatch raises `StalePreview`; no candidate/verdict is
returned.

## 9. Legacy Runtime Compatibility

The Legacy Runtime still consumes `LegacyRuntimeReadTool`; no tool surface or
timeout contract changed.  Its result recorder now uses the private recorder
hook when present (`backend/app/agents/legacy_runtime.py:228-231`).  This keeps
Legacy replacement provenance available without adding it to RuntimeResult.
`_replacement_provenance_allows` (`preview_apply.py:481-510`) fails closed for
replacement fields lacking a matching trusted `find_replacement_place` result.

## 10. Pydantic Runtime Compatibility

The capability implements the framework-neutral `RuntimeReadTool` interface,
so the Pydantic adapter receives the same read-only descriptors.  It gains no
write authority or replacement capability.  The isolated fake Pydantic suite
passed (119 tests; command in §27).

## 11. Deterministic Classification Boundary

Agent output is only an `AgentSuggestedChange` syntax proposal.  B.3 filters
its allowed patch fields and calls `orch.classify_change` fresh against the
captured authoritative Plan (`preview_apply.py:333-374`).  Apply invokes the
existing locked `orch.propose_change`; it never trusts a client preview path or
`allowed` flag.

## 12. Apply Authorization and Lock Order

Apply extracts only `PreliminaryActorScope` primitives
(`backend/app/api/main.py:269-278`), closes the request Session in the B.3
route, and calls the existing durable kernel.  The kernel locks
`Trip → credential → membership → Plan → Ledger → children`, freshly validates
the bearer and membership, then runs the writer (`kernel.py:209-311`).

## 13. Plan/Trip Revision Ownership

Preview reads both version tokens.  Existing Plan-bound Change Submission owns
the Plan revision and increments it exactly once for a state-changing command
(`durable.py:329-372`).  It does not alter Trip planning-facts revision; that
remains owned by the accepted Trip-bound writers.  A changed value of either
token invalidates Preview.

## 14. Durable Command Identity and Replay

Apply reuses the frozen `plan.change.submit.v1` code (`change_proposal.py:42`),
resource kind `plan_item`, server-validated item ID, current expected Plan
revision, canonical command fields, and existing HMAC version.  No B.3 Ledger
schema or new identity was introduced.  Replay is authenticated and checked
before new-execution stale revision rejection (`durable.py:415-481`).

## 15. No-op, Conflict and Stale Semantics

The existing durable no-op guard and Change Proposal conflict policy remain
authoritative.  Preview has no durable write/no-op result.  Revision changes
produce `StalePreview`; Apply stale revision/key conflict is handled by the
existing controlled protected-change mapping.  A client cannot turn stale into
success by resubmitting a Preview verdict.

## 16. Protected-only Runtime Profile

`_b3_preview_apply_gate_state` (`backend/app/api/main.py:369-409`) requires a
new explicit B.3 flag plus every required effective B.2C/B.2D/B.2E flag via
`b3_profile_admission`.  Off preserves Legacy.  Invalid/missing environments,
partial profiles, and production return unavailable.  The code never enables a
dependency gate itself.

## 17. HTTP/Internal Adapter Integration

With B.3 on, Chat returns the existing reply/proposed-change fields plus a
detached `preview` metadata object (`plan_id`, both revisions,
`authoritative: false`).  Apply requires the existing `Idempotency-Key` and
`X-Expected-Plan-Revision` headers through `_protected_submit_change`.  B.3
does not add a request-level post-commit.

## 18. Session Ownership and Lifetime Audit

The B.3 Chat route closes its dependency Session before Runtime work
(`main.py:1169-1175`).  The new Chat adapter accepts scalar scope only
(`chat/service.py:53-82`).  Capture, each tool, classification, and final
verification own/close independent Sessions.  Apply opens its own durable
kernel Session.  No request Session or ORM membership crosses Runtime/provider
or durable transaction boundaries.

## 19. AI Proposal Trust Boundary

Models cannot choose Trip, membership, Plan, revision, Ledger ID, HMAC scope,
or authorization.  Model patches are filtered, Plan-scoped, replacement-provenance
checked, deterministically classified, then reclassified inside Apply's locks.

## 20. Privacy and Safe Outcome Contract

Preview serializes only existing public classification/result contracts and
safe candidate fields.  It does not return credentials, raw prompts, ORM rows,
private constraint text, membership identities, or raw provider data.  Apply
reuses the established Change Proposal outcome allowlist and historical/current
outcome validation.

## 21. PostgreSQL Preview Consistency Evidence

`tests/test_b3_revision_bracketed_preview.py` passed 5 tests on disposable
`localhost` database `cadensy_b3_test`:

- Stable Preview returns a detached candidate with `r0=0` and no mutation.
- A committed Plan revision change during Runtime causes `StalePreview` and
  leaves the proposed item unchanged.
- While Preview waits at a simulated Runtime/provider boundary, a separate
  connection obtains `Trip FOR UPDATE NOWAIT`; a real protected Change writer
  commits, then the original Preview is discarded as stale.

No arbitrary sleeps are used; Events and independent sessions coordinate the
Runtime-latency test.

## 22. Apply Writer-to-Writer Concurrency Evidence

The real writer race above uses the existing Trip-first protected Change
writer.  The B.3 HTTP test further proves first Apply and identical replay
produce one completed Ledger result and one revision increase.  Earlier B.2C
Change Proposal coverage remains the detailed contention/fault evidence for
the reused writer; B.3 did not fork it.

## 23. Fault Injection and Atomic Rollback

No new transaction code was added.  B.3 delegates mutation/fault rollback to
the accepted durable Change Proposal writer.  Its focused regression suite was
run with B.3 (42 tests total; §26 focused command), including protected writer
rollback/error behavior.  Additional B.3-specific failure injection beyond
the stale/revision race is still acceptance evidence to add before production
activation.

## 24. Historical Replay and HMAC Cases

No envelope changed.  The B.3 HTTP test performed first Apply plus identical
retry and verified identical response, one Plan revision, and one Ledger row.
The existing durable kernel continues to verify stored HMAC, fingerprint
version, historical/current outcome policy, expiry, and current authorization.

## 25. Domain Classification Regression Matrix

The B.3 path calls the unchanged classifier.  The reused protected Change
writer retains NOTICE, ROUND, REOPEN_ROUND, and CONFIRM coverage in
`test_protected_change_proposal_writer.py`.  The targeted B.3+profile+writer
run passed **42 tests** (exit code 0):

```powershell
python -m pytest tests/test_b3_revision_bracketed_preview.py `
  tests/test_b3_protected_profile.py `
  tests/test_protected_change_proposal_writer.py -q
```

The narrower B.3 Preview command alone passed **5 tests** (exit code 0).

## 26. Full Legacy CI Evidence

Safety environment was explicitly set to:

```text
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_b3_legacy_test
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_b3_legacy_runtime
DISABLE_SCHEDULER=1; MOCK_AI=1; DEV_ALLOW_MEMBERSHIP_HEADER=0
DEEPSEEK_API_KEY=; GEOAPIFY_API_KEY=
```

Exact CI-equivalent Legacy command (with all Pydantic-only modules ignored)
completed with **1 failed, 983 passed, 10 warnings in 47.10s**.  Failure:
`tests/test_upsert_fixed_accounts.py::test_upsert_fixed_accounts_is_idempotent`
expected two `User` rows but observed three.

The same command with only `tests/test_b3_revision_bracketed_preview.py`
excluded also completed with **1 failed, 978 passed, 10 warnings in 46.23s**,
the identical failure.  This demonstrates the full-suite failure is not caused
by B.3 test fixture data.  `git diff --check` passed; `py_compile` passed for
all B.3 changed Python files.  Both full commands had exit code **1**;
no JUnit XML was generated for this local validation.

## 27. Full Isolated Pydantic CI Evidence

On separately disposable localhost database `cadensy_b3_pydantic_test`, the
exact workflow Pydantic test list was run using
`backend/.venv-pydantic-poc/Scripts/python.exe`:

```text
tests/test_pydantic_ai_poc.py
tests/test_pydantic_composition.py
tests/test_pydantic_runtime.py
tests/test_symmetric_evaluation_harness.py
tests/test_formal_ab_runner.py
tests/test_runtime_composition.py
tests/test_provider_smoke_harness.py
tests/test_live_provider_smoke_adapter.py
tests/test_evaluation_foundation.py
```

Result: **119 passed in 8.12s**.
Exit code: **0**.  No JUnit XML was generated for this local validation.

## 28. Known Limitations

- B.3 does not create a server-persisted Preview token.  This is intentional:
  Apply revalidates all inputs/facts and treats Preview as informational.
- Replacement proposals still require Legacy trusted tool provenance; Pydantic
  replacement suggestions remain fail-closed.
- New B.3-specific fault-injection matrices beyond the accepted reused writer
  suite are not yet independently expanded.

## 29. Remaining Production Activation Blockers

- Resolve the reproducible full Legacy suite failure in
  `test_upsert_fixed_accounts_is_idempotent` without masking a seed/isolation
  defect.
- Independently review the complete B.3 acceptance matrix, including additional
  writer-vs-Preview/fault cases, before changing the production-denied gate.
- Production remains explicitly unavailable; no fleet-wide protected-profile
  deployment assertion was introduced.

## 30. B.3 Final Verdict

**B.3 — CONDITIONAL PASS.** The implemented default-off non-production
vertical slice is safe in its focused evidence, but the required complete
Legacy regression gate is currently red for a demonstrated non-B.3 suite
isolation failure.  Therefore PASS is not justified.

## 31. Readiness for R5 Review 1

**Not ready for R5 Review 1 as a PASS handoff.** It is ready for an independent
review of the B.3 implementation under this conditional finding.  First close
the full Legacy regression failure and then rerun the complete validation gate.

## B.3 — Final Correctness and CI Acceptance Closure

### A. Legacy full-CI root cause and source-backed correction

The prior failure was not a B.3 data leak and was reproducible only after the
full suite.  The isolated fixed-account module passed (`2 passed`), and the
actual preceding-account-join regression sequence passed after the correction
(`3 passed`):

```text
tests/test_generator_commit.py::test_real_protected_invite_join_during_detached_generator_work_stales_existing_empty_plan[account]
tests/test_upsert_fixed_accounts.py
```

Root cause: `generator_commit_store` in
`backend/tests/test_generator_commit.py` committed an account created by an
invite-join during detached Generator work, but its teardown tracked only the
seed organizer ID.  That joined account survived into later tests; the final
fixed-account idempotency test therefore correctly observed one extra User.

The narrow fixture correction derives account User IDs from memberships of the
fixture-owned Trip before deleting those memberships, then removes their
AuthSessions and Users.  It neither weakens the two-account assertion nor
adds a broad database cleanup.  The complete CI ordering is the final
regression proof: the same fixed-account test now passes at the end of the
complete Legacy run.

### B. Candidate-option scope and privacy boundary

`preview_apply.py` now treats PR-04 `AgentCandidateOption` as non-authoritative
syntax, not as proof that its `item_ref` is in scope.  In a fresh bounded
read-only Session it verifies the captured Trip/Plan revisions, loads only
non-removed `PlanItem` rows from the captured Plan, validates each entire
patch, rechecks replacement provenance, and classifies non-empty patches
before serializing an option.  Unknown, foreign-Plan/foreign-Trip, removed,
and malformed targets are omitted.  The historical `keep` empty-patch option
is retained only when its target remains in that same Plan.

No private constraint, membership, credential, or provider payload is accepted
as an option field.  The option DTO remains limited to the existing public
PR-04 label/title/body/tradeoff/item/patch surface; final revision verification
still runs after option processing.

Focused PostgreSQL and HTTP tests prove that a foreign item, a removed item,
an unknown item, and malformed `start_hour` never reach the response, while
the valid option and `keep` remain.

### C. Partial patch / replacement provenance decision

The restrictive behavior is intentional and matches the accepted Legacy
`classify_change` tool contract (`backend/app/agents/tools.py:72-100`):
`title`, `place`, `lat`, and `lng` describe a venue replacement and must travel
together; price is part of that replacement provenance.  Therefore:

- time-only edits remain valid without provider provenance;
- title-only and price-only patches fail closed;
- a full replacement passes only when it exactly matches a trusted Legacy
  `find_replacement_place` result (including price when supplied);
- a full replacement without that provenance fails closed;
- Pydantic replacement suggestions remain fail closed because PR-04B does not
  supply this Legacy provenance.

The focused matrix is in
`test_replacement_provenance_retains_the_legacy_full_replacement_contract`.
No broad field-name policy was weakened.

### D. Tool Session/closure audit

`RevisionBracketedReadTripCapability._fresh_handler_tool()` no longer closes
over the original `AgentTool` or its handler.  A frozen descriptor contains
only name, description, JSON-round-tripped schema, guard, and cache policy;
the new handler locates the current Session's tool by name.  Thus the tool
construction Session has ended before the Runtime starts, and each invocation
opens its own PostgreSQL `SET TRANSACTION READ ONLY` Session.

The focused TrackingSession test proves capture, tool-descriptor construction,
tool invocation, classification, and final verification each use independent
sessions and all are closed before Preview returns.  The existing stale-tool
path remains fail-closed.

### E. Real HTTP Preview/Apply evidence

The former HTTP test no longer replaces the Preview service.  It calls
`POST /api/trips/{trip_id}/chat` through the real revision-bracketed service,
while substituting only a deterministic Runtime.  It exercises capture,
read-tool invocation, deterministic classification, final verification, HTTP
serialization, the existing protected Change Proposal Apply, and an identical
durable replay (one Plan revision and one ledger row).

A separate HTTP race holds the fake Runtime after a real read tool call.  A
real protected Change Proposal commits while Preview is paused; Preview returns
controlled HTTP 409 rather than an apparently-current candidate, and a later
Apply using the captured revision receives the expected stale conflict.  The
test uses Events and independent PostgreSQL Sessions, not sleeps.

Existing B.3 profile and protected Change Proposal tests cover default-off,
incomplete configuration, production rejection, missing/duplicate Plan,
removed membership, revoked credentials, authorization, and the detailed
writer fault/concurrency cases reused by Apply.

### F. Exact final validation evidence

All database runs used distinct disposable localhost PostgreSQL names, with
`DATABASE_URL` set to a different unused localhost name, scheduler disabled,
mock AI enabled, development membership headers disabled, and empty provider
keys.

| Validation | Result | JUnit |
| --- | --- | --- |
| B.3 focused Preview/HTTP/session suite | 10 passed, exit 0 | `b3-focused-closure.xml` |
| Account-join cleanup followed by fixed-account idempotency | 3 passed, exit 0 | `b3-leak-regression.xml` |
| B.3 profile + B.2E + protected Change Proposal regressions | 77 passed, exit 0 | `b3-relevant-regression.xml` |
| Complete Legacy CI-equivalent selection | 989 passed, 0 failed, exit 0, 47.581s | `b3-legacy-full.xml` |
| Complete isolated Pydantic CI-equivalent selection | 119 passed, exit 0, 7.09s | `b3-pydantic-full.xml` |
| `py_compile` of changed B.3/fix modules | exit 0 | n/a |
| `git diff --check` | exit 0 | n/a |

The full Legacy command used the exact workflow Pydantic exclusions.  The
Pydantic command used the exact nine-module workflow list.  No remote database
or provider call occurred.

### G. Exact modified files in this closure

- `backend/app/domain/preview_apply.py`
- `backend/tests/test_b3_revision_bracketed_preview.py`
- `backend/tests/test_generator_commit.py`
- this report

Earlier B.3 adapter files remain unchanged in this closure except for their
already-present integration work; no migration, kernel, HMAC/UUID, generator,
frontend, production-gate, commit, push, or deployment change was made.

## Final B.3 Verdict

**B.3 — PASS.** The default-off, non-production Preview/Apply vertical slice
now has complete focused, relevant-writer, full Legacy, and isolated Pydantic
evidence.  Production remains explicitly denied, and this PASS does not begin
R5 Review 1 automatically.
