# Cadensy PR-03 — Isolated Pydantic AI Compatibility PoC

Status: implemented and verified locally with Fake Model / FunctionModel,
synthetic fixtures, and an explicitly named disposable PostgreSQL database.
No real DeepSeek request, AWS/RDS access, production migration, deployment,
Git push/merge, Chat route change, Runtime selector, or PR-04 work occurred.

## Scope and integration boundary

This is a framework capability proof, not a Chat Runtime migration. The only
prototype module is `backend/app/agents/pydantic_poc.py`; it is not imported by
`backend/app/api/main.py`, `backend/app/agents/base.py`, or the production Chat
service. The following remain authoritative and unmodified in behavior:

* Legacy Custom Runtime and `app.agents.base`;
* read-only Cadensy tool scope and deterministic constraint/decision engine;
* PR-01C `run_agent_with_deadline` lifecycle/session boundary;
* server-side preview/transactional application and explicit human approval.

Model output is restricted to a typed `SuggestedAction` or typed
clarification. It does not create `base_plan_revision`, Server Preview
Metadata, a proposal, a vote, a PlanItem, or any database write. A future
integration must have the server derive those separate artifacts from a trusted
Plan Snapshot and deterministic classification.

## Exact dependency configuration

The isolated configuration is:

| Item | Verified version / policy |
|---|---|
| Python | 3.13.5 |
| Pydantic AI package | `pydantic-ai-slim[openai]==2.54.0` |
| Pydantic | 2.13.4 |
| OpenAI SDK | 3.24.0 |
| FastAPI in PoC venv | 0.142.2 |
| SQLAlchemy in PoC venv | 2.1.3 |
| Reproducible closure | `backend/requirements-pydantic-ai-poc.lock.txt` |
| Minimal declaration | `backend/requirements-pydantic-ai-poc.txt` |

The lock file was installed into `backend/.venv-pydantic-poc`; `pip check`
reported no broken requirements. The production-oriented `requirements.txt`
was deliberately not changed and no runtime code imports Pydantic AI.

During the initial local API investigation, installing the optional `openai`
extra into the pre-existing development `.venv` upgraded its local `openai`
package from 3.0.0 to 3.24.0 and `httpx2`/`httpcore2` from 2.10.0 to 2.13.1.
That is a local developer-environment change only; it is not a source pin or
production rollout. The isolated PoC venv and lock file are the reproducible
verification target. Any future production dependency change requires separate
review.

Create the isolated environment with:

```powershell
cd backend
.\.venv\Scripts\python.exe -m venv .venv-pydantic-poc
.\.venv-pydantic-poc\Scripts\python.exe -m pip install -r requirements-pydantic-ai-poc.lock.txt
```

The official installation guidance documents `pydantic-ai-slim[openai]` for
OpenAI-compatible and DeepSeek support. The pinned version was selected only
after checking its installed API signatures for `Agent`, `deps_type`,
`RunContext`, `UsageLimits`, `TestModel`, `FunctionModel`, output validators,
and `DeepSeekProvider`.

The deterministic PR-03 command is:

```powershell
cd backend
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr03_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
$env:PYDANTIC_AI_NO_BANNER='1'
.\.venv-pydantic-poc\Scripts\python.exe -m pytest -q tests/test_evaluation_foundation.py tests/test_pydantic_ai_poc.py
```

Without the PR-03 lock installed, the PoC test module is skipped rather than
causing a production Runtime import or changing the default dependency set.

## PoC architecture

```text
Fake TestModel / FunctionModel
        |
        v
Pydantic Agent[TripReadCapability, typed ModelOutput]
        |                     |
        |                     +-- frozen trip_id, actor_membership_id,
        |                         independent Session factory only
        v
Typed read-only function tool
        |
        v
new short-lived worker Session -> existing build_read_only_trip_tools
        |
        v
existing deterministic fact/classification engine (no writes)
        |
        v
typed output validator -> framework-neutral observation
        |
        +-- optional PR-01C deadline wrapper; late result is discarded
```

`TripReadCapability` is a frozen dataclass and never carries a request Session.
Each tool invocation opens and closes its own Session. The PoC's internal
instrumentation contains only a safe tool name, output kind, and usage metadata;
it deliberately does not record prompt text, raw tool payloads, membership IDs,
provider headers, or private constraint wording.

