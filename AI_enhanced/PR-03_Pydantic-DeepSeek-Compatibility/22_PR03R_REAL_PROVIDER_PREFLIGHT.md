# Cadensy — PR-03R Real DeepSeek Compatibility Preflight

**Date:** 2026-10-04
**Scope:** local source and SDK inspection, official-document research, and a bounded-test design only.
**Result:** **Conditionally Ready** for a separately authorized, four-request real-provider compatibility test. This document does **not** authorize or perform a DeepSeek request, API-key use, deployment, migration, AWS/RDS access, a runtime replacement, or PR-04 work.

## 1. Repository baseline and scope controls

`git fetch origin --prune` completed before this review. At that point, both the new feature worktree's `HEAD` and `origin/main` resolved to:

```text
8c9d12b681828bbe511b5d0bbc3b1f82a5b8e194
```

This is the accepted PR #2 merge commit. The feature branch is local only:

```text
feature/pr03r-deepseek-compatibility
```

It was created at the verified `origin/main` commit in the independent worktree:

```text
C:\Users\zdxzh\Desktop\capstone\New-pr03r-deepseek-compatibility
```

The original `New` worktree contained user-owned uncommitted AWS-document changes and the local consolidation report, so it was not reset, cleaned, staged, copied from, or otherwise modified. No branch was pushed and no pull request was created.

`21_REPOSITORY_BASELINE_CONSOLIDATION.md` was found in that original local worktree and cross-checked against the accepted PR #2 merge, closed/non-merged PR #3, post-merge run `37238668239`, required-check names, and the Golden Dataset Git object. Its claims are consistent. It remains an uncommitted, separate documentation follow-up and is intentionally not copied into this feature worktree.

The accepted post-merge CI evidence remains:

| Scope | Accepted hosted result |
| --- | --- |
| Legacy backend | 474 passed |
| Pydantic AI fake compatibility | 16 passed |
| Frontend | 13 passed |
| Golden evaluation | 8/8 passed |

No fake database test was re-run in this preflight. The isolated worktree has no independently demonstrated disposable PostgreSQL target, and no source change requires it. The accepted GitHub-hosted fake result is recorded as historical evidence, not as a real-provider result.

## 2. Read sources and actual dependency closure

The design/evaluation reports `03`, `04`, `05`, `07`, `14`, `15`, `16`, `18`, and `19` were reviewed together with the current agent, tool, execution, chat-service, evaluation, and PoC-test sources named in the authorization.

The repository-owned PoC lock and the installed isolated PoC interpreter agree:

| Component | Verified value |
| --- | --- |
| Python | 3.13.5 (lockfile target) |
| `pydantic-ai-slim[openai]` | 2.54.0 |
| OpenAI SDK | 3.24.0 |
| Pydantic | 2.13.4 |
| SQLAlchemy | 2.1.3 |
| Current configured model ID | `deepseek-v4-flash` |

No framework package was installed, upgraded, or removed during this work.

The Golden Dataset content was not modified. Its canonical Git-object SHA-256 is the approved value:

```text
e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

On this Windows checkout, `core.autocrlf=true` expands the file to CRLF and therefore produces a distinct working-file byte hash (`b567f144…714172`). `git show origin/main:<dataset-path>` has LF bytes and produces the approved hash; `git diff` reports no Dataset change. A future local smoke report must record the canonical Git-object hash plus its line-ending provenance rather than treating this checkout conversion as a Dataset version bump.

## 3. Existing safety boundary remains authoritative

The current PoC is isolated: `backend/app/agents/pydantic_poc.py` is not imported by the HTTP route or by the Legacy Custom Runtime. It contains only model-proposed `SuggestedAction`; it has no write tool and no access to PlanItem, Proposal, Vote, or application commands.

`TripReadCapability` is frozen and accepts a session factory rather than a request `Session`. Both PoC tools open and close a fresh worker-owned Session, then bind existing `build_read_only_trip_tools(...)` to an immutable trip ID and membership ID. Those closures first check membership-to-trip binding. The accepted fake tests prove an independent Session is created, uses a separate connection, reads synthetic facts, and closes.

The production Legacy path in `domain/chat/service.py` separately creates `SessionLocal()` inside the worker; it does not capture the request Session. `execution.py` correctly distinguishes a local request cancellation from a running synchronous worker's actual completion. A late result is discarded and recorded; Python cannot force-stop an already-running synchronous thread or a remote request that a provider has already accepted.

PR-03R must preserve all of those boundaries. The future real smoke harness must run directly as a short foreground async process—no streaming, thread executor, background task, HTTP route, write tool, or production `SessionLocal` binding.

## 4. Official DeepSeek state as of 2026-10-04

DeepSeek's current official documentation names `deepseek-flash` as the canonical Flash model. It states that the retired `deepseek-v4-flash` identifier is still accepted temporarily, routed to DeepSeek-V4.1-Flash, and billed at Flash rates. The repository must **not** silently change the model ID in this PR.

This matters because the exact Pydantic AI 2.54.0 `DeepSeekProvider` source explicitly recognizes `deepseek-v4-flash` and gives V4 models the correct thinking/tool-choice profile. It does not list the newer `deepseek-flash` alias in `DeepSeekModelName`; switching aliases with the current pin would therefore need its own source-level compatibility review. The bounded test should retain the current alias only to test the existing configuration, while recording that its provider routing is temporary.

DeepSeek documents an OpenAI-compatible Chat Completions base URL of `https://api.deepseek.com`. Pydantic AI's `DeepSeekProvider` uses that base URL and `OpenAIChatModel` calls `client.chat.completions.create(...)`. This is source-verified construction, not a server acceptance result.

