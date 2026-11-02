# Missing Duration Fallback and Assistant Change Options

Date: 2026-08-16

Scope: `backend/app/domain/decisions/`, `backend/app/agents/`, `trip/src/final/plan-feature/`

This note records four related changes and the two defects that are still open. They all
trace back to one fact about the data.

---

## 0. The root fact

`PlanItem.duration_min` is almost always `NULL`.

`PlannerPlace.duration_min` defaults to `None` in `backend/app/domain/places/service.py`
and **is never assigned anywhere**. The `place` table has no duration column, and the
Geoapify provider does not return one. The generator copies `poi.duration_min` into every
plan item it writes, so the value it copies is always `None`.

Measured across the local database:

| Plan | Items | Items with a duration |
| --- | --- | --- |
| `Mia's 30th in Chicago` (hand written seed) | 9 | 9 |
| `xi'an` (generated) | 15 | 0 |
| `la`, `laa`, `laaa`, `laaaa`, `vacation` (generated) | 181 | 0 |

Every generated itinerary is affected, including any generated during a demo. This is not
a per-trip data problem that can be fixed by backfilling one trip.

The planner already handled this: `generator.py` uses the idiom `poi.duration_min or 90`
in six places. Three other call sites did not, and each failed in a different way.

---

## 1. Overlap detection silently skipped unmeasurable blocks

**File:** `backend/app/domain/decisions/orchestrator.py`

`_changed_item_window()` returned `end_hour = None` when the item had no duration, and
`_schedule_conflict_item()` bailed out on that `None`. It also skipped any peer whose own
duration was missing.

The effect on a generated itinerary was that **overlap was never detected at all**. Moving
an item on top of another item classified as `notice` — "no conflict, this can apply now" —
and the plan quietly stacked two blocks at the same time.

**Change:** introduced `DEFAULT_BLOCK_MINUTES = 90` and used it for both the changed item
and each peer. The `| None` in the return type is gone; the window is now always defined.

**Verified on the `xi'an` trip:** moving `Xi'an City Wall` from 14:00 to 12:00, where
`Pizza Hut` is scheduled at 12:00.

| | Before | After |
| --- | --- | --- |
| Path | `notice` | `round` |
| Message | "No conflict — this can apply now" | "Pizza Hut is already scheduled at this time." |

## 2. Replacement search refused to run without a duration

**File:** `backend/app/agents/tools.py`, `_replacement_candidates()`

The function opened with `if selected.duration_min is None: return ()`. Duration is used
in exactly one place inside it — `_time_block_is_supported()`, which checks a candidate
against opening hours. Every other filter (title dedupe, coordinate dedupe, distance
ranking) is independent of duration.

So `find_replacement_place` returned **zero candidates for every item of every generated
itinerary**. The place library was not the limitation: the local cache holds 70 Xi'an
places, 74 Chicago, 80 Los Angeles, and destination matching is case-insensitive, so the
lowercase `xi'an` destination resolved correctly.

**Change:** compute `selected_duration = selected.duration_min or DEFAULT_BLOCK_MINUTES`
and pass that to the opening-hours check.

**Verified:** asking to swap `Xi'an City Wall` now returns real nearby attractions from
the place service instead of an empty list.

## 3. The model could not see the block the scheduler was using

**File:** `backend/app/agents/tools.py`, `_safe_item()`

After change 1, the backend scheduled a missing duration as 90 minutes, but `_safe_item()`
— the only source of itinerary facts the model receives — still reported `duration_min`,
`end_hour`, `end_time_label` and `time_range_label` as `null`.

The two sides were working from different numbers. The model read "Pizza Hut is at 12:00"
and proposed 13:00 as a free slot; the backend held Pizza Hut until 13:30 and rejected it.
Every compromise the assistant drafted was classified back as a conflict.

**Change:**

- `end_hour`, `end_time_label` and `time_range_label` are computed from
  `item.duration_min or DEFAULT_BLOCK_MINUTES` and are never `null`.
- `duration_min` is unchanged and still reports the real stored value, which may be `None`.
  The assumed value is not written back into the field that claims to be a fact.
- A new boolean `duration_assumed` is `true` when the end time is an estimate.
- The `get_current_plan` tool description states the 90-minute assumption.

**Verified.** The model now answers a "list day 1 with start and end times" question with
`Pizza Hut, 12:00 PM to 1:30 PM`, and the compromises it drafts land in genuinely free
slots. On the `xi'an` trip, three runs produced 2-3 same-item alternatives each, and all
but one classified as `notice` on their own.

One behavioural side effect is worth knowing. Once the model understands the durations, its
first instinct is often to move the *blocking* item rather than the requested one — for
example "shift Pizza Hut later" instead of moving the City Wall. A decision round settles
exactly one item, so `roundAlternativesFrom()` in the frontend drops any option aimed at a
different item, and the ballot ends up with no assistant option. Constraining the request
to the same stop ("suggest other times for this same stop") avoids this.

## 4. Assistant change options were built but never rendered

**Files:** `trip/src/final/plan-feature/PlanFeature.jsx`, `trip/src/final/final.css`

