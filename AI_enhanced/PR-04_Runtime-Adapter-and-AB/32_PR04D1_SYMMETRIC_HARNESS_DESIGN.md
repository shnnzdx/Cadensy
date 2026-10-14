# PR-04D1 — Symmetric Evaluation Harness Design Audit

## Verdict

**CONDITIONAL — DESIGN GAP**

The two runtimes can receive deterministic fakes at the same conceptual level
(below their Runtime orchestration) without a network request.  The Legacy
runtime already has a suitable lower-level provider-call seam.  Pydantic AI's
`FunctionModel` is already below the Pydantic Agent graph.

However, a formal A/B runner must first add an evaluation-only way to observe
actual read-tool invocations while preserving the Legacy adapter's current
`LegacyRuntimeReadTool` type boundary.  It must also specify a shared scenario
script and failure-event mapping; the present `fake_provider` function returns
a completed Legacy result and cannot be reused as a Pydantic `FunctionModel`.
Those additions are deliberately not implemented in PR-04D1.

## 1. Verified base

| Item | Verified value |
| --- | --- |
| `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| PR-04D-Prep checkpoint | `eb913e3a1b7a9bbf79998990705de65ac87ae1bc` |
| PR-04D1 branch | `codex/pr04d1-symmetric-harness-design` |
| PR-04D1 worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04d1-symmetric-harness-design` |
| Frozen dataset version | `chat-change-preview-v1` |
| Canonical LF dataset SHA-256 | `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f` |
| Existing behavior-derived Legacy application baseline | 6 / 8, 0 safety violations |

The SHA is the repository's canonical LF hash: the runner and CI normalize
CRLF before hashing.  The Windows working-copy byte hash is therefore not a
dataset version signal.

This review was static only.  No formal A/B run, Provider request, AWS/RDS
access, deployment, push, pull request, or merge was performed.

## 2. Current asymmetry

`backend/evals/runner.py::_run_legacy_case` currently patches
`base.call_agent` with `_fake_provider(...)`.  That replacement returns a
synthetic `AgentRunResult` (or raises) before the real Legacy loop runs.

Consequently, the current evaluator bypasses the Legacy implementation of:

- request/provider deadlines inside `base.call_agent`;
- provider-round handling and the multi-round limit;
- Legacy tool lookup, guards, cache, handler dispatch, and tool-result
  accumulation;
- Legacy trace/round construction and stopped-reason logic.

The repaired PR-04D-Prep observer remains valid as an **application-contract
baseline** because it no longer derives observations from Golden labels.  It is
not a symmetric framework comparison and must not be compared directly with a
Pydantic Agent graph execution.

## 3. Verified Legacy call chain and true Provider boundary

The currently selected Legacy path is:

```text
Chat Service `_respond_with_agent_branch`
  -> `LegacyChatAgentRuntime.run`
     -> `run_agent_with_deadline(worker=...)`
        -> `LegacyReadTripCapability.run_with_read_only_tools(...)`
           -> independent, short-lived worker Session
           -> `build_read_only_trip_tools(...)`
           -> `base.call_agent(...)`
              -> build messages, tool map, guard/cache state and round state
              -> per round: `deadline.before_provider()`
              -> `base._agent_reply_with_fallback(...)`
                 -> `base._invoke_agent_provider(...)`
                    -> `OpenAI(...).chat.completions.create(...)`
              -> `deadline.after_provider()`
              -> Legacy guard/cache/tool-handler loop
              -> next round or `AgentRunResult`
```

The exact real Provider-call function is
`backend/app/agents/base.py::_invoke_agent_provider`:

```python
def _invoke_agent_provider(
    *,
    config: ProviderConfig,
    messages: list[dict[str, Any]],
    tools: tuple[AgentTool, ...],
    max_tokens: int | None,
    timeout_seconds: float = 90.0,
) -> AgentProviderReply
```

It validates provider configuration and is the only function in this chain
that creates `OpenAI` and calls `chat.completions.create`.  The caller
`_agent_reply_with_fallback` retains the configured-provider loop and timeout
normalization; `call_agent` retains every round, tool, guard, cache, token,
and stopped-reason behavior.

## 4. Recommended Legacy fake seam

For PR-04D2, patch or inject a deterministic evaluator implementation at
`base._invoke_agent_provider`, not at `base.call_agent`.

The fake must accept the exact signature above and return a real
`AgentProviderReply` sequence.  For a tool case, its first reply should contain
a real `AgentToolCall`; the unmodified `call_agent` then validates the name,
runs guard/cache/handler code, appends the tool message, and requests the next
fake model reply.  This leaves `base.call_agent` genuinely executing.

