# Cadensy AI V1 Capstone Complete

Last updated: 2026-08-15

## 1. Final Scope Verdict

For the current capstone scope, Cadensy AI V1 should now be treated as complete.

More precisely:

```text
Cadensy AI V1

Chat Agent V1
COMPLETE

Planner V1
COMPLETE FOR CAPSTONE

Deterministic Decision Layer
COMPLETE / AUTHORITATIVE

AI Evaluation / Regression Baseline
ESTABLISHED
```

This does not mean every future optimization is done.
It means the core AI product contract is finished, connected, testable, and
demonstrable.

## 2. What Is Now Actually Finished

### 2.1 Chat Agent V1

Chat Agent V1 is complete for capstone delivery.

Current locked behavior:

- frontend and backend are connected end to end
- chat remains read-only until Apply
- the backend returns proposals and candidate options, not silent mutations
- deterministic rules remain the authoritative decision path
- agent/tool failure degrades safely
- the Current Plan does not change until Apply
- regression coverage for the approved V1 path is green

Short version:

```text
AI proposes.
Backend rules decide.
Humans apply.
```

### 2.2 Planner V1

Planner V1 is complete for capstone delivery.

Current locked behavior:

- the real DeepSeek route is working
- the Planner returns structured output through a constrained day-level schema
- deterministic validation still checks candidate identity, time windows,
  opening hours, and legality after model output
- invalid day output is rejected rather than trusted
- unusable planner days still degrade to deterministic rules fallback
- final itinerary generation remains legal and persistable
- a real-AI evaluation harness now exists and can be rerun locally

This is exactly the intended hybrid Planner architecture:

```text
model output is useful when valid
Python rules stay in charge when it is not
```

### 2.3 Deterministic decision layer

This boundary is complete and should be treated as authoritative.

That means:

- AI does not directly write arbitrary plan state
- AI does not bypass legality checks
- AI does not overrule deterministic backend constraints
- fallback behavior is part of the design, not evidence of failure

## 3. Phase 2C Outcome

Phase 2C focused only on one question:

```text
why does real DeepSeek Planner structured output sometimes fail?
```

Final diagnosis:

- not mainly a JSON serialization problem
- not mainly an API schema mismatch problem
- not mainly parser brittleness
- mainly a semantic legality problem in model-selected `start_hour` values
- especially visible when repair output still ignored opening-hour and time-window constraints

Minimal fix that was applied:

- add per-candidate `legal_start_hours` to the Planner prompt context
- strengthen the repair prompt so the model must repair against those legal
  start times instead of vaguely retrying
- keep all deterministic validation rules strict

What was intentionally not changed:

- no relaxation of legality validation
- no geographic ranking changes
- no meal-ranking changes
- no fallback redesign
- no candidate ranking redesign

## 4. Current Real-AI Baseline Meaning

After the current structured-output fix, the real Planner baseline is good
enough for capstone completion.

Why:

- Planner output now survives deterministic parsing much more reliably
- invalid outputs are still blocked safely
- full itineraries still generate legally
- the evaluation harness can explain what came from AI versus rules

This is the right capstone bar:

- usable
- explainable
- safe under failure
- demonstrable

It is not the bar of:

- every run must use AI on every day
- every day must be perfectly geographically compact
- every run must be identical

Those are optimization goals, not V1 completion requirements.

## 5. Known Limitations That Are Not Blockers

The following are still real, but they should now be documented as known
limitations or future improvements rather than unfinished V1 work:

- meal-anchor placement can still occasionally force a planner day to fall back
- some fallback days can have larger geographic spread
- some days can still be underfilled
- sparse-data cities remain weaker when hours and duration facts are missing
- two real-AI runs can still differ from each other

These do not invalidate the architecture.
They are expected consequences of a hybrid system where deterministic rules
protect the final result.

## 6. Recommended Project Stance Now

The recommended stance after this point is:

```text
freeze AI feature development for capstone
```

Meaning:

- do not keep chasing 4/4 AI days on every run
- do not keep tuning spread metrics right now
- do not keep reopening Planner prompt work just to make one sample prettier

The next best project tasks are operational and presentational:

1. demo data and demo script
2. local startup checklist and handoff clarity
3. teacher-facing explanation of the AI safety chain
4. evidence of database changes and Apply behavior
5. known limitations / future work write-up

## 7. Demo-Ready Summary

If a teammate or instructor asks what Cadensy AI V1 now is, the shortest honest
answer is:

```text
Cadensy uses AI to suggest trip changes and help generate itineraries,
but deterministic Python rules remain the final safety and legality layer.

If the AI output is valid, the product uses it.
If the AI output is invalid or weak, the system safely falls back instead of
corrupting the trip.
```

## 8. Final Status Label

Current status label:

```text
Cadensy AI V1
COMPLETE FOR CAPSTONE
```

Supporting labels:

```text
Chat Agent V1
COMPLETE

Planner V1
COMPLETE FOR CAPSTONE

Deterministic Decision Layer
AUTHORITATIVE

Evaluation Harness
ESTABLISHED
```
