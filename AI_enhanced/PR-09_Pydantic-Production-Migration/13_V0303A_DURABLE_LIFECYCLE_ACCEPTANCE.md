# v0.3.0-03A Durable Lifecycle and Authorization Acceptance

Date: 2026-10-23

## Scope and baseline

This is the first isolated Stage 03 acceptance slice. It is based on reviewed,
but not merged, PR #31 candidate commit
`8012dce80d5d8ecd30906192311d685ed1d9e13f` on
`codex/v0303a-pydantic-durable-acceptance`.

The slice adds acceptance tests only. It does not modify production runtime
selection, Legacy behavior, Domain authorization, Preview, Durable code,
schema, API, Protected Apply, provider configuration, AWS resources, or release
workflows.

## Acceptance seam

Each executable scenario enters the existing public
`ConversationRuntimeCoordinator.execute` seam. It uses a real
`PydanticChatAgentRuntime` backed by Pydantic AI `FunctionModel`, then retains
the real `RevisionBracketedPreview`, scoped read-only capability, Durable
Conversation service, and PostgreSQL persistence. The FunctionModel response is
the only scripted boundary. It is local and deterministic; model network access
is disabled.

```text
committed ConversationSubmission
  -> server-owned build_context
  -> ConversationRuntimeCoordinator
  -> RevisionBracketedPreview
  -> Pydantic FunctionModel / scoped tools
  -> Preview freshness and classification
  -> fenced durable completion or safe rejection
```

The test module deliberately uses a `sessionmaker` bound to the PostgreSQL
engine, not `db.connection()` plus `join_transaction_mode="create_savepoint"`.
Concurrent revocation and revision changes are committed from a second session
while a FunctionModel is paused by bounded `threading.Event` synchronization.
There are no sleep-based races.

## Scenario coverage

| Scenario | Test node | FunctionModel | Preview | Durable coordinator / PostgreSQL | Independent committed transaction | Acceptance result |
| --- | --- | --- | --- | --- | --- | --- |
| A1 two-turn durable conversation | `test_pydantic_two_turn_durable_conversation_reconstructs_server_history` | Yes | Yes | Yes | Committed setup and worker sessions | Two User/Assistant turns persist in order; second run receives server-owned completed history; Plan unchanged. |
| A2 durable options reconstruction | `test_pydantic_durable_options_are_reconstructed_then_rejected_when_stale` | Yes | Yes | Yes | Committed setup and worker sessions | Scoped options are Domain-validated, Preview-admitted, persisted, history-projected, initially ready, then rejected as `historical_option_stale` after either captured revision changes; Plan unchanged. |
| A3 revocation before runtime | `test_pydantic_revocation_before_context_admission_never_calls_model` | Intentionally no invocation | No; admission stops first | Context/authorization and PostgreSQL checks | Membership revocation committed before execution | Existing `ConversationUnavailable` rejection; no Assistant/Option rows or model-visible history. |
| A4 revocation during runtime | `test_pydantic_revocation_during_runtime_discards_late_durable_result` | Yes | Yes | Yes | Yes; NullPool-traced physical backend commits revocation while model is blocked | Late result is discarded as `membership_revoked`; no Assistant/Option rows. |
| A5 cross-trip isolation | `test_pydantic_cross_trip_conversation_is_denied_before_model_execution` | Intentionally no invocation | No; admission stops first | Context/authorization and PostgreSQL checks | Distinct committed Trip/Membership roots | Foreign Conversation is rejected before model execution; neither Plan changes. |
| A6 Plan revision fencing | `test_pydantic_preview_rejects_revision_changed_during_model_execution[plan_revision]` | Yes | Yes | Yes | Yes; NullPool-traced physical backend increments Plan revision | Runtime returns fenced `stale_context`; no Assistant/Option rows. |
| A6 facts revision fencing | `test_pydantic_preview_rejects_revision_changed_during_model_execution[planning_facts_revision]` | Yes | Yes | Yes | Yes; NullPool-traced physical backend increments facts revision | Runtime returns fenced `stale_context`; no Assistant/Option rows. |