## Fake compatibility results

`tests/test_pydantic_ai_poc.py` disables `pydantic_ai.models.ALLOW_MODEL_REQUESTS`
globally, so an accidental live model request fails before network I/O.

| Requirement | Evidence | Result |
|---|---|---|
| Typed `deps_type` / immutable capability | `TripReadCapability` + `RunContext[TripReadCapability]` | Pass |
| No request Session sharing | Worker Session is a distinct connection, is closed, and is not the request Session | Pass |
| Trip-scoped read-only tool | `TestModel` invokes real `get_trip_facts` through existing Cadensy tool builder | Pass |
| Tool argument validation | FunctionModel sends integer for strict string `item_id`; handler is not run until a valid ID arrives | Pass |
| Discriminated structured output | Typed `change_preview` / `clarification` output tools, each carrying `output_kind` | Pass |
| Output validator / bounded retry | Invented item preview is rejected; valid retry succeeds. Permanently invalid output stops after two attempts | Pass |
| Usage limits | Request limit and tool-call limit both raise `UsageLimitExceeded` | Pass |
| Fake 401 / 429 / 503 | FunctionModel raises Pydantic `ModelHTTPError`; maps to stable PR-02 taxonomy | Pass, simulated only |
| Timeout/cancellation | Cooperative async model receives local cancellation through PR-01C boundary | Pass |
| Blocking late result | Sync callback is not claimed killed; it completes after release and is recorded/discarded as `late_completion` | Pass |
| Privacy-safe instrumentation | Synthetic private phrase and membership ID absent from serialized safety metadata | Pass |

The PR-03 PoC suite is **11 passed**. The isolated PoC environment additionally
ran the PR-02 evaluator contracts: **15 passed** across
`test_evaluation_foundation.py` and `test_pydantic_ai_poc.py`.

## Deadline, cancellation, and PR-01C compatibility

The prototype uses `run_agent_with_deadline` rather than creating its own
background execution policy. It passes the remaining provider budget into
Pydantic AI model settings and checks the existing tool budget around actual
read-only tool work. Those are distinct from the outer request deadline.

The tested guarantees are deliberately bounded:

* cooperative async work is locally cancelled when the request deadline elapses;
* a blocking synchronous callback may continue after HTTP/local timeout;
* a late output is never consumed by the caller and cannot affect an already
  returned response;
* all Pydantic tools in this PoC are read-only, so no Proposal, Vote, PlanItem,
  or other durable plan mutation can occur from late completion;
* lifecycle events remain metadata-only and contain no private prompt/tool data.

This does not claim that Python can force-kill a synchronous thread or undo a
remote provider request already accepted by the provider.

## PR-02 evaluation compatibility and fixture correction

The PoC normalizes `output_kind`, `suggested_action`, `tool_trajectory`,
`error_classification`, `usage`, and safety metadata into
`StructuredPreviewObservation`. These fields map to the frozen PR-02
observation contract without changing any of the eight Golden case business
expectations. No Legacy-versus-Pydantic score comparison is claimed.

PR-02's original runner used a nested fixture transaction that was not visible
to a new worker Session and did not execute a real tool. The failure was
characterized first. The corrected runner now commits only uniquely owned
synthetic rows through a safe independent factory, exercises real
`get_current_plan`, `get_trip_facts`, and `classify_change` calls from another
Session, then deletes only those exact IDs. The original V1 report remains at
`backend/evals/reports/legacy_baseline_v1/`; the regenerated V2 report is at
`backend/evals/reports/legacy_baseline_v2_fixture_visible/` and records:

* destination `Synthetic City` and two members from the actual fixture;
* both expected plan items;
* deterministic `notice` classification from the real `classify_change` tool;
* independent worker-session ownership.

V2 result: **8/8 Golden cases passed, 0 safety violations**.

## DeepSeek compatibility matrix

Current Cadensy configuration uses `deepseek-v4-flash` via the existing
OpenAI-compatible Chat Completions path. This table keeps documentation, local
fakes, and real-provider evidence distinct.