### Thinking, tools, and output format

The current PoC constructs the model with `OpenAIChatModelSettings(thinking=False)`. In Pydantic AI 2.54.0, the unified setting is translated to Chat Completions `reasoning_effort='none'`; DeepSeek documents `none` as disabling thinking. Pydantic's DeepSeek profile also records that V4 models default to thinking and permits forced tool choice only when thinking is off. Real acceptance of that complete wire request remains unverified.

DeepSeek documents that `required` and named `tool_choice` values return HTTP 400 in thinking mode; non-thinking mode is required for that probe. Tool calling itself is documented in both modes. Pydantic AI preserves DeepSeek `reasoning_content` between tool turns when thinking is enabled, but this bounded test will keep thinking off so it does not expose or retain reasoning content.

The present PoC uses `ToolOutput`, not Chat-Completions native JSON-schema `response_format`. That is the correct conservative shape: the exact Pydantic provider profile states that DeepSeek Chat Completions rejects native JSON-schema output, whereas DeepSeek documents JSON-object output and function tools. Typed validation still occurs locally in Pydantic before Cadensy consumes an output.

DeepSeek strict function schemas are a beta feature requiring `https://api.deepseek.com/beta`; normal Chat Completions must not be used as evidence that strict mode is supported. The current PoC leaves `strict=None`. Its Pydantic OpenAI mapper emits a `strict` property only when it is truthy. The future bounded test must explicitly set `strict=False` for the read-only function and both `ToolOutput`s, then rely on Pydantic argument/output validators. A separate beta-endpoint/strict-schema experiment is out of scope.

## 5. Compatibility matrix

Status values mean: **Documented** = official provider documentation; **Source Verified** = exact local Pydantic AI 2.54.0/OpenAI 3.24.0 source inspection; **Fake Model Verified** = accepted isolated fake tests; **Requires Real Provider Verification** = not proven without an outbound request; **Unsupported** = known incompatible for this route; **Unknown** = not established.