Two explicit controls are required:

1. Evaluation must prevent the `MOCK_AI=1` branch from selecting
   `_next_mock_agent_reply`, because that branch never reaches
   `_invoke_agent_provider`.  It must do so in a tightly scoped test context,
   with a fake-only provider configuration and a network-deny guard; it must
   not change normal CI or production configuration.
2. The scripted fake must never delegate to the original function.  An
   outbound-network deny assertion belongs in the harness test.

There is therefore already a usable technical seam; no production Provider
interface is required solely to establish it.  An optional explicit injectable
Provider-call callable would improve test readability later, but is not needed
for the first symmetric harness.

### Failure semantics caveat

The existing `raise_provider_failure` path raises from the replacement
`call_agent`, and the evaluator separately records that event as
`provider_failure`.  A lower-level fake exception instead travels through
`_agent_reply_with_fallback`: generic exceptions become `AgentUnavailable`,
and the present Runtime mapping does not itself produce the evaluation-only
`provider_failure` label.  PR-04D2 must introduce a scenario-event observer
that records the actual fake dispatch failure independently of Golden labels,
or use a typed, documented fake exception mapping.  It must not infer the
failure from `expected_failure_taxonomy`.

The present `tool_failure` fake is also not a real handler failure: it returns
a pre-stopped `AgentRunResult`.  A symmetric test needs an explicit shared
failure design (for example, a scripted request for a common read tool plus an
eval-only controlled handler failure) and must document how its raw failed-tool
event relates to the frozen Golden case whose expected successful tool list is
empty.  Until that distinction is specified, a literal tool-trajectory grader
cannot be claimed symmetric for this case.

## 5. Pydantic fake seam

`backend/app/agents/pydantic_runtime.py::PydanticChatAgentRuntime._run_async`
constructs a Pydantic AI `Agent` and calls `await agent.run(...)`.  Existing
isolated tests pass `pydantic_ai.models.function.FunctionModel` to
`PydanticChatAgentRuntime(model=...)`.

```text
PydanticChatAgentRuntime.run
  -> run_agent_with_deadline
  -> same ReadTripCapability worker Session lifecycle
  -> `_run_async`
  -> `_DeadlineBoundModel(FunctionModel, deadline)`
  -> Pydantic `Agent.run(...)`
  -> FunctionModel scripted response
  -> Pydantic tool/output validation and result mapping
```

`FunctionModel` is below the Pydantic Agent's orchestration: it receives the
Agent's generated model request and returns `ModelResponse` / `ToolCallPart`.
The Pydantic Agent still validates tool arguments, invokes its registered tool,
enforces output validation/retries/usage limits, and maps a `RuntimeResult`.
It is the correct fake seam for Pydantic in PR-04D2.

## 6. Proposed symmetric architecture

```text
frozen case (input, fixture preconditions, scenario name only)
                         |
                         v
same disposable synthetic fixture + same actor/trip scope
                         |
                         v
same Chat Service deterministic preflight
  |                                      |
  | application-only return              | Runtime reached
  |                                      v
  |                    instrumented `LegacyReadTripCapability`
  |                    (independent worker Session per execution)
  |                              |
  |              +---------------+----------------+
  |              |                                |
  |              v                                v
  |  `LegacyChatAgentRuntime`          `PydanticChatAgentRuntime`
  |  real `base.call_agent` loop       real Pydantic `Agent.run` graph
  |  scripted `_invoke_agent_provider` scripted `FunctionModel`
  |              |                                |
  +--------------+--------------------------------+
                         |
                         v
                 common sanitized observation
                         |
                         v
                  unchanged Golden grader
```

The scenario compiler may read only execution fields: case id, message,
selected synthetic item key, fixture facts, and `fake_provider`.  It produces
framework-specific wire scripts from one neutral event plan; it does not read
any `expected_*`, `domain_oracle`, or allowed-output field.  Legacy scripts
produce `AgentProviderReply` events; Pydantic scripts produce equivalent
`FunctionModel` `ModelResponse` / `ToolCallPart` events.  The two wire formats
need not be identical.

For the existing Chat Service, PR-04D2 needs **test-only composition** to pass
an injected `PydanticChatAgentRuntime(FunctionModel(...))`: normal
`build_chat_runtime` intentionally fails closed for `CHAT_AGENT_RUNTIME=pydantic`
without an explicitly injected factory.  The harness must not alter the
production selector or route to get this composition.

## 7. Shared actual tool-invocation instrumentation

