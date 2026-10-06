# Cadensy PR-04 Step 1 — Runtime Contract Review

## 1. Current GitHub baseline

This review inspected `origin/main` after `git fetch origin --prune`.

| Check | Observed result |
| --- | --- |
| Current `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| PR-03R | [PR #4](https://github.com/shnnzdx/Cadensy/pull/4), merged |
| Required checks configured when inspected | Strict: `Backend — Legacy Full Regression`, `Pydantic AI — Isolated Fake Compatibility`, `Frontend — Regression and Builds` |
| Current/default Chat-code boundary (not deployment evidence) | No Chat/API import of `pydantic_poc`, `provider_smoke_harness`, or live-smoke runners |
| Current/default Chat code path (not an AWS deployment claim) | `respond_to_trip_chat → _run_chat_agent_with_timeout → base.call_agent` |
| Frozen dataset | V1 canonical SHA-256 `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f` |

The bounded PR-03R result is protocol evidence only; it is neither production
readiness evidence nor a claim of runtime or model superiority. Required-check
configuration was inspected through the GitHub branch-protection API at review
time, while the merged PR/run results are repository history. This review did
not query or verify an AWS deployment. It made no business-source, dependency,
Provider, route, configuration, or dataset change.

## 2. Current Legacy Runtime actual contract

`respond_to_trip_chat(db, trip_id, membership, message, item_id, history)` first
performs deterministic request-session work: membership/trip scope and trip
existence checks, Current Plan lookup, selected-item lookup, item-reference
resolution, deterministic missing-information clarification, explanations, and
history-based candidate-option selection. Those responsibilities already exist
outside the model loop and are not a Runtime migration target.

`base.call_agent(...)` then accepts system/user text, bounded user/assistant
history, `AgentTool` instances or one deferred tool factory, provider route,
round/token/output limits, guard-rejection limit, and optional PR-01C deadline
context. `AgentTool` contains name, model-visible JSON schema, handler, optional
guard, and cache policy. Provider configuration is resolved internally through
the Legacy catalog; API credentials and model wire settings are not Chat input.

The actual worker receives only immutable trip/actor identifiers, message, and
history. It opens `SessionLocal()` itself, builds `build_read_only_trip_tools`
inside that worker Session, and closes it at actual worker completion. The
request Session and request-owned ORM membership are not captured.

`AgentRunResult` is broader than what the application uses:

| Runtime field | Chat Service consumption |
| --- | --- |
| `content` | Sanitized into a reply; any claim that a plan already changed becomes safe pending wording. |
| `tool_results` | Converts `propose_options` results to candidate options and `classify_change` result to a proposed preview. |
| `stopped_reason` | Causes safe degradation with no preview. |
| `rounds`, `trace_id`, `total_tokens`, `total_elapsed_ms` | Runtime/telemetry evidence; not part of current `ChatResult` or Domain classification. |

The public application result is `ChatResult(reply, proposed_change, candidate_options)`.
`ProposedChatChange` contains an item, patch, and **deterministic** verdict.
The model does not own notice/round/confirm, proposal creation, voting, or apply.

`classify_change` has two deliberately distinct uses in the current Legacy
path. Inside `build_read_only_trip_tools`, the Runtime may consult a scoped,
read-only `orchestrator.classify_change(...)` result to ground a reply or expose
candidate information. That is a Runtime-visible observation, not the
authoritative preview verdict. After the tool loop,
`_proposed_change_from_agent_classification(...)` finds the scoped current item,
validates the returned item identity and normalized patch against its
request-side scoped item set, then recomputes the deterministic Domain
classification in the request Session to construct `ProposedChatChange.verdict`.
Recomputing classification is not a fresh authoritative reread, snapshot reload,
optimistic-concurrency, or revision-validation guarantee. A migrated Runtime may
retain the bounded read-only capability for grounding/explanation, but it never
owns the verdict; mutation remains outside the Runtime.

## 3. Current Pydantic AI actual contract

`app.agents.pydantic_poc` is isolated and not imported by a route, the Legacy
runtime, or Chat Service. It proves a narrow typed/read-only shape:

| Concern | Actual PoC contract |
| --- | --- |
| Dependency | Frozen `TripReadCapability(trip_id, actor_membership_id, session_factory)` |
| Session ownership | Every typed function tool opens/closes a Session from the factory; no request Session is accepted. |
| Read scope | Delegates to existing `build_read_only_trip_tools` and therefore retains trip/membership checks. |
| Output | Discriminated `ModelClarification` or `ModelChangePreview`. |
| Suggested action | `SuggestedAction(kind="move_time", item_id, new_start_hour)`; a model proposal only. |
| Validation | Strict typed tool argument, output validator requiring same-run item read, bounded `ModelRetry`. |
| Limits | Pydantic `UsageLimits` request/tool limits. |
| Execution | Optional PR-01C `AgentExecutionContext` and `run_agent_with_deadline`. |
| Provider | Test-only DeepSeek model construction with thinking disabled; no route call. |

Framework-specific elements are Pydantic AI `Agent`, `RunContext`, `ToolOutput`,
`ModelRetry`, `UsageLimits`, `RunUsage`, OpenAI model/provider classes, and
provider settings. Framework-neutral concepts are clarification, suggested
action, tool trajectory, redacted observation, usage counters, and normalized
failure.

## 4. Domain, Application Service, Runtime, Provider, and test-only matrix

| Concern | Owner |
| --- | --- |
| Item reference, selected-item and history-option interpretation | Chat Application Service |
| Deterministic known-fact clarification | Chat Application Service |
| Natural-language call, tool loop, output syntax | Runtime |
| Read-only trip access | Scoped Application capability and Tool layer |
| Tool schema/argument validation | Runtime adapter, backed by Tool authorization |
| Suggested action syntax | Runtime |
| Runtime-visible read-only change-classification observation | Scoped read capability / Tool layer |
| Authoritative preview classification, notice/round/confirm, Organizer policy, apply | Application revalidation plus Domain |
| Provider model ID, HTTP settings and retry | Provider composition |
| Request/provider/tool deadline, cancellation, late discard, trace envelope | Shared execution infrastructure |
| Token usage | Runtime observation, normalized by Application |
| SQLAlchemy Session lifecycle | Capability/Tool implementation |
| Pydantic/OpenAI/DeepSeek types | Pydantic adapter / Provider only |
| Smoke ledger, R1/R2/R3 helpers, cost estimates, live runners | Test-only |

The Domain layer must not import Pydantic AI, OpenAI SDK, DeepSeek wire types,
or Provider-specific settings.

## 5. Minimum common Runtime Interface

The interface begins after deterministic Chat preflight and stops before
authoritative preview classification or apply. A Runtime may make a bounded
read-only classification observation through its injected capability. The common
contract characterizes the current Legacy request-side validation/recomputation
first; an explicit fresh scoped reread before authoritative preview
classification is a **future desired contract / later hardening**, not current
Legacy behavior or a PR-04A requirement:

```text
Chat Application Service
  -> ChatAgentRuntime.run(RuntimeRequest, ReadTripCapability, ExecutionContext)
  -> RuntimeResult
  -> server validates scoped item identity + safe patch against request-side set
  -> server recomputes deterministic Domain classification and builds preview