| Contract | Status | Evidence / consequence |
| --- | --- | --- |
| `deepseek-v4-flash` request ID | Documented; Requires Real Provider Verification | Officially a temporary accepted compatibility ID routed to V4.1 Flash. Retain it for this test; do not silently switch to `deepseek-flash`. |
| `DeepSeekProvider` + `OpenAIChatModel` + Chat Completions | Documented; Source Verified; Requires Real Provider Verification | Exact provider base URL and `chat.completions.create` path were inspected. Construction alone proves no server acceptance. |
| Frozen typed dependencies / independent read Session | Fake Model Verified | Existing PoC test proves a factory-owned Session and real synthetic trip read; no request Session is passed. |
| `thinking=False` | Documented; Source Verified; Requires Real Provider Verification | Pydantic maps it to `reasoning_effort='none'`; DeepSeek documents that this disables thinking. R3 verifies the whole wire contract. |
| Read-only function tool argument validation | Fake Model Verified; Requires Real Provider Verification | Pydantic rejects invalid typed arguments locally. R2 confirms server-side function-call wire compatibility with `strict=False`. |
| `ToolOutput` discriminated preview / clarification | Fake Model Verified; Requires Real Provider Verification | Validated with `FunctionModel`; R1 and R2 verify actual provider tool-output shape. |
| Forced `tool_choice` | Documented; Source Verified; Requires Real Provider Verification | Disallowed with thinking, supported by the source profile when V4 thinking is off. R3 is the one-request probe. |
| Native Chat-Completions JSON-schema output | Unsupported | Exact Pydantic DeepSeek profile documents that this route is rejected. Do not use it for the smoke test. |
| JSON-object mode | Documented; Requires Real Provider Verification | Not needed by the `ToolOutput` test and therefore intentionally not exercised. |
| Strict function JSON schema | Documented (beta endpoint only); Source Verified; Not in bounded test | Do not claim support for the normal endpoint. Future test uses explicit `strict=False`. |
| Output-token parameter mapping | Source Verified; Requires test-only profile override and Real Provider Verification | DeepSeek documents `max_tokens`; the current Pydantic DeepSeek profile leaves `openai_chat_supports_max_completion_tokens` unset, whose mapper default is `True`. Set an explicit profile override to `False` before using the 128-token cap. |
| Per-invocation provider timeout | Source Verified; Requires minimal test-only adapter | Pydantic forwards `model_settings['timeout']` to each Chat Completions call, but the current PoC calculates its value only once before `agent.run()`. |
| OpenAI SDK retries | Source Verified; unsafe in current builder | SDK default is `max_retries=2`; current `build_deepseek_chat_model()` lets `DeepSeekProvider(api_key=...)` create that default client. A real test must inject an `AsyncOpenAI(..., max_retries=0)` client. |
| Request/tool/token limits | Source Verified; Fake Model Verified; Requires Real Provider Verification | Pydantic enforces request/tool counts and checks returned token limits. Provider-reported usage and a cross-case aggregate ledger still need real evidence. |
| HTTP 401 / 429 / 5xx mapping | Fake Model Verified; Requires Real Provider Verification | Current mappings are synthetic only. Any real non-2xx response stops the test. |
| Token/cost metadata | Documented; Requires Real Provider Verification | DeepSeek documents usage fields. Record returned usage exactly; Pydantic cost may be `null` for a newer/routed model. |
| `openai_user`, `openai_store`, OpenAI service tiers, logprob options | Unsupported / unused for this test | Do not transmit a user identifier, raw personal data, OpenAI-only storage settings, or nonessential provider-specific parameters. |

## 6. Preflight findings that require a minimal test-only harness

### A. Provider deadline is not currently per invocation

`run_structured_preview_poc()` calls `execution_context.before_provider()` once, then passes one timeout value into `agent.run(...)`. A tool turn, output-validator retry, or second model request can therefore inherit a stale initial timeout. This is not a per-provider-call deadline guarantee.

For the future smoke harness only, wrap the real `OpenAIChatModel` in Pydantic AI's `WrapperModel`. Immediately before every delegated `request(...)`, copy its model settings and set:

```text
timeout = min(8 seconds, execution_context.request_remaining_seconds())
```

Then call `after_provider()` before allowing the result to enter the agent loop. The wrapper must implement the equivalent guard for `request_stream(...)` even though streaming is prohibited, so it fails closed if mistakenly invoked. This provides a verifiable per-invocation local deadline without rewriting the Legacy runtime. It does not claim a remote provider was forcibly stopped.

Use a foreground `asyncio.timeout(...)` outer request deadline, not `run_agent_with_deadline()`; the latter deliberately uses a bounded thread executor and the authorized smoke test forbids background execution. On local cancellation, record `local_cancellation_requested=true` and `remote_completion=unknown`, discard all output, and run no tool/write path afterward.

### B. `PlanItemOutput.start_hour` is aligned today

The actual SQLAlchemy `PlanItem.start_hour` declaration is `Mapped[float]` with no `nullable=True`; `_safe_item(...)` returns that value directly. `PlanItemOutput.start_hour: float` is therefore currently aligned. `duration_min`, not `start_hour`, is nullable and is explicitly marked as assumed when absent. No unknown or unscheduled time is fabricated by this PoC.

The future harness must preserve this check as a static contract test. If a future schema permits nullable `start_hour`, the Pydantic DTO and deterministic clarification/unscheduled behavior must be redesigned before a real provider sees the field.

### C. Failure taxonomy is not yet normalized

`classify_poc_exception()` emits implementation-specific labels such as `provider_auth_failed`, `provider_rate_limited`, and `usage_limit_exceeded`. PR-02's frozen runner uses case-level labels (`provider_failure`, `tool_failure`, `cross_trip_denied`), while the A/B plan's required taxonomy uses `provider_timeout`, `provider_rate_limit`, `provider_5xx`, `provider_compatibility`, `malformed_output`, `output_validation`, `usage_limit`, and others. There is no shared enum or mapping today.

Do not change the Golden Dataset in PR-03R. The future smoke report should write both a non-secret technical detail and a normalized evaluation class:

