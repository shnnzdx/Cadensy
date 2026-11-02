# Chat Agent V1 History And Future

Last updated: 2026-08-15

## 1. Purpose

This document records what was actually completed for Chat Agent V1, what was intentionally excluded, what was validated, and what the next likely directions are after the current capstone baseline turned green.

This is not a speculative redesign note.
It is a baseline handoff document tied to the current repository state.

## 2. Current Verdict

Chat Agent V1 is functionally complete for the current capstone scope.

### 2.1 Main branch restoration note on August 15, 2026

This needs to be stated explicitly because the repository history can otherwise look misleading:

- commit `d30cad3c1e5dc202754364a8ba590cde168f9f96` completed the Chat Agent V1 backend baseline
- later, commit `127ef9196cbda8a1238c37c8d11727e8df40af2c` kept many valid repo updates, but it also replaced the active chat-agent runtime path with a simplified branch
- commit `6e2197e04f605b7178fae0f310b36d7806d5b952` added backend handoff docs only
- commit `4b5b44bff6110c929572308c506bf32019918449` refreshed Trip workspace UI and handoff docs, and was not the backend chat-agent overwrite
- commit `9b69a44` on `main` restored the missing Chat Agent V1 runtime behavior while preserving the rest of the already-landed `127ef91` repository baseline outside the chat-runtime files

Practical meaning:

- `main` should now be understood as: keep the broader repo state that exists after `127ef91`, but restore the Chat Agent V1 backend behavior that had existed in `d30cad3`
- future sync work must not treat the simplified post-`127ef91` chat path as the intended source of truth
- if Chat Agent code is compared again in a future merge, use `d30cad3` and `9b69a44` as the behavioral reference points, not only the immediate pre-restore state

The restoration on `main` specifically re-established these V1 backend capabilities:

- `base.call_agent(...)` as the active read-only chat agent harness
- chat history flowing back into the backend agent branch
- `candidate_options` round-tripping through the API contract
- follow-up option selection from prior assistant-provided options
- deterministic degraded fallback when the agent/tool branch fails
- the "Current Plan has not changed until Apply" safety contract
- replacement validation tied to supported tool-produced candidates

Completed and validated:

- P0 Core Product Contract
- P1.1 Candidate Options / Selection
- P1.2 Multi-turn Item Reference
- P1.3 Failure Handling
- P1.4 Functional Completeness
- Final frontend characterization baseline cleanup

Current baseline status:

- backend regression suite A: green
- backend regression suite B: green
- trip frontend build: green
- plan-feature characterization suite: green

## 3. Product Contract We Locked

The following behavior is now treated as approved V1 scope and should not be casually redesigned:

- Chat is read-only until the user clicks Apply.
- The assistant may prepare a proposed change, but it must never imply that the Current Plan has already changed.
- The authoritative decision path always comes from deterministic backend rules, not from the model.
- Fuzzy requests produce candidate options rather than silently choosing one.
- Explicit concrete requests can produce a ProposedChatChange directly.
- Multi-turn item references work through selected-item context and history.
- Cross-day moves require proper inspection of the target day.
- Replacement places must come from real supported replacement candidates.
- Non-supported destinations fail safely for replacement.
- Booked and required cases escalate to confirm.
- Settled cases escalate to reopen_round.
- Overlap and contested cases escalate to round.
- Failure fallback must stay deterministic and read-only.

Short version:

```text
AI proposes.
Backend rules decide the path.
Humans apply.
```

## 4. Architecture We Confirmed

### 4.1 Core backend split

Chat Agent V1 is not a free-writing assistant attached directly to the database.
It is a controlled read-only agent layer on top of deterministic domain rules.

Current flow:

```text
POST /api/trips/{trip_id}/chat
  -> domain/chat/service.py
  -> agents/base.call_agent(...)
  -> read-only tools
  -> deterministic classify_change / propose_options / replacement validation
  -> proposed_change + candidate_options
  -> frontend Apply
  -> /plans/items/{item_id}/changes
  -> decisions/orchestrator.py
```

### 4.2 Read-only tool boundary

The important V1 safety boundary is that the agent tools remain read-only.
The model can inspect, classify, and suggest, but cannot mutate plan state itself.

### 4.3 Frontend boundary

The real product seam for Chat Agent is now:

- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`
- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/TripAppState.jsx`
- `backend/app/api/main.py`
- `backend/app/domain/chat/service.py`

That is the end-to-end surface to inspect before changing behavior.

## 5. What We Fixed Across P0 -> P1.4

### 5.1 P0

We aligned the basic contract:

- natural-language request enters chat
- backend classifies impact
- frontend shows proposed change
- user action remains required

### 5.2 P1.1 Candidate options / selection

We completed the fuzzy-request path:

