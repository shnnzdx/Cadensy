# PR-03R.1a — Final Offline Boundary Hardening

## Scope and non-goals

This change hardens the test-only DeepSeek compatibility smoke harness. It
does not invoke DeepSeek, read an API key, alter the formal Chat route, grant
Domain write authority, alter the frozen Golden Dataset, access AWS/RDS, push
Git, or start PR-04.

All provider interactions in this report used an in-process `httpx2`
transport with the non-secret offline placeholder. The resulting evidence is
an offline wire-contract proof, not a Real Provider Compatibility Pass.

## Changed files

- `.github/workflows/build-validation.yml`
- `backend/app/agents/provider_smoke_harness.py`
- `backend/evals/runner.py`
- `backend/tests/test_ci_workflow_contract.py`
- `backend/tests/test_evaluation_foundation.py`
- `backend/tests/test_provider_smoke_harness.py`
- This report

The previously delivered `22_PR03R_REAL_PROVIDER_PREFLIGHT.md` and
`23_PR03R_SMOKE_HARNESS_OFFLINE_VALIDATION.md` remain historical evidence;
they were not rewritten as part of this correction.

## Corrections implemented

### CI partition and zero-skip contract

The Legacy job now explicitly excludes both Pydantic-only modules:

```text
--ignore=tests/test_pydantic_ai_poc.py
--ignore=tests/test_provider_smoke_harness.py
```

The isolated Pydantic job explicitly runs:

```text
tests/test_pydantic_ai_poc.py
tests/test_provider_smoke_harness.py
tests/test_evaluation_foundation.py
```

`test_ci_workflow_contract.py` asserts both halves. The Legacy check still
parses JUnit and fails for any collected skip; Pydantic AI was not installed
to make Legacy collection work.

### Global Usage Ledger fail-closed behavior

`GlobalUsageLedger` has a first-terminal-cause stop state. Once stopped, its
HTTP boundary rejects every later request before capture or Mock handler
dispatch. The following conditions close the Harness:

- a fifth request after the four-request global budget;
- reported usage exactly reaching 2,000 or exceeding the configured budget;
- absent or invalid usage;
- a non-2xx Provider response;
- outbound privacy rejection;
- Provider/request/tool deadline failure;
- caller cancellation;
- malformed output or tool-schema failure; and
- an unexpected retry/usage-limit failure.

Usage evidence is retained even for a rejected over-budget response. For
example, a response reporting 28 tokens against a 27-token cap records 28 and
then stops; it is not treated as free. The exact-2,000 test proves that even
with three request slots left, the next outbound request is rejected before
the HTTP handler runs.

### R2 symbolic item reference

R2 now exposes only `scoped_item` to the model. The test-only
`get_scoped_plan_item(item_ref="scoped_item")` adapter maps that symbol inside
the trusted Trip-scoped capability to the synthetic PlanItem ID. It returns a
typed projection with no raw identifier.

The R2 test proves that the Model wire request contains `scoped_item` and not
the fixture PlanItem or Membership ID; a preview can be accepted only after a
successful scoped read; the independent worker Session sees the synthetic
fixture; a foreign Trip capability fails before Provider dispatch; and the two
R2 turns consume exactly two Provider requests. Captured wire evidence and
the persisted `SmokeObservation` contain symbolic item references only.

### Deadline and cancellation boundary

`DeadlineBoundModel.request()` now handles outer `asyncio.CancelledError`.
It signals local cancellation, requests cancellation of the local Provider
coroutine, retains that task until completion, and discards its terminal
payload/exception. This is intentionally not described as cancelling an
already accepted remote request.

The new failure-injection test calls `task.cancel()` while the mocked Provider
is running. It proves that the Provider task is tracked, the late result is
discarded, no active late task remains after controlled completion, and the
Agent does not proceed to tool execution. Separate tests preserve the
distinction between request deadline, provider deadline, tool deadline, local
cancellation, and eventual late completion.

### R3 required-tool success criteria

R3 succeeds only when all of the following hold:

1. exactly one Provider HTTP request was admitted;
2. the captured request has `reasoning_effort: "none"`;
3. the captured request has `tool_choice: "required"`;
4. exactly `compatibility_probe` was invoked; and
5. no second Provider request or Domain tool/write is observed.