| Event | Evaluation class | Technical detail retained |
| --- | --- | --- |
| local/provider timeout | `provider_timeout` | stage only, no payload |
| HTTP 401/403 or unsupported request parameter/schema | `provider_compatibility` | numeric status / safe error category |
| HTTP 429 | `provider_rate_limit` | numeric status |
| HTTP 5xx | `provider_5xx` | numeric status |
| invalid function arguments | `tool_schema` | tool name only |
| malformed provider response | `malformed_output` | Pydantic exception family only |
| output validator rejection | `output_validation` | validator stage only |
| request/tool/token/cost limit | `usage_limit` | limit name and counters |
| any unmapped Pydantic failure | `unexpected_exception` | exception class only |

An authorization for the actual test should include this test-only mapping; normalizing the production/PR-02 implementation is an independent follow-up.

### D. Privacy-safe observation contract

The future report may retain only: case ID, source commit, canonical Dataset hash, lockfile hash, model ID, endpoint host, test configuration flags, request ordinal/count, tool *names* and safe synthetic symbolic references, output kind, deterministic classification, elapsed milliseconds, normalized failure class, HTTP status category, and provider-reported numeric usage/cost.

It must not retain API credentials, Authorization headers, raw prompt/user text, private constraint wording, real or synthetic membership IDs, raw tool arguments/results, model reasoning, raw reply content, or provider response bodies. `evals.runner` temporarily uses `provider_prompts` to grade the fake baseline but strips it from its public observation; the real-provider smoke harness must not collect that field at all.

## 7. Four-request real-provider verification plan — future authorization only

### Common controls

1. Use a new explicitly local PostgreSQL `TEST_DATABASE_URL`, validated before connect as PostgreSQL on `localhost`/`127.0.0.1`/`::1` with a `test_` or `_test` database name. Construct an Engine and session factory directly from that URL; never inherit `DATABASE_URL`, a pytest Session, or `SessionLocal`.
2. Create only the existing uniquely identified synthetic fixture through the established evaluation fixture helper. Verify cleanup by exact owned IDs and before/after durable counts. No schema migration, AWS/RDS connection, production database, or real user data is allowed.
3. Before any outbound request, run local assertions: independent worker connection; scoped membership read; foreign membership fails before agent invocation; no private phrase/IDs in the candidate observation; immutable `TripReadCapability`; no write-capable tool registered.
4. Construct an `AsyncOpenAI` client from a future process-injected secret with `base_url='https://api.deepseek.com'`, `max_retries=0`, and a fixed **8-second provider timeout**. Pass it as `DeepSeekProvider(openai_client=client)`. Construct `OpenAIChatModel` with the narrow profile override `OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False)`, which merges with the provider's existing DeepSeek profile and makes Pydantic send DeepSeek's documented `max_tokens` field for the output cap. Do not call the existing `build_deepseek_chat_model()` for the real test because it neither sets `max_retries=0` nor this output-token mapping.
5. Use the current model ID `deepseek-v4-flash`, `thinking=False`, no streaming, no background execution, `strict=False` on every function/output tool, agent/tool/output retries set to zero, and `max_tokens=128` per provider request.
6. Maintain one process-local global ledger across all cases: four maximum outbound requests, 2,000 maximum reported total tokens, and the remaining requests/tokens must never reset on an agent retry or a new case. Each individual `UsageLimits.request_limit` is at most that case's remaining budget and has explicit tool/token limits.
7. Pydantic's token limits are checked after a provider response for Chat Completions. Therefore the 2,000-token figure is an enforced operational stop limit, not a guarantee that an oversized first response was never billed. The small fixed output cap, short synthetic prompts, no retries, and immediate post-response ledger check bound this exposure. Do not represent it as an absolute pre-billing cap.

| Case | Purpose and expected safe result | Max outbound requests | Per-case limits |
| --- | --- | ---: | --- |
| R1 | No Cadensy-domain-tool typed clarification. The request intentionally lacks a target time and must return `ModelClarification`; it proves typed output, not itinerary quality. | 1 | `request_limit=1`; permit one output tool; `max_tokens=128`. |
| R2 | Synthetic scoped trip: the model calls only `get_plan_item`, which opens an independent short-lived Session, then returns a typed `ModelChangePreview`. Verify returned symbolic item maps to fixture facts, the deterministic classifier runs on those facts, and durable counts are unchanged. | 2 | `request_limit=2`; one read tool + one output tool only; tool deadline 2 seconds; `max_tokens=128` per call. |
| R3 | Provider-settings probe: non-thinking plus `tool_choice='required'`, with a no-domain-tool typed clarification output. It tests the documented restriction boundary without issuing another read or write. | 1 | `request_limit=1`; one output tool; `max_tokens=128`. |

