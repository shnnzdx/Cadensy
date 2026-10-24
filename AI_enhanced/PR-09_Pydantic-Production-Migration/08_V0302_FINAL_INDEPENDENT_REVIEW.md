# v0.3.0-02C Final Independent Review

Date: 2026-10-23
Reviewer role: independent security, backend-architecture, and release reviewer
Review state: **BLOCKED — do not commit or open a PR**

## A. Source baseline and complete inventory

| Item | Observed value |
| --- | --- |
| Worktree | `Cadensy-v0302-pydantic-functional-parity` (local implementation worktree) |
| Branch | `codex/v0302-pydantic-functional-parity` |
| `HEAD` | `5ce3782df8222cc87fd468bf1cf649a6d4aefe69` |
| Merge-base with `origin/main` | `5ce3782df8222cc87fd468bf1cf649a6d4aefe69` |
| Ahead / behind `origin/main` | `0 / 0` |
| Baseline requested for review | `5ce3782df8222cc87fd468bf1cf649a6d4aefe69` |

This is a worktree review, not a review of an already-created commit. At review
start, the tracked modifications were `backend/app/agents/pydantic_runtime.py`
and `backend/tests/test_pydantic_runtime.py`; the untracked PR-09 records were
`02` through `07`. The source diff is 298 additions / 91 removals in the runtime
and 645 additions / 5 removals in its tests. `git diff --check` passed against the
baseline. No credential, `.env`, PostgreSQL data, cache, generated log, patch, or
production configuration file is part of the candidate diff.

The new review artifact is this `08` file. It is deliberately not staged or
committed while the blockers below remain unresolved.

## B. Functional architecture assessment

`PydanticChatAgentRuntime` accepts only the existing `RuntimeReadTool` capability
seam. It creates a per-execution dependency container for approved read tools,
deadline state, grounded read ids, evidence, candidate options, and cache. It
does not receive a SQLAlchemy Session, database URL, provider credentials, generic
network access, a write tool, or a model-visible `classify_change` tool.

The path is intentionally advisory:

`FunctionModel/Pydantic typed call -> guarded scoped handler -> RuntimeResult -> RevisionBracketedPreview -> durable coordinator`

The existing handler owns option calculation and `_validated_suggestions`; Preview
owns current membership/plan scope, revision-bracketed re-reads, deterministic
classification, and admissibility. Durable persistence remains downstream of that
Preview. `classify_change` remains in the Application/Domain boundary
(`backend/app/domain/preview_apply.py:405` and `:454`), not in the Pydantic tool
surface.

## C. Security trust boundary

The review found no concrete P0/P1 authorization defect in the replacement-evidence
path. A model can request a scoped lookup, but cannot construct a
`ToolExecutionEvidence`; evidence is appended only after a dict-shaped approved
handler return in `_invoke_scoped_tool` (`pydantic_runtime.py:300-313`). Guard
rejection, unsupported tools, handler exceptions, tool deadlines, model failure,
and late results do not produce usable replacement candidates. The v0.3.0-01
immutable DTO boundary remains the authority; the Pydantic runtime only constructs
that DTO from detached handler projection values.

Replacement matching follows the unchanged Preview policy. The adapter accepts a
venue patch only if one approved same-run candidate exact-matches title/place/lat/lng
and, where present, price. Its candidate iteration continues after a non-matching
price, so candidate ordering cannot deny a later valid candidate. Preview repeats
the provenance check independently; it was not weakened for Pydantic.

The cache is per `_PydanticRunDeps`, keyed by tool name plus sorted JSON arguments,
and is consulted only after the plan/item/conflict guards. Only dict-shaped output
is cached. Its raw projection is a trusted in-process handler output; no model
tool result is re-used as evidence before wrapper validation. No distinct primitive
argument type collision was found in the exposed typed schemas. This is acceptable
for the present read-only, per-run seam; no speculative cache refactor is proposed.

## D. Targeted Options review

