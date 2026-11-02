# Chat Agent V1 Frontend Chatbox Reconnect

Last updated: 2026-08-15

## 1. Purpose

This document records the August 15, 2026 frontend-side reconnection work for Chat Agent V1.

It is intentionally different from:

- `CHAT_AGENT_V1_HISTORY_AND_FUTURE_2026-08-15.md`

That earlier file is the historical baseline and future-direction handoff.
This file is the concrete incident record for why the chat box still looked broken in the real Trip UI after the backend Chat Agent V1 runtime had already been restored.

## 2. User-Visible Symptom

The backend Chat Agent V1 path was already alive again, but the real Trip chat surfaces still felt broken:

- the Plan drawer assistant could return a reply, but follow-up option-based turns did not work correctly
- fuzzy requests that should have come back with `candidate_options` did not stay usable across turns
- the personal chat page at `/trip/:tripId/chat` was still flattening agent responses into plain text
- from the user point of view, "the agent is still unusable in the chat box" was a fair description

## 3. Root Cause

The issue was not the active AI provider configuration and not the backend runtime path itself.

The real break was a frontend protocol regression:

- backend now returned Chat Agent V1 payloads again, including `candidate_options`
- frontend assistant state was still using the older simplified shape
- assistant history preserved only `{ role, text }`
- the drawer stored only `proposed_change`, not `candidate_options`
- option selection was not reclassified through the authoritative backend path before Apply
- `TripAppState.submitChange(...)` was not forwarding assistant-provided alternative options into `/plans/items/{item_id}/changes`

Short version:

```text
Backend V1 was restored.
Frontend chat protocol was still half on the old branch.
```

## 4. Scope Of The Fix

This repair stayed inside the already-approved frontend boundaries.

It did not move plan logic back into `FinalApp`, and it did not bypass the frozen seams.

The main files changed were:

- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`
- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/FinalApp.jsx`
- `trip/src/final/final.css`
- `frontend/tests/plan-feature-characterization.test.mjs`
- `frontend/public/trip-app/` embedded preview output after rebuild

## 5. What Was Reconnected

### 5.1 Drawer assistant history and option flow

The Plan drawer assistant now:

- preserves `candidate_options` in assistant history
- stores `candidateOptions` in local message state
- supports selecting one candidate option before Apply
- reclassifies the chosen option through the backend classification path
- only submits after the user explicitly applies the prepared change

### 5.2 Submit-change option forwarding

`TripAppState` now again supports the V1 path where assistant alternatives are forwarded into the real change request:

- `classify(...)` accepts an explicit `patch`
- `submitChange(...)` forwards `options` when they exist
- assistant-generated alternatives can therefore become real vote options instead of being dropped at the request seam

### 5.3 Personal chat page preservation

The personal chat page in `FinalApp.jsx` was also updated so it no longer discards the richer assistant protocol:

- assistant history on `/trip/:tripId/chat` now also preserves `candidate_options`
- fuzzy replies with options are surfaced to the user as numbered follow-up choices
- the personal chat path no longer collapses everything into a plain single-turn text response

### 5.4 UI presentation

The drawer UI now includes:

- a visible candidate option list
- selected-option state
- duration-aware change preview support
- clearer error and "prepare before apply" states

## 6. Validation Loop Used

The red-capable local feedback loop for this incident was:

```text
node -e "const fs=require('fs'); const assert=require('assert'); const flow=fs.readFileSync('trip/src/final/plan-feature/useAssistantChangeRequestFlow.js','utf8'); const state=fs.readFileSync('trip/src/final/TripAppState.jsx','utf8'); assert.match(flow,/candidate_options/); assert.match(flow,/candidateOptions/); assert.match(state,/patch = null/); assert.match(state,/if \\(options\\?\\.length\\) body\\.options = options/); console.log('frontend chat-agent loop green');"
```

This was red before the fix and green after the fix.

## 7. Validation Results After The Fix

Confirmed locally on August 15, 2026:

- targeted frontend chat-agent loop: green
- `node frontend/tests/plan-feature-characterization.test.mjs`: 12 passed
- `cd frontend && npm run build:trip-preview`: passed

Important note:

- the first `build:trip-preview` attempt failed inside the sandbox with `spawn EPERM`
- rebuilding with the required elevated execution succeeded
- the embedded `/trip` bundle was therefore actually updated, not just the source files

## 8. Why This Document Exists Separately

This file should stay separate from the history/future handoff because it answers a different question.

`CHAT_AGENT_V1_HISTORY_AND_FUTURE_2026-08-15.md` answers:

- what Chat Agent V1 is
- what was restored on `main`
- what future work is reasonable

This file answers:

- why the user still saw a broken chat box even after backend restoration
- which frontend seams were stale
- what exact frontend reconnection work was required to make the real Trip UI usable again

## 9. Practical Rule Going Forward

If Chat Agent behavior appears broken again in the UI, do not stop after backend validation.

Check all of these together:

1. backend `/api/trips/{trip_id}/chat` response shape
2. drawer assistant history serialization
3. `candidate_options` storage and rendering
4. option-selection reclassification path
5. `submitChange(...)` forwarding of assistant options
6. rebuilt `frontend/public/trip-app/` preview output

If only the backend is fixed, the product can still look broken to the user.

## 10. Final Summary

The Chat Agent V1 backend restoration was necessary but not sufficient.

This follow-up repair reconnected the real Trip frontend chat surfaces to the restored V1 protocol so that:

- drawer chat can keep option-based context
- assistant alternatives stay usable
- Apply still remains the only mutation boundary
- the embedded `/trip` experience now reflects the repaired source

Current status label:

```text
Chat Agent V1 Frontend Chatbox Reconnected
LOCAL VALIDATION GREEN
EMBEDDED TRIP PREVIEW REBUILT
```