R1's and R3's typed output tools are transport/output functions, not Cadensy domain tools. R2 is the only case that may read data, and its factory closes the worker Session before its tool result returns to the model. A real-provider success proves narrow protocol compatibility only; it is not a Golden Dataset quality score, a model comparison, or PR-04 readiness.

## 8. Usage and cost ceiling

DeepSeek's current published peak Flash rates are $0.30 per million cache-miss input tokens and $1.20 per million output tokens. The official page says the legacy `deepseek-v4-flash` alias is billed at Flash rates. Under the 2,000-token total operational budget, the deliberately conservative all-output peak-rate ceiling is:

```text
2,000 / 1,000,000 × $1.20 = $0.0024 USD
```

The smoke report must record actual provider usage fields and calculate cost from the current official rate table at execution time. If the price/model routing has changed, or the provider/Pydantic cost value is unavailable, record `cost: null` / `pricing_status: Unknown`, stop before any additional optional case, and do not invent a cost. Recheck the official pricing page immediately before the future test.

## 9. Explicit stop conditions

Stop the entire test plan immediately—do not consume remaining requests—if any of the following occurs:

1. Any automatic HTTP retry is observed or the SDK is not demonstrably configured with `max_retries=0`.
2. A provider rejects/ignores the fixed non-thinking, tool-choice, schema, timeout, or `max_tokens` contract; a tool/output schema is malformed; or typed validation needs a retry.
3. A tool accesses a foreign trip, receives a mismatched membership binding, exposes an item outside the synthetic trip, or opens a non-independent Session.
4. Any candidate trace/report includes a credential, Authorization header, private constraint text, raw prompt/reply, identity, raw tool payload, reasoning content, or provider response body.
5. A provider call reaches its 8-second timeout, the outer local request deadline expires, or a cancellation leaves an output eligible for consumption.
6. The global request count would exceed four, reported usage reaches/exceeds the budget, a limit is exceeded, or price/model routing cannot be verified at execution time.
7. Any durable PlanItem, Proposal, Vote, PlanChange, or DecisionRound side effect occurs other than the exact synthetic fixture setup/cleanup rows.
8. A non-2xx response, including 401, 429, or 5xx, occurs. Record only safe status/category metadata and stop; do not retry.

## 10. Remaining unknowns and authorization request

The following remain deliberately unproven until a real provider is separately authorized: actual acceptance of the temporary legacy model ID; actual non-thinking/forced-tool-choice behavior; actual Chat-Completions ToolOutput schema compatibility; returned usage shape; live error mapping; provider cancellation behavior after local cancellation; and live cost metadata.

**Requested future authorization, not granted by this document:** authorize one local, non-production PR-03R smoke harness that implements the test-only controls in Section 7, accepts a DeepSeek credential only through a separate process environment/approved secret mechanism, runs at most R1/R2/R3's four total outbound calls, and emits the redacted observation contract in Section 6D. Do not paste a credential into chat, source, Git, or a report.

## 11. Verdict and changed-files summary

**Bounded real-provider compatibility verdict: Conditionally Ready.** The framework pin, typed read-only boundary, fake evidence, official provider documentation, price bound, and test shape are sufficient to prepare a controlled smoke test. It is conditional on a future test-only harness that fixes the stale per-invocation deadline, disables SDK/Pydantic retries, uses explicit non-strict tools, maintains a global ledger, verifies a disposable database at runtime, and receives separate real-provider authorization.

**PR-04 verdict: Not Ready / blocked by authorization and evidence.** No production route can select Pydantic AI until real-provider smoke evidence is accepted, the taxonomy boundary is normalized, the temporary model-ID decision is explicitly resolved, and a separately authorized integration/A-B phase completes.

Changed in this preflight worktree:

| File | Change |
| --- | --- |
| `AI_enhanced/phase-0-audit-2026-10-03/22_PR03R_REAL_PROVIDER_PREFLIGHT.md` | Added this preflight report. |

No business source, dependency lock, Golden Dataset, production runtime, database, credential, remote branch, or GitHub configuration was changed.

## Sources consulted

- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/) — accessed 2026-10-04.
- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/) — accessed 2026-10-04.
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) — accessed 2026-10-04.
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) — accessed 2026-10-04.
- [Pydantic AI 2.54.0 `DeepSeekProvider` source](https://github.com/pydantic/pydantic-ai/blob/v2.54.0/pydantic_ai_slim/pydantic_ai/providers/deepseek.py) — inspected against the local pinned package.
- [Pydantic AI usage limits API](https://pydantic.dev/docs/ai/api/pydantic-ai/usage/) — accessed 2026-10-04.