The typed Pydantic `propose_options` surface bounds ids, count, day, conflict text,
and suggestion presentation fields. It forwards its suggestions to the existing
`app.agents.tools._validated_suggestions`, which constrains the patch to one real
item and invokes authoritative classification in a savepoint. The runtime converts
only handler-returned, syntactically safe single-item patches to
`AgentCandidateOption`; Preview then repeats scope/revision/classification checks.

The initially reported concern that `get_current_plan(day="all")` followed by a
specific-day named conflict was a P1 rejection is **not sustained**. The existing
handler's `_option_focus` (`backend/app/agents/tools.py:935-949`) silently falls
back to that day's longest item when the named ids are absent from the narrowed
day. The Pydantic guard at `pydantic_runtime.py:275-278` rejects the ambiguous
combination before dispatch, and
`test_pydantic_options_rejects_specific_conflict_outside_its_read_day` asserts
that no handler call or successful evidence occurs. This is a deliberate
fail-closed protection against an unrelated-item fallback, not a safe case to
relax without a separately approved handler contract change.

The documented behavior supports cross-day *patches* through existing suggestion
validation, but a targeted conflict must be read for the same narrowed day. A
multi-day conflict therefore needs explicit per-day reads/requests rather than
silently treating a full-plan projection as a proof of same-day membership.

## E. Replacement, Preview, and Durable review

The source tests cover exact price/no-price matching across candidate order,
forged venue output refusal, real scoped lookup into `RevisionBracketedPreview`,
and durable option persistence. The real replacement test uses a `FunctionModel`,
the real `LegacyReadTripCapability`, an injected local candidate, then asserts a
`succeeded` immutable candidate evidence record and exact Preview patch
(`test_pydantic_runtime.py:959`). The durable test is a separate
Context -> Pydantic -> Preview -> coordinator path (`:1032`).

These are the right boundaries and no Legacy runtime or production selector source
was changed. The selector test sets `CHAT_AGENT_RUNTIME=pydantic` only through an
explicit injected test runtime; production configuration continues to default to
Legacy. No real DeepSeek call is involved.

## F. Independent 11-scenario evaluation trace

`07_V0302B_FUNCTIONAL_EVALUATION.json` is a useful scenario-to-test manifest, but
it is not itself execution evidence: it records test names and `"pass"`, not each
run's tool trajectory, observed result, test stdout, exit code, or whether the
named test is Pydantic/FunctionModel versus a generic downstream test. The table
below corrects that overstatement by distinguishing source-assertion coverage from
fresh observed execution.

| Scenario | Source assertion / runtime | Tool or integration evidence asserted in source | Independent execution status |
| --- | --- | --- | --- |
| grounded-trip-facts | `test_pydantic_trip_facts_and_options_use_scoped_handlers_only`; FunctionModel | exact scoped facts/plan/options call records | Manifest only; no completed fresh run |
| existing-plan-schedule-change | `test_suggested_change_requires_same_run_scoped_read_and_has_no_write_authority`; FunctionModel | read-before-change, succeeded evidence, advisory DTO | Fresh run passed (one of initial two dots); no DB integration in this test |
| specific-two-item-conflict | `test_pydantic_real_options_handler_keeps_a_named_conflict_off_unrelated_longest_item`; FunctionModel + real handler | names Art/Dinner and excludes 600-minute unrelated item | Not freshly completed; requires PostgreSQL |
| multiple-targeted-compromises | `test_pydantic_options_passes_grounded_conflict_ids_and_typed_suggestions`; FunctionModel | keep plus two one-item patches with exact ids | Manifest only |
| direct candidate without options | same schedule-change test; FunctionModel | no write authority | Fresh source-level run passed; durable assertion is a separate test |
| trusted venue replacement | `test_pydantic_real_scoped_replacement_evidence_reaches_revision_bracketed_preview`; FunctionModel + real handler + Preview | succeeded evidence, trusted id, exact Preview candidate | Not freshly completed; requires PostgreSQL |
| fabricated venue refusal | `test_replacement_shaped_suggestion_without_provenance_fails_closed`; FunctionModel | malformed output, no accepted change | Manifest only |
| unknown/foreign plan item | `test_pydantic_options_rejects_foreign_conflict_ids_before_handler_call`; FunctionModel | empty handler calls; no succeeded/cache-hit evidence | Manifest only |
| revoked membership / stale revision | `test_membership_revocation_during_runtime_discards_uncommitted_reply` and `test_preview_discards_candidate_when_revision_changes_during_runtime` | fenced persistence and revision discard | Downstream generic tests, not a Pydantic FunctionModel scenario; manifest claim is incomplete |
| underspecified clarification | `test_typed_reply_and_clarification_map_to_common_runtime_result`; FunctionModel | clarification DTO; empty options/evidence | Manifest only; its node was queued after the stalled database-backed node |
| durable valid options / no plan write | `test_pydantic_runtime_persists_preview_options_through_durable_coordinator` plus coordinator metadata test | durable completion, captured revision, safe metadata filtering | Not freshly completed; requires PostgreSQL |