```

The minimum immutable `RuntimeRequest` is: message; bounded history; optional
server-resolved `selected_item_ref` (a semantic hint only); server-generated
`request_id`; and explicit `RuntimeLimits`. It deliberately carries neither
trip nor actor authority. Provider credentials, model configuration, a request
SQLAlchemy Session, ORM entities, FastAPI Request, and mutable state are also
absent.

`ReadTripCapability` is the single authority source injected separately. It
holds immutable trip scope, immutable actor scope, scoped read/classify methods,
and an internally controlled Session factory. It exposes no generic query,
commit, write, or request-Session API. `selected_item_ref` is never an
authorization boundary and cannot grant access to another trip. The existing
`AgentExecutionContext` remains a separate non-serializable capability.

The output is a discriminated business union plus a separate observation:

```text
RuntimeResult(
  reply: str,
  outcome: AgentClarification
         | AgentSuggestedChange(item_ref, safe_patch)
         | AgentReplyOnly,
  candidate_options: tuple[AgentCandidateOption, ...],
  observation: RuntimeObservation(usage?, redacted_trace, failure?)
)
```

Every normal successful Runtime result therefore preserves a non-empty
user-visible `reply`: clarification wording is explicit in `reply`, and a
suggested change can coexist with that reply and candidate options. Suggested
change means model syntax only. Current Legacy validates the scoped item/patch
against its request-side set and recomputes deterministic Domain classification
to create preview metadata; it does not thereby provide a fresh reread or
concurrency guarantee. A Runtime result never includes a revision, verdict,
proposal ID, vote, or apply authority.

Candidate options are an orthogonal optional collection, not a mutually
exclusive output variant: the Legacy Runtime can return a suggested change and
candidate options in the same run. Fuzzy/exact selection, history lookup,
stale-option replies, and conversion to `ProposedChatChange` remain
deterministic Chat Service behavior. PR-04 must not force Pydantic parity for
every Legacy option heuristic before core clarification/change-preview behavior
is proven.

## 6. Framework-neutral failure contract

Store both a normalized evaluation label and a non-secret technical classifier;
never store raw Provider bodies, prompts, headers, tool arguments, or results.

| Event | Normalized label | Safe detail |
| --- | --- | --- |
| Provider/request deadline | `provider_timeout` / `request_timeout` | timeout source |
| 401/403, 429, 5xx | `provider_auth`, `provider_rate_limit`, `provider_5xx` | status class |
| Unsupported model/wire setting | `provider_compatibility` | exception family/status |
| Malformed output / validator exhaustion | `malformed_output` / `output_validation` | parser or validator stage |
| Tool deadline / schema rejection | `tool_timeout` / `tool_schema` | tool name only |
| Foreign trip scope | `cross_trip_denied` | scope denied |
| Request/tool/token budget | `usage_limit` | named limit/counters |
| Local cancellation | `request_cancelled` | local signal only |
| Other | `unexpected_exception` | exception class only |

Legacy `stopped_reason`, Pydantic exceptions, and PR-03R test labels map at
adapter boundaries. The frozen Golden Dataset is not edited for either runtime.

## 7. Shared execution and Session ownership

Both adapters must use PR-01C's request deadline, remaining provider budget,
tool deadline, local cancellation, bounded worker admission, late-result
discard, and redacted lifecycle envelope. Legacy already does so through its
worker-created `SessionLocal` and deferred tool factory. A Pydantic adapter
must use the same outer boundary and open a short-lived worker Session from the
capability for each tool invocation.

Cancellation never proves a running sync worker or remotely accepted request
was terminated. The testable invariant is isolated ownership, no late-result
consumption or mutation, cleanup at actual completion, bounded admission, and
traceability.

## 8. Feature Flag placement (design only)

| Option | Decision |
| --- | --- |
| FastAPI Route reads flag | Reject: routes should not own Agent orchestration. |
| Domain Chat Service reads flag | Reject: Domain/Application logic should not depend on environment configuration. |
| Application/runtime composition reads flag | **Recommend**: one factory injects a Runtime and is replaceable in tests. |

The eventual parser accepts only `legacy` and `pydantic`; absent means `legacy`.
Invalid configuration fails closed during composition. A runtime failure must not
silently run the other adapter, because that contaminates A/B evidence and can
duplicate Provider/tool work.

## 9. Fair A/B evaluation

Both adapters must use the same frozen Golden Dataset, synthetic fixture,
disposable-DB boundary, deterministic classifier, grader version, failure
taxonomy, and privacy rules. The report records dependency lock, source/diff
hash, prompt/tool-contract/grader versions, and case-level outcomes.

| Dimension | Measures |
| --- | --- |
| Correctness | pass rate, allowed clarification, item/reference resolution, change-preview, tool arguments, decision path |
| Safety | cross-trip denial, injection/privacy, write attempt, durable side effect, trace redaction |
| Reliability | malformed output, validation, Provider/tool failure, timeout, cancellation, unexpected exception |
| Efficiency | Provider requests, tool calls, reported usage, latency; unknown values remain `null` |
| Engineering | orchestration/parsing/framework-glue LOC, error translations, runtime branches, contract-test count |

Real Provider evaluation is manual and bounded; it is never a default PR CI gate.

### Observation-oracle caveat before a fair A/B claim

The frozen Legacy baseline remains historical evidence and is not rewritten in
this review. However, current `backend/evals/runner.py::_run_legacy_case`
derives some observed output kinds from `expected_business_outcome` (including
the `ask_for_...` clarification branch, and the analogous read-only fallback
branch). That is oracle leakage: expected labels must be consumed only by the
grader, never used to construct an observed result. Before PR-04D claims a fair
A/B comparison, normalized observed output must instead derive from actual
Runtime/Application behavior. Correcting that observation extraction is not a
Golden Dataset change, but it requires a separately reviewed later PR; it is
not authorized or implemented here.

## 10. PR-03R reuse review

| Potentially shared after separate review | Must remain test-only |
| --- | --- |
| PR-01C execution/deadline semantics; immutable scoped-read capability concept; normalized usage/failure DTO concepts; redacted observation shape | `ProviderSmokeHarness`; global smoke ledger; R1/R2/R3 scenarios; `compatibility_probe`; live runners; mock transport; fixed wire assertions; smoke cost calculations |

Do not import or copy `provider_smoke_harness.py` as production orchestration.
A production adapter may use a newly reviewed small abstraction, never the
compatibility Harness itself.

## 11. Proposed minimum PR decomposition

| PR | Scope | Acceptance |
| --- | --- | --- |
| PR-04A | Add framework-neutral DTOs, `ChatAgentRuntime` Protocol, failure/observation mapping, direct Legacy adapter characterization. No selector/route change. | Legacy public behavior and Golden baseline unchanged. |
| PR-04B | Add isolated Pydantic adapter behind the interface. No production selector. | Fake/failure-injection parity; no writes or request-Session sharing. |
| PR-04C | Add composition-layer selector and validated `CHAT_AGENT_RUNTIME`, default Legacy. | Invalid flag fails closed; no silent cross-runtime fallback. |
| PR-04D | Shared evaluation runner and A/B evidence only. | Same fixtures, graders, taxonomy; no automatic rollout. |

This keeps contract stabilization, framework integration, configuration, and
evidence in reviewable/revertible units instead of one large migration.

## 12. Compatibility table

| Concern | Legacy today | Pydantic PoC today | Common proposal | Risk |
| --- | --- | --- | --- | --- |
| Input | Prompt/history/tools/limits | message/typed deps/test model | immutable request + capability + limits | Legacy selected context is prompt text |
| Output | non-empty content; preview plus options may coexist | typed clarification/preview | required user-visible reply + primary discriminated outcome + orthogonal candidate-options collection + observation | candidate-option/reply parity |
| Tools | handwritten schemas/guards/cache | typed Pydantic functions | read capability + typed boundary | schema/guard differences |
| Sessions | one worker Session | Session per tool | worker-owned only | lifecycle/visibility |
| Retries | fallback + loop limits | ModelRetry + UsageLimits | explicit adapter policy | hidden request/cost change |
| Timeout | PR-01C loop boundary | optional context wrapper | shared execution | multi-turn remaining budget |
| Validation | guards + service parsing | typed args/output validator | server scope/patch validation | types are not authorization |
| Usage/trace | aggregate tokens/rounds | RunUsage/safe observation | nullable usage/redacted trace | Provider differences |
| Failures | stopped reason/exceptions | partial classifier | normalized + technical failure | taxonomy drift |
| Clarification/action | shortcuts + read-only tool classification | typed output/action | reply-backed clarification/proposal syntax only; Service recomputes deterministic verdict against request-side set | Domain authority leakage |

## 13. Major migration risks

1. Legacy candidate-option/follow-up behavior is wider than the Pydantic PoC.
2. Pydantic retries, tool loops, and settings can change request count, cost,
   latency, and error behavior unless explicitly constrained.
3. Typed output validates shape, not authorization, item freshness, decision
   path, revision, or transactional apply.
4. Request-Session leakage would violate PR-01C; fixture visibility and scoped
   worker-session tests must remain.
5. Current Legacy recomputation does not include a fresh item reread, snapshot
   reload, optimistic concurrency, or revision-validation contract. Any explicit
   fresh scoped reread before preview classification is later hardening, not a
   PR-04A behavior-preservation change.
6. PR-03R proves narrow protocol compatibility, not production reliability,
   billing correctness, model quality, or arbitrary multi-turn behavior.
7. The current Legacy evaluation observation extractor has oracle leakage for
   some clarification/fallback labels; no fair PR-04D A/B claim is valid until
   a separately reviewed correction derives observations from behavior.

## 14. Required final answers

### A. Current Legacy Runtime Contract

It is a bounded read-only tool loop over system/user/history text, deferred
trip-scoped tools, Legacy provider configuration, and PR-01C execution context.
It returns content, tool results, rounds, tokens, trace ID, elapsed time, and
stop reason. Chat Service consumes content, tool results, and stop reason only;
deterministic reference resolution and apply stay outside it. The Runtime may
observe a scoped read-only `classify_change` result, but Chat Service
validates returned item identity and normalized patch against its request-side
scoped item set, then recomputes deterministic Domain classification in the
request Session for the preview. This is not a fresh reread or an optimistic
concurrency guarantee.

### B. Current Pydantic AI Contract

It is an isolated typed PoC: immutable `TripReadCapability`, independent
short-lived Sessions, typed read tools, discriminated clarification/change-preview
output, bounded retry/limits, and optional PR-01C execution wrapper. It has no
production Route, selector, write tool, or Domain decision authority.

### C. Minimum common Interface

`ChatAgentRuntime.run(RuntimeRequest, ReadTripCapability, AgentExecutionContext)
-> RuntimeResult`. `RuntimeRequest` has message, bounded history,
semantic selected-item hint, request ID, and limits only; the injected
capability is the sole trip/actor authority. `RuntimeResult` has one
required user-visible `reply`, discriminated clarification/suggested-change/
reply-only outcome, an orthogonal candidate-options collection, and redacted
observation/failure metadata.

### D. Domain versus Runtime

The Runtime may consult a scoped read-only classification observation for
grounding. Chat Application Service owns deterministic reference/selection/
history behavior and validates the item/patch against its request-side scoped
set; it invokes Domain again for deterministic preview classification. Domain
owns notice/round/confirm, Organizer policy, and apply. This current behavior
is not a fresh reread or concurrency guarantee. Runtime owns model/tool
orchestration; Provider owns wire/retry/model settings; test harnesses own smoke
ledgers and scenarios.

### E. Minimum PRs

PR-04A contracts plus Legacy characterization; PR-04B isolated Pydantic adapter;
PR-04C composition selector defaulting Legacy; PR-04D shared A/B evaluation.

### F. Feature Flag placement

An Application/runtime composition factory reads `CHAT_AGENT_RUNTIME`. Routes
and Domain Chat Service do not. Default is `legacy`; invalid values fail closed;
there is no silent cross-runtime fallback.

### G. Legacy behavior preservation

Keep Legacy as default and preserve current Chat Service deterministic preflight
and public `ChatResult` mapping. Use direct adapter characterization, full Legacy
regression, and frozen Golden cases before adding any selector.

### H. Fair A/B Evaluation

Use the same dataset, fixture, disposable DB, deterministic rules, graders,
privacy/safety contracts, and normalized taxonomy. Compare correctness, safety,
reliability, Provider/tool usage, latency, and auditable engineering complexity.
The current frozen Legacy baseline remains evidence, but PR-04D cannot claim a
fair A/B result until observed output extraction stops deriving clarification/
fallback observations from expected outcome labels; those labels belong only to
the grader.

### I. PR-03R reuse versus test-only

Reuse execution/session/capability and normalized-observation concepts after
review. Keep the Smoke Harness, global ledger, symbolic scenarios, compatibility
probe, live runners, fixed wire assertions, and cost calculations test-only.

### J. First genuinely safe implementation step

**PR-04A only:** add additive framework-neutral DTOs and `ChatAgentRuntime`
Protocol with direct Legacy adapter characterization tests. Do not wire a route,
selector, flag, or Pydantic adapter. Acceptance is unchanged externally visible
Legacy behavior under current Golden and regression contracts, including the
two-stage read-only-observation versus deterministic-reclassification boundary
and simultaneous reply/suggested-change/candidate-options representation.
Explicit reread, revision, or concurrency changes require separate authorization
and tests.

## 15. Recommended first implementation step and stop condition

The next work requires separate authorization and must start from verified `main`.
It should be limited to PR-04A and keep Legacy as the default. This review does
not authorize implementation, Provider requests, AWS/RDS, deployment, migration,
push, pull request, or merge.

## 16. Review Corrections Applied

1. Distinguished Runtime-visible, read-only `classify_change` observation from
   current request-side item/patch validation and deterministic Domain
   recomputation; recorded that this is not a fresh reread or concurrency
   guarantee.
2. Removed duplicate trip/actor authority from the conceptual `RuntimeRequest`;
   immutable scope and the controlled Session factory belong only to
   `ReadTripCapability`.
3. Made candidate options an orthogonal collection on `RuntimeResult`, and made
   the user-visible reply explicit, so reply, suggested change, and options can
   coexist as they do in Legacy behavior.
4. Recorded the Legacy evaluation observation-oracle leakage and made a future
   behavior-derived extraction correction a prerequisite for a fair PR-04D A/B
   claim, without changing the frozen dataset or baseline here.
5. Tightened PR-03R, current/default Chat-code boundary, and Required-check
   wording so none is presented as deployment evidence, provider superiority,
   or live Required-check verification.
6. Marked explicit fresh scoped reread, revision validation, and optimistic
   concurrency as future hardening that requires separate authorization and
   tests, not a PR-04A Legacy-preservation change.

## 17. Revised A–J final answers

### A. Current Legacy Runtime Contract

Legacy is a bounded read-only tool loop with worker-owned Sessions and PR-01C
execution controls. Its tool-loop classification is an observation only; Chat
Service validates returned item identity and normalized patch against its
request-side scoped item set, then recomputes deterministic Domain
classification in the request Session. This is not a fresh reread or
optimistic-concurrency guarantee.

### B. Current Pydantic AI Contract

It is an isolated typed, read-only PoC with independent Sessions, typed tools
and outputs, bounded retry/limits, and no route, selector, write authority, or
Domain decision authority.

### C. Minimum common Interface

`RuntimeRequest` contains only message, bounded history, semantic selected-item
hint, request ID, and limits. `ReadTripCapability` is the only trip/actor
authority. `RuntimeResult` has a required user-visible reply, one discriminated
clarification/suggested-change/reply-only primary outcome, orthogonal candidate
options, and redacted observation/failure metadata.

### D. Domain versus Runtime

Runtime orchestrates model/tools and may observe bounded read-only
classification. The Application Service owns deterministic preflight and
request-side set validation, then calls Domain for deterministic preview
classification; Domain owns decision policy and mutation. Explicit fresh scoped
reread is future hardening, not current Legacy behavior.

### E. Minimum PRs

PR-04A contracts and Legacy characterization; PR-04B isolated Pydantic adapter;
PR-04C composition selector defaulting Legacy; PR-04D shared A/B evidence.

### F. Feature Flag placement

Only an Application/runtime composition factory should read
`CHAT_AGENT_RUNTIME`; default is `legacy`, invalid values fail closed, and no
silent cross-runtime fallback is allowed.

### G. Legacy behavior preservation

Keep Legacy as default and preserve Chat Service preflight and public
`ChatResult` mapping through direct adapter characterization, full regression,
and the frozen Golden contract.

### H. Fair A/B Evaluation

Use the same fixtures, dataset, rules, graders, privacy/safety contract, and
taxonomy. Before claiming fairness, replace the current expected-label-derived
Legacy observation extraction with behavior-derived extraction; this is a later
reviewed fix, not a dataset change.

### I. PR-03R reuse versus test-only

Reuse only reviewed execution/session/capability and normalized-observation
concepts. Keep the Smoke Harness, ledger, R1/R2/R3 scenarios, probe, live
runners, wire assertions, and cost helpers test-only.

### J. First genuinely safe implementation step

Only after separate authorization, PR-04A may add additive contracts and direct
Legacy characterization. It must preserve the two-stage classification boundary
and combined reply/suggested-change/candidate-options behavior; it must not wire
a route, selector, flag, Pydantic adapter, or provider call. Any explicit
reread, revision, or concurrency change needs separate authorization and tests.