- fuzzy request can produce candidate options
- options do not auto-apply
- user can choose one
- the chosen option can proceed through the normal change flow

### 5.3 P1.2 Multi-turn item references

We completed history-based resolution for common follow-up language:

- pronouns like this / it
- item references carried from history
- day-qualified follow-up references
- stale or missing references fail safely

### 5.4 P1.3 Failure handling

We hardened agent failure behavior:

- timeout / empty response / stopped_reason / model failure degrade safely
- no second model call during failure fallback
- fallback remains deterministic and read-only
- fallback explicitly states the Current Plan has not changed

### 5.5 P1.4 Functional completeness

We audited the real product, not just backend tests, and closed the true end-to-end seams:

- candidate options are now actually visible in the frontend when fuzzy requests return options without an immediate proposal
- assistant history now carries `candidate_options` back to the backend so follow-up selection works in the real UI path
- option selection now reclassifies through the authoritative backend path before Apply
- duration changes now travel correctly through the API and UI instead of being dropped at the request schema seam
- frontend characterization tests were updated to match the approved implementation instead of older source shapes

## 6. Key Functional Outcomes Now True

A normal traveler can now do all of the following inside the current intended scope:

- ask about the Current Plan
- ask basic trip facts supported by tools
- request an explicit time move and see a real proposal
- request a cross-day move and have it classified correctly
- request a replacement place within supported destination scope
- make a fuzzy request and receive usable options in the drawer
- select an offered option and continue through the real product flow
- apply a prepared change into notice / round / reopen_round / confirm
- do nothing and leave plan state unchanged
- receive a deterministic safe reply when the agent branch fails

## 7. Validation Record

Final validated results before this document:

### Frontend

- `node --test frontend/tests/plan-feature-characterization.test.mjs`
  - 12 passed
- `cd trip && npm run build`
  - passed

### Backend

- `python -m pytest tests/test_chat_agent_branch.py tests/test_chat.py tests/test_agents_tools.py -q`
  - 79 passed
- `python -m pytest tests/test_chat_safety_and_time.py tests/test_paths.py -q`
  - 55 passed

## 8. What We Intentionally Did Not Do

These were explicitly kept out of scope for Chat Agent V1:

- Pydantic AI migration
- runtime refactor
- multi-agent architecture
- provider scaling redesign
- candidate token signing / security hardening pass
- Redis or persistent chat session infrastructure
- generalized production provenance architecture
- destination support expansion beyond current validated seams
- changes to `constraints/engine.py`
- changes to `decisions/orchestrator.py`

## 9. Known Boundaries That Still Exist

These are not current V1 blockers, but they are still true:

- chat history is not persisted across refreshes
- the assistant remains a bounded read-only layer, not a general autonomous planner
- replacement support is still constrained by the current place and destination seams
- production security hardening is not complete
- provider routing and runtime architecture are still legacy-shaped

## 10. Recommended Next Directions

These are ordered by likely value after the baseline, not by mandatory execution.

### 10.1 Demo preparation

Safest next non-architecture step:

- prepare a stable demo script around the now-validated V1 product contract
- use known-good destinations and known-good decision-path scenarios
- avoid mixing demo preparation with new behavior work

### 10.2 Production hardening, if needed later

Reasonable future hardening topics:

- persistent chat history
- stronger provenance and trace surfaces for proposed changes
- signed or otherwise tamper-resistant candidate option transport
- richer runtime observability around tool usage and degraded replies

### 10.3 Possible future migration path

If the team later revisits Pydantic AI, do it only after protecting the current contract.
The migration target should preserve:

- read-only tools
- deterministic authoritative classification
- Apply-required mutation boundary
- safe degraded fallback
- candidate-option and multi-turn behavior

Migration should be judged against the existing validated product behavior, not against a cleaner abstract architecture.

## 11. Practical Rule For Future Work

Before changing Chat Agent behavior, ask:

1. Is this a real product gap or just an architecture itch?
2. Does it cross the read-only / Apply-required boundary?
3. Does it preserve authoritative backend classification?
4. Can it be validated end to end, not only by unit tests?

If the answer to 2 or 3 is no, it is probably not Chat Agent V1 follow-up work.

## 12. Final Summary

Chat Agent V1 is now in the right state for the capstone:

- product contract is clear
- end-to-end behavior is connected
- regression baseline is green
- future work can start from a stable reference instead of an ambiguous partial implementation

Current status label:

```text
Chat Agent V1
FUNCTIONALLY COMPLETE
REGRESSION BASELINE GREEN
```

See also:

- `docs/backend/dixin/CADENSY_AI_V1_CAPSTONE_COMPLETE_2026-08-15.md`
  for the final capstone-level status across both Chat Agent and Planner
