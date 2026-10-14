# Cadensy PR-02 — Evaluation Foundation, Golden Dataset & Baseline Regression

Status: implemented and verified locally against an explicitly named,
disposable PostgreSQL database, synthetic fixtures, and Fake Providers only.
No Pydantic AI dependency was installed; no real DeepSeek call, AWS/RDS access,
production migration, deployment, Git push, or PR-03 work occurred.

## Corrected boundary

| PR-02 owns | Explicitly deferred to PR-03 |
|---|---|
| Legacy Custom Runtime baseline; synthetic Golden Dataset; evaluation contracts; deterministic graders; failure injection; regression repair; reproducible reports. | Pydantic AI version pinning; Fake Model compatibility; real DeepSeek/provider compatibility; structured-output/tool-calling PoC; thinking-mode evaluation. |

The PR-02 evaluation contract is framework-neutral. A future runtime must
normalize its output to the same business/safety observation shape and run the
unchanged dataset and deterministic graders. It may not edit expected results
to improve its score.

## Regression repair: retired Membership Header fixtures

The 13 previously failing positive Chat HTTP cases were inspected. Their only
common failure was a raw `X-Membership-Id` used as positive authentication,
which PR-01B correctly rejects with `401`.

`test_chat.py` and `test_chat_safety_and_time.py` now authenticate their
positive API calls by public account login, then supply only:

```text
Authorization: Bearer <account-token>
X-Trip-Id: <requested-trip>
```

The one remaining raw membership header use is named
`test_chat_rejects_raw_membership_header_as_an_explicit_security_negative` and
asserts `401` before Agent execution. No compatibility switch or authorization
fallback was restored.

| Command | Result | Interpretation |
|---|---:|---|
| `pytest -q tests/test_chat.py tests/test_chat_safety_and_time.py` | 27 passed | All migrated Chat HTTP positives now use account bearer; the named negative stays fail-closed. |
| Historical pre-migration baseline | 13 failed / 13 passed | Superseded fixture debt, not a PR-01C runtime regression. |

## Versioned Golden Dataset

Dataset: `backend/evals/datasets/chat_change_preview_v1.json`.

Every case records fixture preconditions, known facts, unknown facts, allowed
output kinds, expected business outcome, deterministic oracle source, expected
tool contract, Fake Provider mode, and safety invariants. The eight current
synthetic cases are:

| Case | Main contract | Allowed result |
|---|---|---|
| `explicit-time-notice` | Target and time are known; `classify_change` must be grounded in Art Institute. | `change_preview`, `notice` |
| `ambiguous-time` | Target is known but no target time exists. | `clarification` |
| `ambiguous-item` | Neither target item nor target time is known. | `clarification` |
| `booked-item-confirm` | Moving the booked dinner invokes the deterministic booking rule. | `change_preview`, `confirm` |
| `provider-failure-fallback` | Synthetic provider error leaves a read-only fallback. | `safe_degraded` |
| `tool-failure-fallback` | Synthetic stopped Agent/tool state leaves a read-only fallback. | `safe_degraded` |
| `privacy-injection` | An instruction-injection request cannot disclose fixture private wording. | `reply_only` |
| `cross-trip-denied` | Foreign membership is denied before Agent work. | `access_denied` |

The ambiguous-time case explicitly declares `target time` as unknown and only
allows clarification. It contains no hidden target time and does not force a
fabricated preview or decision path.

## Framework-neutral runner and graders

`backend/evals/runner.py` runs the Legacy Runtime through the public
`respond_to_trip_chat` seam, with a Fake Provider response only where Agent
work is appropriate. It creates uniquely identified synthetic fixture rows
through a separate disposable-database Session, makes them visible to a second
worker-owned Session, then deletes only those exact rows in dependency order.
The runner does not duplicate the Planner generator;
existing `run_planner_eval.py`, `run_real_trip_tools_trace.py`, and Agent/Chat
regressions remain their own source-specific harnesses.

`backend/evals/graders.py` (`chat-golden-grader-v1`) evaluates:

* task success and output kind;
* intent/item resolution;
* tool selection and expected arguments;
* deterministic decision path;
* read-only/privacy/cross-trip safety violations;
* safe fallback classification;
* latency recording;
* token usage and cost (`null` when unknown);
* failure taxonomy.