Merely catching `UsageLimitExceeded` is no longer success. Missing probe
evidence produces `r3_required_probe_inconclusive` and closes the Harness.

### Explicit scenario tools and failure taxonomy

The outbound privacy gate now receives a per-scenario allow-list rather than
depending solely on a write-tool-name deny-list. Technical error labels map to
the frozen framework-neutral evaluation labels as follows:

| Technical failure | Normalized evaluation failure |
| --- | --- |
| `provider_timeout` | `provider_timeout` |
| `tool_timeout` | `tool_timeout` |
| `provider_compatibility` | `provider_compatibility` |
| `provider_http_429` | `provider_rate_limited` |
| `provider_http_5xx` | `provider_unavailable` |
| `missing_usage` | `usage_unavailable` |
| `usage_budget_exceeded` | `usage_limit` |
| `malformed_output` | `malformed_output` |
| `tool_schema_failure` | `tool_schema_failure` |
| `privacy_boundary_violation` | `privacy_boundary_violation` |

Request cancellation and unexpected retry are represented separately as
`request_cancelled` and `retry_policy_violation`; no failure category is
silently converted to a successful observation.

### Frozen Dataset and report-output safety

The V1 Dataset bytes in Git remain unchanged. A Windows checkout uses CRLF,
so its raw working-file SHA-256 differs from the approved LF Git-object hash.
The Runner, CI artifact validation, and static contract now hash canonical LF
content. Thus the approved V1 hash remains:

```text
e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

This is a cross-platform content-hash correction, not a Dataset update or
version bump. `git diff` confirmed no change to the Golden Dataset.

During verification, the standalone Runner was found to default to the
checked-in archived `legacy_baseline_v1` directory. It now requires an explicit
`--output` path, and a regression test proves that omission exits before any
database/report work. The accidentally written baseline artifacts were
restored byte-for-byte to their repository content; all new local evidence was
written under a separate `pr03r1a-*` path.

## Offline verification

Database work used disposable local PostgreSQL only:

- `TEST_DATABASE_URL`: localhost database named `test_pr03r1a_smoke`;
- a distinct localhost runtime URL was supplied to detect accidental binding;
- no AWS/RDS or real project database was contacted.

| Verification | Result |
| --- | --- |
| Legacy lock, no `pydantic_ai` installed, two Pydantic modules ignored | 475 passed, 0 skipped |
| Pydantic lock: PoC + Smoke Harness + Evaluation Foundation | 49 passed |
| Smoke Harness suite alone | 32 passed |
| Pydantic aggregate including CI workflow contracts | 56 passed |
| Standalone Legacy evaluation, explicit output | 8/8 cases passed; 0 safety violations |
| JUnit Legacy collection | 475 tests, 0 failures, 0 errors, 0 skipped |
| Python syntax and both dependency checks | passed; no broken requirements |

The standalone evaluation manifest records a `TEST_DATABASE_URL` source,
localhost PostgreSQL, `runtime_database_url_used: false`, independent worker
session visibility, and the approved Dataset hash. Synthetic fixture cleanup
was also exercised after Provider failure.

## Remaining unknowns

- No real DeepSeek request was made. Real support for the exact model,
  authentication, Thinking-off interpretation, Provider-side tool behavior,
  reported usage semantics, pricing, and remote cancellation remains unknown.
- Local `Task.cancel()` cannot prove a remote Provider stopped; the verified
  invariant is local result discard, task tracking, no downstream Agent tool,
  and fail-closed admission.
- Hosted CI was not dispatched in this authorization. Its existing remote
  evidence remains separate from this local verification.

## Real Provider authorization readiness

The offline Harness is technically ready for a separately authorized bounded
R1/R2/R3 Real Provider smoke run: global maximum four HTTP requests, maximum
2,000 reported tokens, per-request cap, no retries, per-invocation deadline,
privacy gate, read-only tools, and immutable terminal stop state. It is **not
a Real Provider Compatibility Pass**, and no credential or request may be
used until a new explicit authorization defines the allowed request set,
credential handling, cost ceiling, and stop conditions.
