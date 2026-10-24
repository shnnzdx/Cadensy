# Trip Backend Contract

This document describes the backend contract that the Trip workspace should respect.

Code lives in `../backend/`. Runtime and setup details live in:

- `../backend/README.md`
- `../backend/LOCAL_DEV.md`
- `../HANDOFF.md`

## Core Rule

Decision policy is backend-owned.

Frontend may present, draft, or preview a change, but it must not own final destination
policy, session identity, or decision-path classification.

## Decision Paths

Current backend decision outcomes are:

- `notice`
- `round`
- `reopen_round`
- `confirm`

### Path Meaning

| Path | Meaning |
| --- | --- |
| `notice` | Apply immediately and create an anonymous update notice. |
| `round` | Open a decision round. The current plan stays unchanged until settlement. |
| `reopen_round` | Reopen a settled slot with a stricter vote requirement and written reason. |
| `confirm` | Create a confirmation proposal for affected members only. |

### Classification Priority

The authoritative decision logic lives in:

- `../backend/app/domain/constraints/engine.py`

The general priority is:

1. hard blockers first
2. reopened settled decisions next
3. contested or touched slots next
4. direct notice last

### Settledness Model

Each plan item has a backend-owned settledness state:

- `loose`
- `touched`
- `settled`
- `booked`

Those states affect whether a future change can go straight through, must open a round,
must reopen a round, or must go to confirm.

## Roles

Roles are trip-scoped through `TripMembership`.

| Capability | Organizer | Participant | Guest |
| --- | :--: | :--: | :--: |
| View plan | Yes | Yes | Yes |
| Submit preferences | Yes | Yes | Yes |
| Propose changes | Yes | Yes | Yes |
| Vote or confirm | Yes | Yes | Yes |
| View members | Yes | No | No |
| Create invites | Yes | No | No |
| Handle deadlock exit | Yes | No | No |

Non-negotiable rules:

- organizer preference weight is not higher than anyone else's
- organizer cannot read private preference wording
- organizer cannot act as a super-user and pick one side's proposal for the group

## Privacy

Privacy is structural.

Private wording must not leak through "best effort" filtering only. The system separates:

- raw private wording
- safe decision data
- group-visible updates

Frontend should assume that safe backend payloads are already anonymized by type and should
not try to reconstruct private identity from local state.

## Key Entities

Important backend entities include:

- `User`
- `Trip`
- `TripMembership`
- `InviteLink`
- `Preference`
- `MemberConstraint`
- `MemberConstraintPrivate`
- `Plan`
- `PlanItem`
- `PlanChange`
- `DecisionRound`
- `Vote`
- `ChangeProposal`
- `ProposalDecision`
- `UpdateNotice`

### Important Notes

- `PlanChange` is the append-only ledger for itinerary change history
- `PlanItem` carries canonical schedule information used by the current plan view
- moving an item across dates must keep `day_date` and `day_index` consistent
- current plan day headers should come from canonical trip-window dates, not stale item data

## Initial Plan Generation

`POST /api/trips/{tripId}/plans/generate` is backend-owned.

Current behavior includes:

- organizer prerequisite checks
- refusal to overwrite existing plan items
- deterministic validation around constraints and schedule legality
- planner fallback behavior
- blocked-state reporting
- Planner model output identifies Place Library candidates by `candidate_id`; the backend,
  not the model, resolves canonical English/local names into `PlanItem`
- normal full-day planning prioritizes hard constraints, fixed/locked events, availability,
  opening hours, geography, transitions, meal timing, interests, and variety in that order
- lunch and dinner remain backend-added anchors; route-aware selection tries restaurants at
  progressively wider bounded radii, then meal-specific relaxed categories, and uses an honest
  flexible break only as the final fallback; dinner excludes café-only venues
- `PlanItem.is_meal` and derived `meal_type` are returned by the current-plan API so Trip renders
  `LUNCH` and `DINNER` separately from the sightseeing sequence
- each day receives a distance-aware candidate neighborhood; ordering uses straight-line
  coordinate distance only and does not claim provider routing or walking time
- Place Service ranks clear, category-specific, travel-relevant names inside the existing
  category round-robin and spatial-spread structure, with reliable address information as an
  additional within-category signal
- safely parseable provider opening hours reject known-closed candidates; missing or unsupported
  hours stay unknown, and optional evening stops require explicit evening suitability