No scenario was observed to fail. However, the cited `11/11 passed` result cannot
be independently re-certified from the committed artifacts: three entries rely on
PostgreSQL integration that was not captured as a completed run in this review,
and the membership/revision row is not Pydantic FunctionModel evidence. This is a
P1 release-evidence gap, not evidence of a replacement authorization bypass.

## G. Frozen A/B historical gaps

The frozen dataset hash and baseline in `03_V0302_FROZEN_AB_EVALUATION.json` are
unchanged. `06_V0302B_FROZEN_EVALUATION_DIAGNOSTIC.md` traces `ambiguous-time` to
Application preflight wording recognition and `privacy-injection` to deterministic
item-reference handling before either runtime executes. The frozen artifact shows
the same Legacy/Pydantic business failures and safety passes. Both are historical
Application/Product baseline gaps, not Pydantic-specific regressions; no frozen
dataset, grader, or shared production behavior was changed here.

## H. Tests, commands, and actual results

Historical records report:

| Command / suite | Recorded result | Independent conclusion |
| --- | --- | --- |
| `python -m pytest backend/tests/test_pydantic_runtime.py -q` | 43 passed | Historical only |
| related policy/Preview/Durable suite | 242 passed | Historical only |
| `python -m pytest backend/tests -q` | 1337 passed, 10 warnings | Historical ordinary command assertion only |
| five non-overlapping sorted-file partitions | 534 + 184 + 119 + 252 + 248 = 1337 | Historical partition sum, not one captured full-run exit result |

This review independently ran:

```powershell
backend/.venv/Scripts/python.exe -m compileall -q backend/app/agents/pydantic_runtime.py
backend/.venv/Scripts/python.exe -m pytest --collect-only -q backend/tests/test_pydantic_runtime.py
backend/.venv/Scripts/python.exe -m pytest --collect-only -q backend/tests
git diff --check 5ce3782df8222cc87fd468bf1cf649a6d4aefe69
```

All exited `0`: compilation succeeded; 43 Pydantic tests and 1337 backend tests
collected; and the diff check passed. A focused 12-function scenario command was
then started on the verified task-owned temporary PostgreSQL cluster at 55434. It
emitted two passing test dots but stalled with no CPU progress before completing;
the reviewer stopped only the two self-started pytest processes and stopped the
temporary cluster. Both ports 5432 and 55434 were then inactive. Consequently,
this review does **not** recertify any full execution pass or assign an exit code
to that interrupted command.

## I. Defects and release blockers

### P1 — Independent functional-execution evidence is incomplete

Affected artifact: `07_V0302B_FUNCTIONAL_EVALUATION.json:1-107`.

The file declares per-scenario results without per-scenario execution evidence,
and its membership/revision scenario points to generic downstream tests while
claiming a Pydantic/FunctionModel evaluation. A fresh focused PostgreSQL execution
attempt did not finish in this review. Before PR approval, capture a successful
focused execution with exact node ids and exit code, then update the evaluation
artifact with actual runtime, tool trajectory/observed outcome, Preview/Durable
evidence, and FunctionModel-versus-synthetic attribution for all 11 scenarios.

