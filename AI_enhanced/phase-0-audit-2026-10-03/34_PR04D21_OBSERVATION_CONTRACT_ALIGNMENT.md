# PR-04D2.1 — Observation Contract Alignment

## Verdict

**READY FOR PR-04D3**

The three observation/grader blockers are resolved without changing Dataset V1,
product behavior, the Runtime default, or either Runtime's execution boundary.
The harness remains one-case-at-a-time and ungraded; no formal eight-case A/B
execution, aggregate score, or winner declaration occurred here.

## 1. Verified base

| Item | Value |
| --- | --- |
| `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| PR-04D2 checkpoint | `f1859f58b4ae1c092b5c18f49d14041469caea1a` |
| Branch | `codex/pr04d21-observation-alignment` |
| Worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04d21-observation-alignment` |
| Starting status | clean reviewed PR-04D2 checkpoint |
| Dataset V1 canonical LF SHA-256 | `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f` |

## 2. Changed files

- `backend/evals/symmetric_harness.py`
- `backend/evals/graders.py`
- `backend/tests/test_symmetric_evaluation_harness.py`
- this report

No Dataset, Chat Service behavior, Domain behavior, Runtime selector/default,
Capability/session/recorder design, Pydantic feature surface, real Provider,
AWS/RDS, deployment, push, pull request, or merge changed.

## 3. Failed-tool semantics: before and after

### Before

The PR-04D2 harness correctly recorded a real failed invocation:

```text
get_current_plan -> failure
```

but exposed one undifferentiated `tool_calls` collection.  Dataset V1's
`tool-failure-fallback` has `expected_tool_calls: []`, so it was unclear
whether that meant no attempted invocation or no successful invocation.

### After

The harness records both facts separately:

```text
attempted_tool_calls  = [{name: get_current_plan, arguments: {day: all}, outcome: failure}]
successful_tool_calls = []
```

The attempted failure remains visible and is never converted into "no call".
The minimal grader clarification is that Dataset V1 `expected_tool_calls`
means **successfully completed calls**.  The grader compares it against
`successful_tool_calls` when present and retains backward compatibility with
older reports that only contain `tool_calls`.

Attempted calls are still included in the privacy scan.  They are evidence and
report material, not a new Dataset expectation or a hidden score adjustment.

## 4. Clarification observation: before and after

### Before

`HarnessObservation` classified every non-preview result as `reply_only`.
That misclassified an actual deterministic clarification and relied on a shape
that could not distinguish Application preflight from ordinary reply text.

### After

The harness wraps only the actual deterministic Chat Service branch helpers:

| Actual helper branch | `clarification_source` |
| --- | --- |
| ambiguous reference | `deterministic_ambiguous_item` |
| missing named reference | `deterministic_missing_item` |
| missing slot/time | `deterministic_missing_slot` |

When any such branch runs, `output_kind` becomes `clarification`.  The result
does not depend on reply-text matching or Golden expectations.  A future
Runtime structured clarification remains a separate Runtime observation path;
none of the frozen V1 scenarios currently emits it, so no artificial scenario
or Runtime behavior was introduced merely to populate the field.

The real current `privacy-injection` case continues to follow its actual
Application branch (deterministic ambiguity), rather than being forced into
the Dataset's intended `reply_only` label.

## 5. Safe-degradation observation: before and after

### Before

The harness inferred degradation from:

```python
result.reply.startswith("I couldn't prepare")
```

That was brittle and coupled an execution fact to user-visible copy.

### After

The harness wraps `chat_service._degraded_reply` inside its temporary
evaluation context.  It records `safe_degraded=True` only when the actual
Chat Service degradation branch executed.  The observed `output_kind` is then
`safe_degraded`, without inspecting reply wording or any expected failure/
business label.

Focused coverage proves this branch for each of:

- Legacy provider failure;
- Pydantic provider failure;
- Legacy actual read-tool failure; and
- Pydantic actual read-tool failure.

## 6. Final `HarnessObservation` schema

```text
runtime
runtime_executed
provider_events
attempted_tool_calls
successful_tool_calls
output_kind
clarification_source
decision_path
safe_degraded
network_attempts
worker_session_is_independent
```

All fields are execution evidence.  The schema contains no Golden expected
value, Domain oracle result copied from Dataset, or score.

## 7. Grader semantics

The only grader adjustment is:

```text
case.expected_tool_calls <-> observed.successful_tool_calls
```

when the new split exists.  Existing reports without that field keep their
historical `tool_calls` comparison, so archived PR-02 / PR-04D-Prep reports
remain gradeable.  `attempted_tool_calls` is never discarded and is not used to
make the Dataset's successful-call expectation pass.

The change does not relax allowed output kinds, expected decision path,
failure taxonomy, or Dataset content.

## 8. Scoped Pydantic request setting

The harness no longer assigns `models.ALLOW_MODEL_REQUESTS` globally.  It uses
a scoped `patch.object(..., False)` that restores the incoming value on exit.
A regression test starts with the value true, runs a Pydantic FunctionModel
case, verifies zero HTTP attempts, and proves the original true value is
restored afterward.

## 9. Exact verification

All database tests used explicit local disposable PostgreSQL
`cadensy_pr04d21_test`; the runtime placeholder URL was a distinct local
database name.  No AWS/RDS or production database was used.

| Scope | Result |
| --- | --- |
| Focused symmetric harness + evaluation foundation | **21 passed** |
| Isolated Pydantic compatibility / smoke / composition / evaluation / CI-contract range | **111 passed** |
| Approved Legacy full partition with Pydantic-only modules explicitly ignored | **510 passed**, JUnit **0 skips** |
| `pip check` — isolated Pydantic environment | no broken requirements |
| `pip check` — Legacy regression environment | no broken requirements |
| Python syntax compilation | passed in both environments |
| Dataset canonical SHA check | matches approved V1 hash |

Focused tests cover:

1. ambiguous-item: application-only, `runtime_executed=False`, structured
   clarification;
2. deterministic missing item and missing slot clarifications;
3. change preview;
4. ordinary runtime reply-only output;
5. provider and tool failures as actual events and actual safe degradation;
6. retained failed tool attempt plus empty successful-call list;
7. existing `expected_tool_calls=[]` grading against the empty successful list;
8. expected-label mutation leaves scenario and both observations unchanged;
9. Legacy/Pydantic real orchestration symmetry and independent worker Sessions;
10. zero HTTP attempts; and
11. scoped Pydantic request setting restoration.

## 10. Symmetry preservation proof

The change does not alter the PR-04D2 seams:

```text
Legacy:   real base.call_agent -> fake only base._invoke_agent_provider
Pydantic: real Agent.run        -> fake FunctionModel
```

No Runtime method is replaced.  Both consume the same capability construction
with the same committed synthetic trip facts and a worker-owned independent
Session.  Actual tool trajectory comes from the real wrapped handler.

## 11. Remaining limits

- PR-04D2.1 still does not run the eight frozen cases as an A/B suite or
  calculate a score.
- The two behavior-vs-Golden mismatches already visible in the PR-04D-Prep
  6/8 application baseline remain product/evaluation evidence; this change
  does not conceal or repair them.
- Runtime tool-surface differences remain intentional and must be reported as
  capability coverage in PR-04D3, not normalized away.

## 12. Recommendation

**READY FOR PR-04D3**

PR-04D3 may now be separately authorized to run a formal comparison using the
same frozen Dataset and grader, with attempted and successful tool evidence
reported separately.  This authorization has not been exercised here.