`useAssistantChangeRequestFlow.js` already exported `selectCandidateOption`, which
re-classifies one drafted option through `POST /api/plans/items/{id}/classify` and swaps it
into the drawer's change card. No component ever called it, so:

- the options the assistant drafted were stored on the message and never shown;
- the `/classify` endpoint and its `app.classify` wrapper were unreachable from the UI,
  even though `docs/PRODUCT.md` lists "support private dry-run chat" as an AI responsibility.

**Change:** added a `CandidateOptionList` component that renders each drafted option as a
`.roundOption` button — the same visual language a ballot uses — and wired it to
`actions.selectCandidateOption`. Each card shows the option title and its tradeoff only;
the assistant-authored label and body restate the title and made the drawer unreadable.

Selecting an option re-checks it on its own, so a member can find a change that needs no
group decision without opening a round first. The stored `candidateOptions` are untouched,
so `applyProposal()` still passes the full set to the ballot.

## 5. Item menu was clipped on the last stop of a day

**File:** `trip/src/final/final.css`

Two separate causes, both released only while a menu is open:

1. `.accordionInner` keeps `overflow:hidden` so the `grid-template-rows: 0fr → 1fr`
   height animation does not leak. A downward-opening menu on the last stop of a day was
   clipped by it.
2. `.accordionInner` is transformed, so it owns a stacking context. The menu could not
   paint above the following day's card regardless of its own `z-index: 200`.

Fixed with `.accordionDay.open .accordionInner:has(.actionMenu){overflow:visible}` and
`.accordionDay:has(.actionMenu){position:relative;z-index:100}`. Both are scoped by
`:has(.actionMenu)`, so every frame without an open menu keeps the original animation and
layering.

---

## Open defects, not fixed

### A. A drafted option may collide, and nothing catches it

`_validated_suggestions()` in `tools.py` runs each suggestion through
`orchestrator.classify_change()` inside a savepoint and rejects it **only if that raises**.
A schedule collision returns a normal `round` verdict, not an exception, so a colliding
suggestion is accepted onto the ballot. `_validated_change_options()` in `api/main.py`
validates frontend-supplied options the same way.

`settle_round()` then applies the winning patch with no conflict re-check, so a vote for a
colliding option stacks two blocks — the outcome `_schedule_conflict_classification()`
explicitly says the product prevents.

This already happened in the local `xi'an` data: `Temple of the Eight Immortals` was settled
onto Day 3 09:00, where `Xi'an Tang Dynasty City Wall Hanguangmen Site Museum` was already
scheduled.

Change 3 reduces how often the model proposes a colliding time, but does not close the gap.

A full fix also has to answer a product question the ballot does not currently ask: when a
round exists *because* of an overlap and the group votes for the proposed change anyway,
what happens to the item that was overlapped? No ballot option addresses it.

### B. `POST /api/plans/items/{id}/classify` duplicates the read-only path

The endpoint dry-runs by calling `propose_change()` inside a savepoint and rolling back,
while `orchestrator.classify_change()` exists specifically to classify without writing.
Two mechanisms for one job. Change 4 made the endpoint reachable again, so this is now a
duplication to resolve rather than dead code.

### C. `POST /api/trips/{trip_id}/constraints` returns 500 for `date_range`

Reproduced with `{"kind":"date_range","params":{"start":"2026-08-14","end":"2026-08-16"}}`.
`time_window` with the same shape succeeds. Not investigated.

### D. `reset_demo.py` is database-wide, not scoped to the demo trip

It issues unfiltered `delete()` against `PlanChange`, `UpdateNotice`, `Vote`,
`ProposalDecision`, `DecisionRound`, `ChangeProposal` and `InviteLink`, so it also clears
decision history for trips a developer created for their own testing.

It then reads **all** plan items ordered by `created_at` and zips them against the 9 seed
rows, so the first nine items in the whole database are overwritten positionally. The
module docstring's claim that ids are preserved holds for the row ids, but the mapping from
id to itinerary content is not stable across runs.

---

## Verification

```bash
cd backend
./.venv/bin/python -m pytest tests/ -q -k "overlap or conflict or replacement or schedule or orchestr or engine or round or tools or agent"
```

152 passed.

```bash
cd frontend
node --test tests/trip-preview-integration.test.mjs
```

10 passed.

The full backend suite reports 415 passed and 2 failed. Both failures
(`test_purge_demo_data.py::test_purge_demo_data_removes_demo_trip_rows_and_preserves_fixed_accounts`
and `test_upsert_fixed_accounts.py::test_upsert_fixed_accounts_is_idempotent`) assert user
account counts in the test database and reproduce with these changes stashed. They are
pre-existing and unrelated.

A new test, `test_item_without_duration_reports_the_scheduled_block_as_an_estimate`, covers
both branches of `_safe_item()`: a stored duration keeps `duration_assumed` false, and a
missing one reports the 90-minute end time while leaving `duration_min` as `None`.

Frontend changes require `npm run build:trip-preview` from `frontend/` before `/trip`
reflects them. Backend changes are picked up by `uvicorn --reload`.
