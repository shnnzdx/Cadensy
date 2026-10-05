# Cadensy — PR-03R.1 Bounded Provider Smoke Harness: Offline Validation

**Date:** 2026-10-04
**Scope:** test-only offline Harness, mock transport, isolated disposable PostgreSQL tests, and isolated-PoC CI wiring.
**Result:** **Offline controls verified. Real DeepSeek compatibility remains unperformed and requires separate authorization.**

No real DeepSeek request or API key was used. This work performed no AWS/RDS operation, deployment, production-database operation, Git push/merge, Chat-route integration, PR-04 activity, or Golden Dataset change.

## Changed files

| File | Change |
| --- | --- |
| `backend/app/agents/provider_smoke_harness.py` | New isolated Harness, mock-only HTTP boundary, redacted wire contract, privacy gate, usage ledger, per-invocation deadline wrapper, R1/R2/R3 probes, and failure classifier. It is not imported by a production route or Legacy Runtime. |
| `backend/tests/test_provider_smoke_harness.py` | New R1/R2/R3, HTTP/error, retry, limit, deadline/cancellation, privacy, cross-trip, independent-session, no-write, and fixture-cleanup tests. |
| `.github/workflows/build-validation.yml` | Adds the module to the existing isolated Pydantic-AI fake-compatibility job; Legacy regression remains separate. |
| This file | Offline validation record. |

`22_PR03R_REAL_PROVIDER_PREFLIGHT.md` and the frozen Golden Dataset were not modified.

## Final test-only request configuration

| Control | Implemented setting |
| --- | --- |
| Model and endpoint | `deepseek-v4-flash`; `https://api.deepseek.com` only in an in-process mock client |
| SDK / Agent / tool / output retries | `AsyncOpenAI(max_retries=0)`; all Agent, function-tool, and output retry settings are explicitly `0` |
| Thinking | `thinking=False`, observed through the real SDK serialization as `reasoning_effort: "none"` |
| Output limit | DeepSeek `max_tokens: 128`, using a test-only profile override to prevent `max_completion_tokens` |
| Structured output | `ToolOutput`; native JSON-schema `response_format` is absent |
| Strict function schema | Explicit `strict=False`. Pydantic AI 2.54.0 omits false instead of serializing it; tests accept this actual behavior and reject unexpected truthy strict values. |
| Streaming | `DeadlineBoundModel.request_stream()` fails closed before an HTTP request |
| Invocation deadline | Every provider `request()` recomputes `min(8 seconds, request_remaining_seconds())` immediately before dispatch |
| Global ledger | One process-wide maximum of four requests and 2,000 reported total tokens; a successful response without numeric usage is a stop, not zero usage |
| Cost/pricing | `reported_cost_usd=null`, `pricing_status="offline_mock_unbilled"`; no mock value is presented as actual Provider billing |

The Harness uses `OfflineRecordingTransport` with `trust_env=False`, so it has no socket-backed transport. Its only SDK credential is the literal non-secret placeholder `offline-provider-placeholder`. Captured evidence contains neither headers nor raw request/response bodies, prompt/reply text, tool argument/result values, provider reasoning, or credentials.

## Captured wire-contract assertions

R1 runs the actual Pydantic AI 2.54.0 → OpenAI SDK 3.24.0 → mock transport chain and asserts this redacted contract:

```text
POST /chat/completions
model = deepseek-v4-flash
reasoning_effort = none
max_tokens = 128
tool_choice = required
native JSON-schema response_format = absent
strict values = absent (explicit false is omitted by the mapper)
unexpected OpenAI-only parameters = none
```

R3 records an important Pydantic constraint: a static `tool_choice="required"` is rejected for an Agent that must subsequently produce a final output, because that would force every later turn. The Harness uses a dynamic first-turn setting, a no-op test-only function tool, and a one-request `UsageLimits` cap. The generated first request still has `tool_choice: "required"` and `reasoning_effort: "none"`; the framework's attempted final turn is rejected before a second HTTP request. This proves the requested first-turn configuration without claiming a full multi-turn required-tool interaction.

## Offline results

| Scenario | Result | Evidence |
| --- | --- | --- |
| R1 — typed clarification | Passed | One request; typed clarification; no Domain tool. |
| R2 — scoped read then typed preview | Passed | Two requests; actual `get_plan_item`; output uses symbolic `scoped_item` only. |
| R3 — non-thinking required choice | Passed | One request; observed `reasoning_effort=none`, `tool_choice=required`; no Domain read/write tool. |
| Fifth request | Passed | Global counter rejects it before mock-handler dispatch. |
| HTTP 400 / 401 / 429 / 503 | Passed | Exactly one request each and stable smoke failure classification. |
| Missing usage / token ceiling | Passed | Stops before Agent output consumption; unknown usage is never treated as free. |
| Invalid tool args / malformed output | Passed | Typed validation stops after one request; zero retry budget. |
| Provider deadline / late completion | Passed | Caller stops locally; late completion is tracked and discarded. |
| Tool deadline | Passed | A blocked independent read tool is stopped before a second Provider turn; its Pydantic timeout is translated to the framework-neutral tool-deadline taxonomy. |
| Local cancellation | Passed | Cancellation does not claim to kill work; a later result is rejected by the execution context. |
| Unexpected stream | Passed | Refused before a new HTTP request. |
| Cross-trip capability | Passed | Foreign membership fails before first Provider request. |
| Privacy / write capability | Passed | Supplied private phrase and membership value, credential-shaped JSON, and write-named tools are rejected before dispatch. |
| Fixture cleanup after failure | Passed | Exact synthetic rows are deleted after a simulated 503; no unrelated rows/schema are removed. |