| Capability | Documented support | Fake/local verification | Real DeepSeek verification | Status |
|---|---|---|---|---|
| `DeepSeekProvider` | Yes | Constructed with placeholder credential; no I/O | No | Requires approval |
| Chat Completions model | Yes (`deepseek:` / `OpenAIChatModel`) | Model object constructed | No | Requires approval |
| Typed function tools | Yes through Chat Completions | Real Cadensy read-only function tool invoked via fake model | No | Requires approval |
| Structured output | Yes, via output tools | Discriminated typed output and validation/retry pass | No | Requires approval |
| Thinking mode | V4 thinks by default | `thinking=False` model settings constructed | No | Requires approval |
| Tool choice while thinking | Documentation warns forced choice is unsupported while thinking | Not sent to provider | No | Unsupported in this configuration |
| OpenAI-compatible custom client | Yes | No transport call | No | Requires approval |
| 401 / 429 / 5xx handling | Provider may surface HTTP errors | FunctionModel/`ModelHTTPError` taxonomy pass | No | Requires approval |
| Responses API state/background features | Documentation lists conversation ID, previous response ID, background mode, and most native tools as unavailable/ignored | Not used | No | Unsupported / not selected |

For this use case, the PoC selects Chat Completions and disables thinking when
typed output/tool behavior is required. A mocked success is not a real DeepSeek
compatibility pass.

### Separately authorized real-provider verification plan

No real call is authorized in PR-03. If approval is granted later, run only
these bounded cases against a dedicated test trip and Fake-free test process:

1. one no-tool typed clarification with `thinking=False` (one request);
2. one scoped `get_plan_item` plus typed preview (normally two requests);
3. one deliberate provider-settings compatibility probe, stopping after its
   first response/error (one request).

Maximum planned provider requests: **four**, with no OpenAI SDK transport
retries (`max_retries=0`), Agent request limit two per case, tool-call limit
one, total-token limit 2,000, a short provider timeout, and no streaming. Use
a separately supplied DeepSeek credential only in the process environment;
never print, persist, or commit it. Stop immediately on any privacy leak,
cross-trip exposure, schema/tool mismatch, unexpected retry, deadline breach,
HTTP failure, or usage-limit breach. Actual monetary cost remains unverified
until that separately authorized provider run; the request/token ceilings are
the current cost controls.

## PR-02 retained quality gates

`17_REGRESSION_REMEDIATION_PLAN.md` records the separate required remediation
for the full baseline: 58 legacy Membership Header fixture failures and two
Organizer domain discrepancies. It requires authenticated Bearer fixture
migration without restoring insecure headers, evidence before any Organizer
rule/assertion change, and a passing full backend suite before PR-04 formal
integration and A/B acceptance.

The final PR-03 full-suite run was **413 passed / 60 failed**. The twelve
additional passes are this PR's isolated evaluation/PoC tests; the 60 failures
are unchanged and are classified by the remediation plan. No insecure
authentication fallback was enabled to alter that result.

## Changed files

| Area | Files |
|---|---|
| Isolated typed adapter | `backend/app/agents/pydantic_poc.py` |
| Fake compatibility tests | `backend/tests/test_pydantic_ai_poc.py` |
| PR-02 fixture visibility repair | `backend/evals/runner.py`, `backend/tests/test_evaluation_foundation.py`, V2 report artifacts |
| Isolated dependency configuration | `backend/requirements-pydantic-ai-poc.txt`, `backend/requirements-pydantic-ai-poc.lock.txt` |
| Documentation | this record, `15_PR02_EVALUATION_FOUNDATION.md`, `17_REGRESSION_REMEDIATION_PLAN.md` |

## PR-04 integration readiness verdict

**Not ready for PR-04 integration.** PR-03 establishes that the selected
Pydantic AI APIs can satisfy the isolated typed/read-only/deadline contracts
with Fake Models. It does not validate real DeepSeek behavior or authorize
Runtime replacement. PR-04 remains blocked on independent approval, the
Regression Remediation Plan's full-suite gate, an approved real-provider plan
if production compatibility is required, and a separate Legacy-versus-Pydantic
A/B evaluation using the unchanged PR-02 dataset.