## Observed authorization behavior

- Before Runtime admission, revoked membership and foreign Conversation access
  fail closed through the existing `ConversationUnavailable` policy. That is a
  deliberate rejection, not a runtime completion; the FunctionModel call count
  remains zero.
- During Runtime execution, a committed membership revocation is observed by
  Preview/Durable completion. The model is permitted to return its local result,
  but it cannot persist an Assistant message or Option.
- A Plan or planning-facts revision change after the captured execution bracket
  is rejected as `stale_context`. This slice does not treat a revision that
  changes after successful Preview verification but before completion as a
  defect: that case is an intentionally historical advisory option boundary and
  remains subject to later option-resolution revalidation.

## Security conclusions

No concrete Pydantic adapter, Preview, Context, Durable, or fixture defect was
found. The implementation uses existing application-owned authorization:

- `context.build_context` locks and validates current membership before model
  input is constructed.
- `RevisionBracketedPreview` captures and rechecks membership, Plan revision,
  and planning-facts revision around scoped tool/model execution.
- `ConversationRuntimeCoordinator` routes stale access/revisions into fenced
  failure/discard paths.
- `complete_submission` requires a live matching attempt and fence before it
  can persist an Assistant result.

The acceptance test initially exposed two test-design issues, not product
defects: pre-runtime denial is an expected `ConversationUnavailable` exception,
and Option counts must be scoped to the scenario Conversation rather than the
session-wide disposable database. The final tests assert those public semantics
directly.

## 03A-FIX P1 evidence correction addendum

The original execution recorded seven parametrized acceptance cases. Independent
review correctly found that its A2 current-state resolution assertion did not
exercise stale rejection, and that engine-bound Sessions did not by themselves
prove distinct physical PostgreSQL backends in A4/A6.

The original behavioral scenarios remain the same, but the test source is not
byte-for-byte unchanged: A2 is now parametrized for Plan and planning-facts
revision changes (the suite is now eight cases), while A4/A6 add test-only
connection instrumentation. No production application source changed.

- A2 persists an advisory Option through the real FunctionModel, Preview, and
  Durable coordinator; it resolves the very same Option as ready, commits each
  revision dimension separately, and then proves the same stored Option returns
  `ready=False`, `code="historical_option_stale"`. Its original stored revision
  metadata and the Plan item remain unchanged.
- A4/A6 use a test-only engine with SQLAlchemy `NullPool`, so each actual
  application Session receives a new physical backend. The test-only Session
  transaction hook records psycopg's server-assigned `backend_pid` without
  issuing a query before Preview sets `SET TRANSACTION READ ONLY`. It attributes
  backend PIDs to the worker before the model barrier, the committed mutator,
  the worker after release, and the final observer read, and asserts the mutator
  PID is distinct from all three relevant paths.
- The tests do not hold any production Preview read transaction open through
  model execution. Production's short bounded transaction lifetime remains
  unchanged; the test only observes it.

The original `14_V0303A_DURABLE_LIFECYCLE_EVIDENCE.json` remains historical
evidence. The execution-backed correction is recorded separately in
`16_V0303A_P1_EVIDENCE_CLOSURE.md` and
`17_V0303A_P1_EVIDENCE_CLOSURE.json`.

## Remaining Stage 03 scope

This slice does not claim full Stage 03 acceptance. Deferred work includes
lease-recovery/fencing races with direct Pydantic execution, timeout/cancellation
persistence, end-to-end telemetry/privacy sequence capture, and the separate
formal Pydantic-versus-Legacy durable evaluation. No Stage 03B or Stage 03C work
starts here.

## Restrictions retained

- PR #31 remains an unmerged candidate baseline.
- No push, PR creation, merge, deployment, provider activation, or production
  operation occurred.
- No real DeepSeek, AWS call, schema migration, or protected Apply change was
  made.