The current `evidence.tool_calls.append(...)` is written by the fake
`call_agent` stub, so it is not evidence of an actual tool call.  PR-04D2
should introduce an evaluation-only `ToolInvocationRecorder` with events such
as:

```text
{name, sanitized_arguments, outcome: success | failure}
```

The recorder must:

- record immediately around the real handler invocation, then re-raise the
  original exception on failure;
- use an allowlist derived from the exposed tool schema and retain only safe,
  scalar argument values needed for the contract;
- replace internal IDs with the fixture's public item key (or redact them),
  and redact strings not explicitly safe;
- retain no raw tool output, ORM entity, Session, credentials, authorization
  header, private constraint wording, or membership identifier;
- make its per-case event stream the sole source of formal tool-trajectory
  observation.

### Why a generic wrapper alone is insufficient

Pydantic invokes `RuntimeReadTool.invoke`, so a generic decorated
`RuntimeReadTool` is sufficient there.  Legacy is more constrained:
`LegacyChatAgentRuntime._legacy_tools` requires each descriptor to be an
`isinstance(..., LegacyRuntimeReadTool)` and then returns the enclosed
`base.AgentTool` directly to `base.call_agent`.  A generic outer wrapper would
either fail that type check or be bypassed by the Legacy handler.

The smallest parity-preserving design is an optional, default-`None` recorder
hook at `LegacyReadTripCapability` construction.  When supplied only by the
evaluator, it wraps each freshly built `base.AgentTool.handler` before creating
the corresponding `LegacyRuntimeReadTool`.  Thus:

- Legacy `call_agent` receives its normal `AgentTool`, with normal guards and
  cache, whose real handler is observed;
- Pydantic receives the same `LegacyRuntimeReadTool` surface and calls its
  `invoke`, which reaches that same observed handler;
- production construction with no recorder is byte-for-byte behaviorally
  equivalent; and
- neither runtime receives a Session, query interface, or write capability.

This is an additive capability-composition change proposed for PR-04D2, not a
PR-04D1 implementation.

## 8. Capability parity and intentional differences

Both adapters can consume the same `LegacyReadTripCapability` instance shape
for the same synthetic trip id and authorized membership id.  Each execution
opens its own short-lived, independent worker `Session`; fairness means the
same committed synthetic facts and scope, **not** sharing one SQLAlchemy
Session across runtimes.  That preserves PR-01C session ownership.

| Surface | Legacy Runtime | Pydantic Runtime | Fairness interpretation |
| --- | --- | --- | --- |
| capability/session | `LegacyReadTripCapability`, independent worker Session | same capability contract and independent worker Session | same scoped facts, no request-Session sharing |
| plan read | `get_current_plan` | `get_current_plan` | common and directly comparable |
| trip facts | `get_trip_facts` | not registered by PR-04B | intentional runtime surface difference |
| classification | `classify_change` | no direct tool; Chat Service recomputes deterministic preview from typed suggestion | intentional; Domain remains authoritative |
| replacement search | `find_replacement_place` | unsupported | intentionally out of current Pydantic scope |
| options | `propose_options` | unsupported | intentionally out of current Pydantic scope |
| write authority | none | none | required parity |

The frozen cases must not be changed to conceal those differences.  A future
comparison may report capability coverage separately, but may not call a
Pydantic limitation a harness defect or invent unsupported Pydantic behavior.

## 9. Scenario-independence audit

`fake_provider` is an execution scenario input, not a Golden oracle.  In the
current runner, `_fake_provider` reads `case["fake_provider"]`, `case["id"]`,
and scenario input/fixture values.  It does **not** read:

- `expected_business_outcome`;
- `domain_oracle.expected_path`;
- `expected_failure_taxonomy`; or
- `expected_tool_calls`.

That makes the field suitable as the identifier for a future shared neutral
scenario script, provided PR-04D2 adds structural tests that reject oracle
field access from the scenario compiler and both framework adapters.

It is not sufficient to reuse the current callable itself: it fabricates a
completed `AgentRunResult`, directly appends a tool trace, and knows the Legacy
result format.  The future compiler must instead express neutral events (reply,
tool request, controlled failure, or must-not-run assertion) and lower them
separately into the two real framework fake seams.

**Finding:** no current fake behavior is derived from expected outcome, path,
failure taxonomy, or expected-tool labels.  There is no oracle-leakage blocker.
The unsolved failure-event mapping described above is a design gap, not label
leakage.

## 10. Frozen eight-case classification

Classification describes the current application call path, not what the
Golden case ideally intended.  Application-only cases remain valuable product
contract coverage but cannot rank framework orchestration.

