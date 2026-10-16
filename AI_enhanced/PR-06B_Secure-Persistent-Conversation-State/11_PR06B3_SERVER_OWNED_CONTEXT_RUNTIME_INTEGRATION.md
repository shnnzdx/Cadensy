# PR-06B.3 — Server-Owned Context & Runtime Integration

**Status:** local implementation checkpoint. No deployment, provider, frontend,
AWS, production database, or PR-06C operation is authorized by this document.

## Scope and compatibility

PR-06B.3 connects a previously committed `ConversationSubmission` attempt to
Cadensy's existing selected Runtime and protected revision-bracketed Preview
boundary. It does not change the Conversation schema, Membership lifecycle,
submission/recovery contract, durable HTTP response shape, domain decision
state machine, or legacy `POST /api/trips/{trip_id}/chat` behavior.

### PR-06B.2 entry gate

Work began on `codex/pr06b-independent-development` with a clean tree, final
PR-06B.2 remediation commits (`e5d64c7`, `a216cf2`), and Alembic head
`b7d3a1e9c642`. A focused durable API/concurrency entry check passed 13 tests
on the disposable local PostgreSQL database before PR-06B.3 changes began.

| Route / path | PR-06B.3 behavior |
| --- | --- |
| `POST /api/trips/{trip_id}/chat` | Remains legacy and client-history compatible. It neither reads nor writes durable Conversation rows. |
| Durable message/recovery routes | Preserve their frozen `202` admission/replay body. When the explicit non-production runtime gate is on, a newly accepted attempt is executed synchronously only after admission commits. |
| Submission status route | Remains the durable polling source of truth. The admission response never claims a provider result. |
| Protected Preview/Apply | Preview is reused for durable candidate construction; Apply remains its existing protected writer, never a Conversation writer. |

`CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED` defaults to off. A true value
is accepted only outside `APP_ENV=production|prod`; any production rollout
requires a separate approved change. There is no background queue, FastAPI
background task, fire-and-forget execution, or automatic replay worker.

## Authoritative execution identity and context

`ExecutionIdentity` contains only server-issued primitives:

```text
Trip ID + owner Membership ID + Conversation ID + Submission ID
    + accepted attempt number + accepted fence
```

`build_context` locks and revalidates the normal lifecycle path:

```text
Trip -> AccountSession or GuestSession -> Membership -> Conversation -> Submission
```

It requires the owner Membership, active non-expired Conversation, matching
Submission, current running attempt/fence, and live lease. Consequently a
different Trip, Organizer, Guest, later Membership incarnation, removed or
quarantined Membership, deleted Conversation, expired root, stale recovery,
or wrong fence cannot construct history. Request admission already validates a
bearer credential; all later phases revalidate current Trip/Membership/root
authority and never widen scope based on an Organizer role.

The common owner-authorized snapshot produces two detached projections:

- **Deterministic projection** preserves message sequence, role, immutable
  assistant Option IDs/ordinals, safe patch data, and historical plan/facts
  revisions. It is evidence, not current mutation authority.
- **Runtime text projection** contains only `RuntimeHistoryTurn(role, text)`.
  Persisted options never become tools, system instructions, or executable
  patches. The current User Message is supplied separately and exactly once.

The textual projection uses a conservative character budget, not a tokenizer
guarantee. System and output reserves plus the current User Message are
mandatory; an oversized mandatory request fails with
`context_budget_exceeded` instead of modifying or silently dropping it.
Historical rows are considered newest-first and restored chronologically. The
selected rows are a contiguous newest complete-message suffix; no message is
mid-message truncated, and an older turn cannot leapfrog a newer turn that
does not fit. User rows belonging to an earlier `running` or `failed`
Submission are excluded: they are not completed conversational turns.
Context reads and retries do not alter retention.

Content-free metadata is `included_turn_count`, `excluded_turn_count`,
`truncated`, and `budget_category`. It deliberately excludes messages, option
patches, credentials, invitation tokens, and constraints from logs/telemetry.

## Three-phase runtime lifecycle

```text
committed accepted/recovery claim
  -> Phase 1: short Context transaction
  -> Phase 2: detached Runtime + revision-bracketed read-only Preview
  -> Phase 3: short fenced durable completion/failure transaction
```

