# Backend Handoff - 2026-08-21

## Scope

This handoff covers the backend changes made for the itinerary edit, remove, confirm, conflict, and replacement-place flows.

Primary backend files touched:

- `backend/app/api/main.py`
- `backend/app/domain/chat/service.py`
- `backend/app/domain/decisions/orchestrator.py`
- `backend/app/domain/decisions/organizer.py`
- `backend/app/domain/access/trip_scope.py`
- `backend/app/agents/tools.py`
- `backend/tests/test_chat_agent_branch.py`
- `backend/tests/test_paths.py`

## What Changed

### 1. Add Stop Supports Constrained Start Time

The add-stop API now accepts an optional `start_hour`.

Endpoint:

- `POST /api/plans/{plan_id}/items`

Request now includes:

- `title`
- `after_item_id`
- `before_item_id`
- `start_hour`

Validation rules:

- The new stop must be between the selected adjacent items.
- Manual stops are treated as 30 minutes.
- The previous item uses its real `duration_min`; if missing, backend assumes 90 minutes.
- If the selected time does not fit, backend returns an error instead of creating an overlap.

### 2. Remove Means Real Removal

`remove` is now a real itinerary operation. It does not turn the item into free time.

Implementation:

- `ChangeRequest` supports `remove`.
- `settledness` supports `removed`.
- Plan outputs and agent tools filter out removed items.
- `_apply()` marks the item as removed and keeps it out of visible Current Plan data.

Important behavior:

- Removed items remain in the database for audit/history.
- They should not show in the itinerary, chat plan context, replacement search, or scoped plan access.

### 3. Confirm Flow Simplified

Confirm is now one proposal with only affected members deciding:

- Affected member: `Accept` or `Decline`.
- Decline closes/escalates the current proposal; it does not apply anything.
- Organizer does not accept for another traveler.
- Organizer deadlock exits are:
  - `keep`
  - `split`
  - `clear` (recorded with the historical `deadlock_remove` audit origin)

Removed from backend:

- Confirm alternative generation endpoint.
- Alternative selection endpoint.

Kept:

- AI chat/group-round candidate options. These are not Confirm-page alternatives.

### 4. Accept Response Now Returns Updated Proposal State

`decide` now returns the full proposal state plus `applied`, instead of only a boolean-like response.

This allows frontend to immediately refresh:

- `my_status`
- `can_decide`
- anonymous member statuses
- final proposal status
- whether the plan was actually applied

### 5. Time Conflict Prevention

Backend now checks schedule overlap more consistently.

Where:

- `propose_change`
- `classify_change`
- `decide_proposal`

Behavior:

- If a proposed move overlaps another visible item, it cannot apply directly.
- At confirm accept time, backend re-checks conflict before applying.
- Removed items are ignored when checking conflicts.
- Missing duration uses the default 90-minute block assumption.

This prevents two activities from stacking at the same time after a later move or confirm action.

### 6. Replacement Place API

New endpoint:

- `GET /api/plans/items/{item_id}/replacement-places?q=...`

It calls the existing replacement-place tool path:

- `agent_tools._find_replacement_place(...)`

Returns up to 12 candidates.

Candidate fields include:

- `candidate_id`
- `title`
- `local_title`
- `place`
- `photo_url`
- `lat`
- `lng`
- `price_per_person`
- `opening_hours`
- `opens`
- `closes`
- `tags`

The replacement search excludes:

- the current item
- removed items
- places already used in Current Plan
- candidates whose known hours do not cover the current item time block

### 7. Replacement Local Title Bug Fixed

Bug observed:

```text
title=东亚饭店
local_title=德发长饺子馆
place=钟鼓楼广场, Beiyuanmen
```

Cause:

- Replacement patch changed `title/place`, but old `local_title` remained.

Fix:

- `ChangeRequest` supports explicit `local_title`.
- If a title/place replacement does not provide `local_title`, `_patch_with_poi_metadata()` clears it with `local_title = None`.
- Replacement candidates now include provider `local_title` when available.

Result:

- New replacements should not show stale subtitles.
- Existing dirty dev data is not automatically migrated.

### 8. Selected Item Replacement Search Bug Fixed

Bug:

When a traveler has an item selected and types English like:

- `search East Asia Hotel`
- `find East Asia Hotel`
- `look for East Asia Hotel`

The resolver did not strongly bind the request to the selected item.

Fix:

- `_resolve_item_reference()` now treats selected-item search phrases as replacement-search intent.
- Agent prompt context explicitly says that `search X`, `find X`, `look for X`, `replace with X`, and `swap this with X` mean replacement venue query for the selected item.

### 9. Agent Internal Reasoning Is Cleaned Before Display

Bug observed from real HTTP chat:

The reply could include paragraphs like:

- `The user wants...`
- `Let me check...`
- `I should...`

Fix:

- Added `_strip_internal_reasoning()` in chat service.
- It removes obvious internal reasoning paragraphs before returning the chat reply.

## Regression Tests Added

Added tests:

- `test_selected_item_replacement_search_phrases_stay_grounded_to_selection`
- `test_agent_reply_internal_reasoning_is_not_shown`
- `test_replacing_place_clears_stale_local_title`

Files:

- `backend/tests/test_chat_agent_branch.py`
- `backend/tests/test_paths.py`

## Verification Run

Commands run:

```bash
cd backend
.venv/bin/pytest tests/test_chat_agent_branch.py::test_agent_reply_internal_reasoning_is_not_shown tests/test_chat_agent_branch.py::test_selected_item_replacement_search_phrases_stay_grounded_to_selection tests/test_paths.py::test_replacing_place_clears_stale_local_title -q
.venv/bin/pytest tests/test_chat_agent_branch.py tests/test_paths.py tests/test_agents_tools.py -q
.venv/bin/python -m py_compile app/api/main.py app/agents/tools.py app/domain/chat/service.py app/domain/decisions/orchestrator.py app/domain/decisions/organizer.py app/domain/access/trip_scope.py
```

Results:

```text
3 passed
116 passed
py_compile passed
```

## Notes For Next Developer

- Restart backend after pulling these changes. The running FastAPI process will not pick up backend code edits unless reload is active.
- Existing dirty data like `title=东亚饭店` with `local_title=德发长饺子馆` remains dirty until manually corrected or replaced again.
- Do not re-add Confirm alternatives unless the product decision changes. The current product rule is one Confirm proposal, accept/decline only.
- If replacement search quality feels weak, improve the place provider/cache ranking, not Confirm logic.