The Legacy Fake Provider baseline validates business-contract wiring, not
probabilistic model quality. A future real-provider evaluation is intentionally
outside this PR.

## Reproducible artifacts and Legacy baseline

The report writer produces:

* `manifest.json` — runtime, Git commit, dataset version/hash, fake model
  configuration, prompt/tool-contract/grader versions, and configured deadline
  contract;
* `summary.json` — total, passed, failed, and safety-violation counts;
* `cases.jsonl` — one normalized, graded case per line.

Recorded artifacts: `backend/evals/reports/legacy_baseline_v1/` (preserved
original) and `backend/evals/reports/legacy_baseline_v2_fixture_visible/`
(corrected fixture-visibility baseline).

| Field | Recorded value |
|---|---|
| Dataset | `chat-change-preview-v1`, SHA-256 stored in manifest |
| Runtime | `legacy_custom_runtime`, source version, Fake Provider |
| Result | 8 total, 8 passed, 0 failed, 0 safety violations |
| Request / Provider / Tool configuration | 30 s / 20 s / 5 s |
| Fake-provider invocation budget, tokens, cost | `null` — not inferred from a fixture response |

The report distinguishes the configured 20-second provider budget from the
30-second request deadline. A Fake Provider has no actual provider invocation
budget, so that per-case field is recorded as `null` rather than misreported as
20 seconds.

### Fixture visibility correction

The original V1 runner placed fixture rows in a nested transaction and patched
the Legacy `call_agent` seam. Therefore a PR-01C-style independent worker
Session could not observe the rows, and no actual read-only tool invocation was
evidence. A new characterization test first failed with `Membership does not
belong to this trip`, proving the gap.

The V2 runner now verifies, before Golden cases run, a separate worker Session
actually calls `get_current_plan`, `get_trip_facts`, and `classify_change`.
Its recorded facts are Synthetic City, two members, the two fixture items, and
the real deterministic `notice` classification for the Art Institute move.
The V2 manifest carries this evidence. Cleanup addresses only fixture-owned
primary keys; no schema or unrelated rows are dropped. V1 remains intact for
review and was not silently replaced.

Run instructions and the future CI command are in `backend/evals/README.md`.
The direct runner rejects non-local or non-test-named `TEST_DATABASE_URL`
before connecting.

## Lifecycle evaluation follow-ups completed

PR-01C's tests were extended without weakening its session boundary:

| Scenario | Evidence |
|---|---|
| Queued cancellation vs running late worker | A deterministic single-worker executor plus Event barrier proves queued work yields `queued_cancellation` with `worker_cancel_requested=true`; a running worker yields `late_completion`. |
| Request vs Provider budget | Baseline manifest records 30 s request and 20 s configured provider budget; Fake invocation budget is null. Existing Agent regression verifies the remaining provider budget is passed to the provider client. |
| Trace redaction | Synthetic private phrase test verifies lifecycle JSON contains only timestamp, phase, opaque execution ID, elapsed time, consumed flag, and queued-cancel flag. |
| Worker capacity exhaustion | Public Chat service test injects capacity exhaustion and proves the existing safe degraded response, no preview, and no durable rows. |
| Timing stability | Slow tool/provider tests now coordinate start/release using Events rather than using completion `sleep()` as a synchronization mechanism. |

No request-scoped SQLAlchemy Session is passed into a worker; these tests retain
PR-01C's independent worker session ownership and read-only tool boundary.

## Current verification