## Deadline and late-result invariant

`DeadlineBoundModel` does not claim that cancellation can kill accepted remote work. It starts a tracked foreground task, waits only for the remaining local deadline, and, if it expires:

1. marks local cancellation in `AgentExecutionContext`;
2. returns a request- or provider-deadline error without passing a response into the Agent graph;
3. retains only an in-memory task reference until actual completion; and
4. consumes/discards the eventual result or exception without payload tracing, then increments a non-secret late-completion counter.

At test-Harness close, owned late tasks receive one bounded 8-second cleanup window before the SDK client is closed; the Harness records any task still unresolved and does not claim to terminate it. The failure-injection test holds a mock request beyond the deadline, releases it with an event, and proves `late_provider_completions == 1`, `active_late_provider_tasks == 0`, `unfinished_late_tasks_at_close == 0`, no Agent output, and no durable Domain side effect. It is evidence of a local discard invariant, not a promise to terminate the remote provider.

## Privacy, Session ownership, and database isolation

`OutboundPayloadPrivacyGate` executes before admission, capture, and mock-handler dispatch. It rejects credential-shaped JSON fields, supplied sensitive values, and write-capable tool prefixes. R2 supplies the synthetic private constraint phrase and organizer membership value as forbidden values. The membership value is closure-only in `TripReadCapability`; it is not registered in a function-tool schema or in captured wire evidence.

The Harness offers no PlanItem/Proposal/Vote/PlanChange/DecisionRound mutation tool. R2 creates a fresh short-lived Session from the frozen capability factory, binds existing read-only trip tools, and closes the worker Session before returning its safe DTO.

Before DB tests, PostgreSQL readiness was verified at `localhost:5432`. Tests were executed only with a clearly disposable local target named `test_pr03r_smoke`; the runtime-looking URL was set to another synthetic name. Pytest independently enforces a test-only database name before recreating its target. R2 proves two distinct worker Sessions—scope preflight and actual read tool—both independently connected from the request fixture Session and both closed. The committed synthetic rows exist only to demonstrate worker visibility and are deleted by exact identifiers after both success and simulated-provider-failure paths. PlanItem, ChangeProposal, DecisionRound, and Vote counts are unchanged by the Harness.

No production, AWS/RDS, or real project database was contacted.

## Test evidence and CI readiness

Using the existing isolated interpreter and no Provider credential:

```text
pytest -q tests/test_provider_smoke_harness.py
20 passed

pytest -q tests/test_pydantic_ai_poc.py tests/test_provider_smoke_harness.py tests/test_evaluation_foundation.py
36 passed
```

The second command is now the Pydantic-AI CI job contract and uses `requirements-pydantic-ai-poc.lock.txt`, not the Legacy lock. This validates the local command and test isolation only; no GitHub-hosted workflow was dispatched under this authorization.

## Future R1/R2/R3 execution plan — authorization required

Before any real call, independently validate the pinned lock, canonical Dataset provenance, disposable local DB, secret-injection mechanism, and outbound privacy policy. Use one global four-request/2,000-reported-token ledger:

1. **R1 (max 1):** synthetic ambiguous request → typed clarification; no Domain tool.
2. **R2 (max 2):** synthetic trip → only scoped `get_plan_item` → typed preview; independently verify fixture facts, deterministic classification, no writes, and cleanup.
3. **R3 (max 1):** non-thinking first-turn required-tool-choice configuration probe; no Domain read/write.

Stop the entire run after any non-2xx status, unexpected retry/request count, missing or over-limit usage, privacy gate rejection, deadline/cancellation, schema/validation failure, cross-trip exposure, unexpected parameter, or durable side effect. Report numeric usage/cost only if the provider actually returns it; otherwise record `null` / `Unknown`, never a fabricated value.

## Remaining unknowns and verdict

Still unproven: live acceptance of temporary `deepseek-v4-flash`; live non-thinking/tool-choice/max-token/non-strict-tool behavior; returned usage/cost and billing; live error mapping/rate limiting; provider-side cancellation; and whether a real-call context needs extra application-specific redaction values beyond the tested synthetic ones.

**Real-provider authorization readiness: Ready for a separately authorized, bounded attempt; not a compatibility pass and not PR-04 readiness.** The Legacy Runtime remains authoritative. A future authorization must explicitly allow at most four real calls and specify the approved secret-injection mechanism and stop conditions.