- saving a preference or constraint never rewrites current items; an existing plan is marked
  `needs_refresh`, which the current-plan API exposes for a lightweight UI notice

Current merged behavior also includes:

- single-member trips do not treat budget ceiling as a group-blocking constraint
- blocked reasons are no longer always mislabeled as budget failures

## Trip city covers

- My Trips requests city covers through `backend/app/domain/trips/cover_service.py`; the browser
  never receives `UNSPLASH_ACCESS_KEY`
- a selected landscape result is persisted on `Trip` with photographer and Unsplash attribution
- successful covers are reused without another API search; unsuccessful provider attempts are
  retried only after seven days, while missing local key configuration is not negatively cached
- the current workspace is requested first, uses eager/high-priority image loading, and requests a
  1000×560 CDN rendition; Other Trips use native lazy loading and 360×220 renditions
- provider failure, unsafe/incomplete attribution, or no result leaves the existing neutral
  `Travel cover`; Trip covers never fall back to `Place.image_url` or `PlanItem.photo_url`

## Backend Behaviors Already Reflected In `New`

The local merged workspace already depends on these backend behaviors:

- account-backed users stay on `My Trips` when landing on workspace home
- guest-backed sessions still default into their trip
- vote rounds auto-settle once every member has voted
- self-only confirms can auto-apply immediately
- overlap-producing changes route into the correct decision path
- plan day dates are canonicalized from the trip window
- preference dates are validated against the trip date window
- item date moves update both `day_date` and `day_index`

## Assistant Runtime Boundary

Conversation and Preview are runtime-neutral boundaries. A Chat runtime may request only
approved scoped read-only tools; it receives neither a database session, arbitrary SQL,
write capability, provider credentials, nor an unrestricted network client. Current Plan
reads ground item references for the execution, and per-execution guards, cache entries, and
deadline checks prevent rejected, failed, timed-out, or late tool calls from becoming usable
results.

Trusted Tool Execution Evidence is created only from successful scoped handler projections.
Venue replacement fields must exact-match an evidenced replacement candidate, including the
price when one is supplied. Preview independently rechecks membership, trip/Plan scope,
captured revisions, evidence, and deterministic Domain classification before producing an
advisory candidate. It remains the authority; a runtime suggestion is not already approved
or applied.

Targeted option suggestions are validated against the real Current Plan and persist only as
safe advisory options through the existing Durable Conversation path. Protected Apply and the
Conversation schema are unchanged. The default production Chat Runtime remains Legacy;
the Pydantic implementation is local review work and has no production activation.

## Key Endpoints

Representative endpoints:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/account`
- `GET /api/trips`
- `POST /api/trips`
- `GET /api/trips/{tripId}`
- `GET /api/trips/{tripId}/plans/current`
- `POST /api/trips/{tripId}/plans/generate`
- `GET /api/trips/{tripId}/updates`
- `GET /api/trips/{tripId}/preferences/me`
- `PUT /api/trips/{tripId}/preferences/me`
- `GET /api/trips/{tripId}/members`
- `POST /api/trips/{tripId}/invite`
- `GET /api/invites/{token}`
- `POST /api/invites/{token}/join`
- `POST /api/plans/items/{itemId}/classify`
- `POST /api/plans/items/{itemId}/changes`
- `GET /api/plans/{planId}/changes`
- `POST /api/updates/{noticeId}/object`
- `GET /api/rounds/{roundId}`
- `POST /api/rounds/{roundId}/votes`
- `POST /api/proposals/{proposalId}/decisions`
- `POST /api/trips/{tripId}/chat`

## Frontend Integration Rules

When wiring frontend to backend:

- do not read or write raw `tripsync:*` keys outside `shared/session-runtime/`
- do not assemble `Authorization`, `X-Trip-Id`, or `X-Membership-Id` headers manually
- do not reintroduce frontend-owned path classification
- treat `409` as a real pending-decision or conflict state, not a generic crash
- treat group-visible history as `PlanChange`-derived state
- keep Plan runtime state inside the frozen Plan feature boundary

## Remaining Backend Follow-Up

Still worth continuing after this merge:

- planner quality and real AI improvement
- richer invite/account binding flows
- migration tooling
- chat persistence if product direction requires it
- stronger route and travel-distance data