All commands used:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr02_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
```

The pytest harness accepts only disposable test names and rebuilds a local
PostgreSQL database. All Provider behavior was fake; blank Geoapify config
prevents accidental external place calls.

| Test range | Result |
|---|---:|
| Chat HTTP regression repair | 27 passed |
| Evaluation dataset/runner/report contracts | 4 passed |
| Lifecycle follow-up injection range | 11 passed |
| Combined PR-02 authorized Chat/Agent/Tool/Lifecycle/Eval range | 146 passed |
| Legacy report CLI | 8/8 Golden cases passed; artifacts written locally |

### Full backend baseline — remaining failures, classified

A full `pytest -q` was also run against the same disposable target. The PR-02
handoff baseline was **401 passed / 60 failed**; after PR-03 added twelve
isolated evaluator/PoC tests, the unchanged functional baseline is **413
passed / 60 failed**. PR-02 does not hide this result by
reenabling header authentication. The 60 failures are classified as follows:

| Count | Files / tests | Actual cause | PR-02 disposition |
|---:|---|---|---|
| 9 | `tests/test_plan_generation.py` API cases | Every positive request still supplies raw `X-Membership-Id`; API now correctly returns `401` before planner behavior is reached. | Separate legacy bearer-fixture migration: account organizer/participant login plus requested-trip header. |
| 49 | `tests/test_trips.py` API cases | Legacy account and legacy anonymous-Guest header fixtures return `401`; two downstream `TypeError` assertions are only consequences of parsing that `401` response. | Separate migration, requiring explicit account-bearer and Guest-bearer fixture setup for each public route. |
| 2 | `tests/test_organizer.py::test_both_exits_decline_to_decide[clear-Free time]`; `tests/test_organizer.py::test_the_organizer_can_never_adopt_the_proposal` | Direct domain assertions unrelated to HTTP authentication: `clear` leaves `Birthday dinner` rather than `Free time`; one organizer attempt does not raise `NothingToDo`. | Pre-existing organizer-deadlock behavior discrepancy; not touched or normalized under PR-02. |

Thus the repaired Chat baseline has no remaining Header-positive tests. The
broader 58-fixture migration remains visible, fail-closed, and separately
tracked rather than being silently converted into green CI.

## CI readiness

`build-validation.yml` currently declares only `workflow_dispatch`; it has
backend and frontend test jobs but is not evidence of PR/push triggering or
branch-protection Required Checks. PR-00 is therefore **not verified complete**
and no automatic required check is claimed here.

PR-02 supplies a deterministic future CI command:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_foundation.py
```

PR-00 integration must add an approved `pull_request`/`push` trigger, run this
command against a disposable PostgreSQL service, upload the three report
artifacts, and configure/verify repository Required Checks separately. Real
Provider evaluation must remain a manual, explicitly authorized non-default
gate.

## Retained independent follow-ups

This PR neither implements nor reclassifies:

* PR-01B independent-session persistence verification;
* GuestSession corrupt trip/membership binding fail-closed test;
* historical `change_proposal.extended_at` schema drift;
* production AWS/deployment verification;
* browser bearer XSS/shared-device hardening.

## Known limitations

* The Fake Provider baseline proves contract behavior, not DeepSeek behavior,
  LLM quality, token cost, or remote cancellation.
* Current latency is recorded for visibility, not used as a hard performance
  gate; local machine scheduling remains variable.
* Report Git metadata describes the working repository `HEAD`, while uncommitted
  local changes must be reviewed separately.
* Dataset cases cover the highest-risk change-preview seams but are not a full
  natural-language coverage claim.

## Changed-files summary

| Area | Files |
|---|---|
| HTTP regression repair | `backend/tests/test_chat.py`, `backend/tests/test_chat_safety_and_time.py` |
| Versioned evaluation | `backend/evals/datasets/chat_change_preview_v1.json`, `backend/evals/runner.py`, `backend/evals/graders.py`, `backend/evals/README.md`, `backend/evals/reports/legacy_baseline_v1/*` |
| Evaluation tests | `backend/tests/test_evaluation_foundation.py` |
| Lifecycle follow-up tests/trace taxonomy | `backend/app/agents/execution.py`, `backend/tests/test_agent_execution_lifecycle.py`, `backend/tests/test_pr01c_agent_lifecycle.py` |
| Audit record | this document |

## PR-03 Compatibility PoC readiness verdict

**Ready for a separately authorized compatibility PoC, not for migration.**
PR-02 provides a fixed Golden Dataset, a Fake-Provider baseline, normalized
reports, and framework-neutral grading. PR-03 must still pin Pydantic AI,
exercise its Fake Model/transport API, and separately obtain authorization for
any real DeepSeek compatibility check. It must preserve this dataset, the
read-only domain boundary, and PR-01C deadline/session invariants.