| Case | Classification | Current-path reason |
| --- | --- | --- |
| `explicit-time-notice` | Runtime-discriminating | selected item is resolved; execution reaches the Runtime. |
| `ambiguous-time` | Runtime-discriminating | current preflight does not recognize this wording as a deterministic missing-time clarification; the current fake's `must_not_run` assertion is reached and becomes safe degradation. This is a visible 6/8 contract mismatch, not a Pydantic score. |
| `ambiguous-item` | Application-only | item reference resolution returns deterministic ambiguity before Runtime construction. |
| `booked-item-confirm` | Runtime-discriminating | selected booked item reaches the Runtime; deterministic Domain classification occurs after the Runtime result. |
| `provider-failure-fallback` | Runtime-discriminating | reaches the Runtime and exercises provider-failure fallback. |
| `tool-failure-fallback` | Runtime-discriminating | reaches the Runtime, although the current stub does not yet exercise an actual handler failure. |
| `privacy-injection` | Application-only | current item-reference preflight resolves it as ambiguous before the scenario's fake privacy reply can run; this is the second visible 6/8 mismatch. |
| `cross-trip-denied` | Application-only | membership/trip authorization raises before any Runtime or capability is constructed. |

Formal PR-04D2 reports must keep all eight cases in the end-to-end product
contract report, but calculate runtime-comparison observations only for the
five Runtime-discriminating cases and label the remaining three as
non-discriminating.  This does not fix the two frozen-case mismatches.

## 11. Minimum PR-04D2 implementation plan (not implemented here)

| File / area | Minimum additive change | Purpose |
| --- | --- | --- |
| `backend/evals/runner.py` or a new `backend/evals/symmetric_harness.py` | test-only explicit runtime composition; neutral scenario compiler; lower scripts to Legacy `_invoke_agent_provider` and Pydantic `FunctionModel`; ensure network denial | retain both real orchestrators |
| `backend/app/agents/legacy_runtime.py` | optional default-off evaluation recorder hook at capability tool construction | one actual, sanitized tool trajectory for both adapters while preserving the Legacy type boundary |
| `backend/evals/...` harness support | sanitized event recorder plus failure-event observer | record success/failure without raw output; resolve lower-seam failure evidence |
| `backend/tests/test_evaluation_foundation.py` and new focused harness tests | assert no `base.call_agent` patch; assert real multi-round/tool handler execution; prove no expected-field reads; verify no network and independent worker Sessions | prevent asymmetry and leakage regression |
| Pydantic-isolated test module | assert `FunctionModel` executes actual Pydantic Agent/tool/output path for every Runtime-discriminating script | prevent a fake-only Pydantic green result |

No Golden Dataset, grader criterion, Chat Service, Domain rule, runtime selector,
production route, or Pydantic capability expansion is proposed as part of this
minimal plan.  If failed-tool trajectory evidence requires changing the frozen
grader interpretation, that must be isolated and separately approved rather
than silently folded into PR-04D2.

## 12. Risks and blockers

1. **Failure parity is incomplete.** Legacy's fallback handling and Pydantic's
   exception normalization differ.  A neutral, actual-event failure observer
   is required before any aggregate failure score is fair.
2. **Tool-failure Golden semantics need an explicit observation rule.** The
   frozen case expects no successful tool calls, while an actual handler
   failure has a real attempted call.  Do not hide that event to make the
   existing grader green.
3. **Current Pydantic composition is intentionally test-injected only.** The
   production selector fails closed without an injected factory.  The A/B
   harness must remain test-only and must not turn this into Chat-route
   integration.
4. **Tool-surface differences are real capability differences.** The harness
   must permit different internal trajectories, while exposing them in reports
   rather than treating them as equivalent.
5. **Application-only cases cannot measure frameworks.** Including them in a
   single framework score would falsely credit or penalize a Runtime for
   deterministic Chat Service behavior that it did not execute.
6. **Synthetic facts must be committed and visible before each independent
   worker Session opens.** The PR-04D-Prep fixture-visibility mechanism should
   be reused; nested transaction visibility must not be relied upon.

## 13. Recommendation

**CONDITIONAL — DESIGN GAP**

PR-04D2 may be separately authorized once its implementation contract includes
the optional capability-level tool recorder, explicit shared scenario/failure
event mapping, test-only Pydantic composition, lower-seam Legacy fake, network
deny assertion, and separate reporting for application-only cases.  It must
not run a comparison or modify product behavior before those controls are
implemented and reviewed.