Phase 1 ends before Runtime execution. Phase 2 passes the same textual history
to whichever existing Runtime is selected by `CHAT_AGENT_RUNTIME` through
`build_chat_runtime`; Legacy and PydanticAI therefore share the same durable
authorization and context semantics. The existing `RevisionBracketedPreview`
opens fresh bounded Sessions for read tools, current classification, and final
revision checking. The Runtime never receives an ORM Session or a generic
write/query capability.

Phase 3 calls only `complete_submission` or `fail_submission`. Both repeat
owner/root checks and require the current lease, attempt, and fence. A stale
old execution, removal/quarantine, deletion/expiry, or newer Recovery discards
the model result. Exactly once applies to the committed Assistant Message and
its Option rows; a valid lease-recovery can still cause another external
Provider invocation after an uncertain earlier one.

The Runtime's `AgentCandidateOption` cannot be persisted as an authoritative
domain change. The coordinator persists only existing protected Preview
candidate options, including the Preview-captured Plan and planning-facts
revisions. They are historical suggestions. A later reference must resolve
the same Conversation/Assistant/Option identity and then run the normal fresh
authorization, revision, classification, Preview, and Apply checks. No code
in this path writes `Plan`, `PlanItem`, Vote, Proposal, or decision tables.

Categorical outcomes include `context_budget_exceeded`,
`context_unavailable`, `membership_revoked`, `stale_context`, approved Runtime
failure kinds such as `provider_timeout`, `unexpected_exception`, and
`completion_unavailable`. Raw Provider exceptions and model bodies are neither
persisted as failure text nor returned by the durable route.

## Request-bound delivery and idempotency

After `submit_message` or `recover_submission` commits, the API invokes the
coordinator only when its internal `execution_claimed` fact is true. It is
true for a new Submission, a valid expired-lease same-key reclaim, or a newly
accepted Recovery key. It is false for an active same-key replay, an existing
Recovery-key replay, and status reads. Therefore an active replay cannot start
another Runtime execution just because a request is repeated.

The route still returns the original committed `202` admission response and
the client polls status. If coordinator composition fails before it can record
a terminal state, the admission is still truthful (`running`) and the normal
lease/recovery protocol remains the only retry path; the API never claims a
non-durable success.

## Changed implementation seams

- `backend/app/domain/conversations/context.py` — server-owned bounded common
  history projections.
- `backend/app/domain/conversations/runtime.py` — three-phase fenced
  coordinator, reusing `RevisionBracketedPreview`.
- `backend/app/domain/conversations/service.py` — internal-only
  `execution_claimed` admission fact; no public response-field change.
- `backend/app/domain/chat/service.py` — composition helper preserving the
  existing Legacy/Pydantic runtime selection and deadlines.
- `backend/app/domain/preview_apply.py` — internal Runtime failure category on
  the Preview result, so durable execution records a failure rather than a
  fabricated assistant response.
- `backend/app/api/main.py` — opt-in post-commit, request-bound delivery only.

## Test matrix and local evidence

Automated tests use injected fake runtimes and `MOCK_AI=1`; no paid provider is
called. The focused suite verifies chronological server-owned history, exactly
once current input, mandatory-budget failure, exclusion of failed turns,
cross-owner denial, fenced completion, redacted runtime failure, request-bound
new-claim versus replay behavior, and existing revision-bracketed Preview
guards.

Final regression used `backend/.venv`, `MOCK_AI=1`,
`DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and only the
disposable local `tripsync_pr06b3_full_test` database:

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=short
1180 passed, 10 Alembic path_separator warnings, 111.98s
```

The warnings originate in `test_trip_bound_phase1_migration.py`; no test was
excluded. The first full-suite attempt correctly hit the existing fail-closed
startup guard because the ignored local `.env` had the retired membership
header switch set to `1` during production-mode tests. The test-only shell
value `0` produced the passing run; no configuration file was edited.

Focused Runtime/context/Preview/API validation passed 28 tests. This is
mock-only evidence: live Provider behavior, production telemetry, TLS,
encryption, backup lifecycle, and deployment remain unverified.

## Checkpoint commits and disposition

- `b1e41c2` — PR-06B.3A server-owned bounded context.
- `1700301` — PR-06B.3B fenced post-commit Runtime coordinator.
- This PR-06B.3C commit records Preview provenance and final evidence.

**Recommendation: PASS for PR-06B.3 local acceptance.** PR-06B.4 frontend
cutover remains a separate authorization; production Runtime execution stays
off by default.