### P1 — Required product/contract/handoff documentation is not synchronized

Affected rule: `docs/AGENTS.md:207-214`.

This change expands the Pydantic runtime's behavior and guarantees, but the
required synchronized documents are not changed: `docs/PRODUCT.md`,
`trip/BACKEND.md`, and `HANDOFF.md`. Update them with the advisory-only,
Legacy-default, Preview-authoritative boundary, or explicitly obtain and record an
exception to that repository rule. PR-09 implementation notes are not substitutes
for the required user-facing/backend handoff records.

### P2 — Complete regression proof is partitioned and incompletely retained

The reported 1337 is a non-overlapping partition sum and the ordinary full-suite
command is reported but lacks captured exit output. Preserve the command output and
exit code for the final pre-merge execution. This is evidence-quality debt, not a
demonstrated source regression.

### P2 — Temporary PostgreSQL launch configuration discrepancy

The retained task cluster's data directory started on 5432 when launched without
explicit options despite the historical task port being 55434. The review stopped
it immediately and re-ran it only with explicit `-p 55434`; no tests were run on
5432. Future review scripts must supply the intended host/port explicitly and
confirm them before pytest.

## J. Standards and compatibility

Standards review otherwise found no hard source-boundary breach: no schema/API/
provider/selector change, no Pydantic AI expansion, no credential artifact, and no
Legacy runtime edit. The only minor code-quality observation is that the
security-sensitive numeric validation helper is dense; it is not a reason to
refactor in this release review.

Legacy compatibility is source-preserved because `legacy_runtime.py`, common
contract semantics, production selector, schema, Protected Apply, and conversation
schema are unchanged. The production selector remains Legacy by default. This is a
static compatibility conclusion; it does not replace the missing independently
captured full regression execution.

## K. Final PR readiness verdict

Do not stage or create the proposed local commit. The correct next action is to
close the two P1 release-governance/evidence blockers, rerun and preserve the
focused and complete regression evidence on an explicitly isolated disposable
PostgreSQL cluster, then request another independent final review. No production
authorization is implied by this report.

`v0.3.0-02 FINAL CODE REVIEW: BLOCKED`

`NEW FEATURE FUNCTIONAL EVALUATION: PARTIAL`

`HISTORICAL FROZEN BUSINESS: KNOWN BASELINE GAP`

`SECURITY REVIEW: PASS`

`LEGACY COMPATIBILITY: PASS`

`PYDANTIC PREVIEW & DURABLE INTEGRATION: FAIL`

`FULL BACKEND REGRESSION: NOT EXECUTED`

`READY FOR LOCAL COMMIT: NO`

`LOCAL COMMIT: NOT PERFORMED`

`READY FOR PR REVIEW: NO`

`PRODUCTION CHANGE AUTHORIZATION: NOT GRANTED`

## 2026-10-23 v0.3.0-02D addendum

The two P1 release blockers recorded above are closed by the execution-backed
`09_V0302D_FUNCTIONAL_EVALUATION_EVIDENCE.json` and
`10_V0302D_EVIDENCE_AND_DOCUMENTATION_CLOSURE.md`. The earlier PostgreSQL stall
was reproduced as a test-environment configuration error: the worktree had no
`backend/.env`, pytest therefore fell back to `localhost:5432`, while the temporary
cluster was running on a different port. A fresh task-owned cluster, explicit test
URL, and loopback IPv4/IPv6 listener completed the scenario suite, focused groups,
frozen evaluation, and all five full-regression partitions.

This addendum does not erase the original blocked review or reclassify the
membership/revision scenario as a Pydantic FunctionModel execution. That scenario
remains accurately recorded as runtime-neutral downstream coverage. Production
activation remains outside this worktree and is not authorized.
