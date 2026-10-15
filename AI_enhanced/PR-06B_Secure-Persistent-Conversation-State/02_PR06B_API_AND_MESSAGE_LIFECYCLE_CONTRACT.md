# PR-06B.0 — API and Message Lifecycle Contract

Status: approved V1 design; not yet implemented.

## API direction

Introduce a durable Conversation API rather than changing the current client-history `/chat` endpoint in place:

```text
POST   /api/trips/{trip_id}/conversations
GET    /api/trips/{trip_id}/conversations/{conversation_id}/messages?before=&limit=
POST   /api/trips/{trip_id}/conversations/{conversation_id}/messages
DELETE /api/trips/{trip_id}/conversations/{conversation_id}
```

All endpoints authenticate through the existing bearer/Membership path. The server derives Trip and Membership scope; neither is accepted as a client authority. [backend/app/api/main.py:195](../../backend/app/api/main.py#L195)

The old `POST /api/trips/{trip_id}/chat` remains an explicit compatibility concern during migration. It must not silently accept a client history as the new authority after the durable UI path is released.

## Read and pagination contract

- Reads require the active owner Membership and active Conversation status.
- Response ordering is immutable ascending `sequence`; reverse cursor pagination may be used for older messages.
- Deleted/expired Conversations return no content and do not create model context.
- Candidate option data is returned only for its owning assistant message, never as a global Trip resource.

## Submission and idempotency contract

`POST .../messages` requires `Idempotency-Key` and accepts only new user text plus permitted local context such as an explicit selected item reference. It never accepts a client-provided history or client-provided candidate options.

Canonical request input includes Conversation ID, owner scope, text, selected item reference, and the endpoint operation version. The server stores an HMAC fingerprint, not a plaintext canonical payload for replay comparison.

| Situation | Required response |
|---|---|
| Same key, same payload, completed | Replay the original durable result; no provider call. |
| Same key, same payload, active lease | Return the original running state; no second provider call. |
| Same key, different payload | `409 idempotency_key_conflict`. |
| New key while another submission is active | `409 conversation_busy`. |
| Deleted, expired, revoked, or removed owner | Access-denied/not-found response with no context or result persistence. |

## State machine and short transactions

```text
new request
  -> short transaction: authorize, lock conversation, write user message,
     create running submission with lease + fence
  -> committed external Runtime execution
  -> short transaction: re-authorize, lock submission/conversation,
     validate same fence and active state, write assistant result/options
  -> completed | failed
```

No transaction remains open during model/provider execution. This follows the existing protected Preview approach, which closes request-owned session state before Runtime work and performs fresh authorization/revision checks. [backend/app/api/main.py:1193](../../backend/app/api/main.py#L1193), [backend/app/domain/preview_apply.py:201](../../backend/app/domain/preview_apply.py#L201)

### Durable lease and fencing

- `lease_expires_at` bounds a crashed worker’s claim.
- `fence` increments every time the same durable submission is reclaimed after lease expiry.
- Completion requires the row to still be `running`, the fence to match, the Conversation not deleted, and the owner Membership to remain active.
- A late worker with an older fence is rejected and cannot write an assistant reply, options, telemetry content, or resurrect deleted data.
- A same-key retry can reclaim only its own expired submission; a distinct new key remains `409 conversation_busy` while an active submission exists.
- V1 intentionally has no queue or background provider worker.

## Server-owned bounded context adapter

The adapter reads only completed, nondeleted messages from the authorized Conversation. It builds two projections:

1. **Deterministic history projection:** bounded `HistoryTurn` plus persisted validated options for the follow-up option resolver.
2. **Runtime textual projection:** bounded `RuntimeHistoryTurn` values for Legacy and Pydantic runtimes.

The builder uses an explicit newest-first token/character budget, then restores chronological order. It must reserve budget for the new user message and system instructions, truncate only at message boundaries, and record only categorical truncation telemetry.

Current service behavior already separates deterministic option resolution from Runtime textual history. [backend/app/domain/chat/service.py:176](../../backend/app/domain/chat/service.py#L176), [backend/app/domain/chat/service.py:222](../../backend/app/domain/chat/service.py#L222) PR-06B.3 must make this adapter common to standard and Protected Preview paths.

## Candidate option selection contract

When a user chooses a historical option, the service must freshly verify:

- Conversation owner and active Membership;
- Conversation/Trip/item scope and item existence;
- safe patch allowlist and option provenance;
- current Plan and planning-facts revisions as applicable;
- current deterministic domain classification;
- existing Preview/Apply authorization.

Stale, deleted, ambiguous, conflicting, or no-op options return a safe clarification or replacement suggestion; they never mutate the Plan. Existing tests already expect stale historical option handling to clarify rather than apply. [backend/tests/test_chat_agent_branch.py:1238](../../backend/tests/test_chat_agent_branch.py#L1238)

## Deletion and retention lifecycle

Deletion is an owner-authorized short transaction that marks the Conversation deleted, clears active submission eligibility, and schedules primary-record purge no later than 30 days later. Context/list/read queries filter deletion immediately. Completion transactions check deletion after acquiring their lock, so an in-flight run cannot recreate output.

A scheduled purge worker is mandatory for the 30-day deletion SLA and 180-day retention policy. It must be idempotent, batchable, observable through content-free counters/categories, and tested with a controlled clock. No existing scheduler is evidence that this contract has been implemented.
