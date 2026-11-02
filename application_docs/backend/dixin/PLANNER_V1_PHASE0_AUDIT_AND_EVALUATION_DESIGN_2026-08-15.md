# Planner V1 Phase 0 Audit And Evaluation Design

Last updated: 2026-08-15

## 1. Purpose

This document is the Phase 0 baseline for the current Planner line, updated to
match the approved Phase 1 direction.

It does not propose a new agent architecture.
It does not try to redesign the existing `generator.py` pipeline.
Its job is narrower:

- audit what the Planner actually is today
- identify the highest-risk blind spots
- define the minimum honest real-AI evaluation harness we need before further
  Planner tuning

This follows the same philosophy already established for Chat Agent:

```text
Do not tune behavior without a repeatable ruler.
```

## 2. Current Planner Reality

Planner is not an agent.

The current path is:

```text
Trip / preferences / constraints / place pool
    -> domain/plans/generator.py
    -> legal day candidates
    -> agents/planner.py single model call per day
    -> deterministic post-parse filtering
    -> deterministic meal anchors
    -> deterministic full-plan validation
    -> rules fallback when the planner day is unusable
    -> persisted PlanItem rows
```

Important boundary:

- `backend/app/agents/planner.py` only chooses `candidate_id` + `start_hour`
- `backend/app/domain/plans/generator.py` owns candidate legality, fallback, meal insertion, full-plan validation, and database writes
- `backend/app/domain/places/service.py` owns place supply quality

So Planner quality is not one thing.
It is the combined result of:

1. place supply quality
2. candidate filtering quality
3. single-day model selection quality
4. meal-anchor insertion quality
5. full-plan validation and fallback behavior

## 3. What Already Exists

The current codebase already has several good ingredients:

### 3.1 Strong day-level planner contract

`backend/app/agents/planner.py`

The model is tightly boxed in:

- output schema is fixed
- only `candidate_id` and `start_hour` are accepted
- picks must be 2-4 items
- times must be quarter-hour increments
- category/window compatibility is revalidated
- opening and closing times are revalidated
- one automatic repair retry exists for invalid model output

This is a good V1 foundation.

### 3.2 Deterministic outer pipeline

`backend/app/domain/plans/generator.py`

The Planner does not directly own plan legality.
The generator still owns:

- organizer/preference readiness
- required-constraint loading
- per-day legal candidate filtering
- rules fallback
- meal insertion
- final validation
- persistence

That means bad model behavior can still degrade to rules instead of directly corrupting the itinerary.

### 3.3 Existing regression coverage

There is already meaningful automated coverage in:

- `backend/tests/test_planner.py`
- `backend/tests/test_plan_generation.py`
- `backend/tests/test_plan_day_variety.py`

Current tests cover:

- schema validation and repair retry
- used-AI metadata behavior
- mixed planner/rules generation outcomes
- blocked reasons
- budget and constraint compliance
- some day-variety regression protection

This is useful, but it is still not a Planner evaluation harness.

## 4. Phase 0 Audit Findings

### 4.1 The main missing piece is evaluation, not another AI feature

The biggest gap is exactly what the Yuming documents warned about:

- Chat has `app/agents/agent-server/run_real_trip_tools_trace.py`
- Planner has no equivalent real comparison harness

Today, changing Planner prompt or ranking logic still largely means:

```text
change code
generate a trip
look at it by eye
guess whether it improved
```

That is too weak for a system with this many interacting layers.

### 4.2 The existing planner diagnostic script is not a reliable baseline

`backend/scripts/diagnose_planner.py`

This script is not a usable evaluation harness in its current state.

Observed problems:

- it constructs `planner.PoiOption(...)` without the required `candidate_id`, `local_name`, `category`, `latitude`, `longitude`, and `opening_hours` fields
- it prints `pick.poi_name`, but `planner.Pick` only has `candidate_id` and `start_hour`
- it exercises only the day-level planner adapter, not the real generator pipeline
- it does not seed or inspect a real trip
- it does not compare equivalent isolated real-AI runs

Practical conclusion:

- this script is not the Planner equivalent of the chat trace tool
- Phase 1 should either replace it or clearly supersede it with a real Planner eval harness

### 4.3 Planner quality is currently spread across three seams

Planner regressions can come from at least three different layers:

1. `backend/app/domain/places/service.py`
   bad place supply, weak names, poor geographic spread, missing hours
2. `backend/app/domain/plans/generator.py`
   bad candidate pool shaping, bad fallback behavior, meal routing, validation edge cases
3. `backend/app/agents/planner.py`
   bad day prompt or poor candidate selection behavior

If the evaluation harness only looks at the final day output, it will be too hard to localize failures.

Phase 0 conclusion:

- the harness must preserve intermediate evidence, not just final itinerary rows

### 4.4 The current prompt contains at least one instruction the day model cannot fully honor

`backend/app/agents/planner.py`

The prompt says:

- vary exact start times naturally between days
- do not repeat `10.0`, `14.0`, `19.0` every day

But the model is called one day at a time.
It does not receive previous days' chosen times as explicit structured context.

This does not mean the prompt is useless.
It does mean we should not evaluate Planner as though the model had true cross-day visibility.

Evaluation implication:

- cross-day time variety must be measured at the full-generator level
- if we want to score cross-day time variety fairly, we should attribute failures carefully between prompt limits and generator behavior

### 4.5 Fallback behavior is a product feature and must be evaluated, not hidden

`backend/app/domain/plans/generator.py`

The Planner line is intentionally hybrid:

- try planner day
- if unusable or unavailable, use deterministic rules

This means a "good" evaluation does not always mean "used AI on every day".

A correct evaluation must track:

- how often AI days survive
- how often the system falls back
- whether mixed planner/rules output remains coherent
- whether fallback days become carbon copies or timing clones

### 4.6 Meal quality is part of Planner quality even though meals are not chosen by the model

Meal anchors are inserted after the sightseeing picks.

That means user-perceived itinerary quality depends on:

- route-aware meal candidate selection
- lunch/dinner timing fit
- meal detour cost
- meal category quality

If we evaluate only sightseeing picks, we will miss a major source of itinerary quality problems.

### 4.7 Place-data uncertainty is a first-class evaluation dimension

The product intentionally preserves unknowns instead of inventing defaults.

That is correct behavior.
But it creates a real evaluation complication:

- Chicago curated/known-duration data is richer
- Geoapify-backed cities often have sparse duration/opening data

So Planner scores must separate:

- model quality under good data
- pipeline quality under sparse data

Otherwise we will blame the model for missing provider facts.

## 5. Phase 0 Audit Verdict

The next best step is:

```text
Build Planner evaluation first.
Tune Planner second.
```

Not because Planner is already perfect.
Because without an honest evaluation harness, any Planner tuning will be anecdotal.

## 6. Approved Phase 1 Direction

Phase 1 is now approved with these authoritative constraints:

- evaluate the current real Planner path only
- do not compare `MOCK_AI=1` versus `MOCK_AI=0`
- use `MOCK_AI=0`
- run exactly two isolated executions per scenario
- keep fixed place supply for both runs of a scenario
- keep only two high-signal scenarios in Phase 1
- report six core metric dimensions only
- do not tune Planner behavior in this phase

The harness should answer these five questions:

1. Did the system generate a complete legal itinerary?
2. How much of the final result actually came from planner days versus rules fallback?
3. Is each day complete enough to be credible under its actual constraints?
4. Is the trip geographically and temporally coherent across days?
5. If the result is weak, does the evidence point to Planner behavior or place/candidate supply?

## 7. Evaluation Scope

### 7.1 In scope

- local backend-only evaluation
- seeded or transaction-scoped temporary trips
- real Planner execution with `MOCK_AI=0`
- two isolated runs per scenario
- per-day and full-trip metrics
- intermediate evidence capture
- human-readable summary output

### 7.2 Out of scope for Phase 1

- no second agent
- no conversion of Planner into a tool-calling agent
- no UI integration work
- no automatic prompt optimization loop
- no judge-model grading step
- no Planner behavior tuning

The first harness should stay deterministic and inspectable.

## 8. Recommended Harness Shape

Recommended new script location:

```text
backend/app/agents/agent-server/run_planner_eval.py
```

Reason:

- chat validation already lives under `app/agents/agent-server/`
- the new script is conceptually parallel to `run_real_trip_tools_trace.py`
- this keeps AI-runtime validation artifacts together

### 8.1 Core execution model

For each scenario:

1. seed or create a temporary trip in one transaction
2. inject preferences, constraints, destination, and fixed place supply
3. run generation once with `MOCK_AI=0` as isolated Run 1
4. capture intermediate and final outputs
5. roll back the run state
6. recreate equivalent input state
7. run generation once with `MOCK_AI=0` as isolated Run 2
8. capture intermediate and final outputs
9. roll back the run state

Run 1 and Run 2 must not share generated plan state.

### 8.2 What to capture

For each day, capture:

- candidate pool size
- sightseeing candidate ids
- meal candidate ids considered for lunch/dinner
- chosen sightseeing items
- inserted meal items
- `generated_by`
- `used_ai`
- `planner_note`

For the full trip, capture:

- final status
- blocked reason
- `generated_by`
- `used_ai`
- day count
- total item count
- number of AI-generated days
- number of rules-fallback days
- reused sightseeing-title counts across days
- repeated exact sightseeing-time-pattern counts across days
- simple intra-day geographic spread

### 8.3 Recommended output format

Produce both:

1. human-readable console summary
2. machine-readable JSON payload

Suggested JSON top-level shape:

```json
{
  "scenario": "chicago_baseline",
  "mode": "real_ai",
  "run_label": "run_1",
  "status": "active",
  "generated_by": "mixed",
  "used_ai": true,
  "blocked_reason": null,
  "days": [],
  "metrics": {},
  "diagnostics": {}
}
```

This keeps later comparison easy without hiding run-to-run differences.

## 9. Evaluation Scenarios

Phase 1 should contain only two scenarios.

### Scenario A: Chicago rich-data baseline

Purpose:

- establish best-case behavior with the richest existing data

Suggested shape:

- 3-4 day trip
- organizer + participant
- moderate budget ceiling
- normal availability
- common interests such as culture + food

Questions:

- does the current real Planner cluster coherent neighborhoods?
- do meals remain route-compatible?
- does the final trip avoid obvious carbon-copy day shapes?

### Scenario B: Fixed sparse-data non-Chicago baseline

Purpose:

- test Planner behavior under thinner provider facts without live provider variability

Requirements:

- choose one supported non-Chicago city
- use fixed reproducible sparse place data
- keep both runs equivalent in place supply

Questions:

- how often does the trip block because candidate quality is thin?
- when it does succeed, how much weakness appears to come from sparse facts rather than model choice?

Do not add tight-budget or partial-availability scenarios in Phase 1.

## 10. Core Metrics

Phase 1 should report exactly six core metric dimensions.

### 10.1 Generation outcome

- generation status: `active` or `blocked`
- `blocked_reason`

### 10.2 Final legality

- final validation passed
- required constraint violations remain zero
- obvious illegal day output should be surfaced

### 10.3 AI survival / fallback

- `generated_by` per day
- AI-generated day count
- rules-fallback day count
- `used_ai`

### 10.4 Day completeness

- sightseeing count per day
- meal count per day
- obviously under-filled days

Do not impose a fake fixed rule such as "must end after 18:00".
Interpret completeness in the context of the day's real availability and window shape.

### 10.5 Cross-day variety

- reused sightseeing titles across the trip
- repeated exact sightseeing start-time patterns across days

### 10.6 Geographic coherence

- simple deterministic intra-day geographic spread

Keep this simple and inspectable.
It is an evaluation signal, not a product rule.

## 11. Diagnostic Context

The harness may also capture supporting evidence such as:

- candidate count per day before planner call
- candidate ids presented to Planner
- candidates with known hours
- candidates with known duration
- candidates with known coordinates
- chosen sightseeing ids
- inserted meal ids
- `planner_note`

These are diagnostic context, not additional scoring metrics.
Their job is to help distinguish Planner/model weakness from place or candidate-pipeline weakness.

## 12. Fixed Place Supply Requirement

Phase 1 must control place-supply variability.

This is especially important for the non-Chicago scenario.

Do not let run quality depend on whatever Geoapify happens to return at that moment.

Use a fixed, reproducible place dataset, cache state, or fixture that is compatible with the existing place pipeline.

Do not redesign place architecture.
Do not invent missing values.
Preserve the current rule:

```text
unknown != invented value
```

## 13. How To Judge Results

Phase 1 is not an A/B comparison project.

The harness exists to establish an honest baseline of current real-Planner behavior.

That means:

- record both runs
- do not collapse them into one synthetic score
- do not hide fallback
- do not tune behavior when you find a quality problem
- record the problem and stop at the baseline

If a scenario blocks, the report should distinguish:

- no usable places
- budget ceiling impossible
- constraints impossible
- planner-day unusable followed by rules failure

Blocked is a valid product outcome.
Unexplained blocked is not.

## 14. Recommended Phase 1 Deliverables

Phase 1 implementation should produce:

1. `run_planner_eval.py`
2. one reusable scenario seed helper
3. one JSON summary format
4. one short operator doc explaining how to run it locally
5. one clear decision on `backend/scripts/diagnose_planner.py`:
   replace it, or supersede it

Do not leave two misleading Planner diagnostic entry points.

## 15. What Not To Do Next

- do not add another Planner-facing AI component yet
- do not turn meals into a second model call yet
- do not tune the planner prompt in Phase 1
- do not treat one attractive generated itinerary as evidence
- do not collapse place-supply problems into model-quality conclusions

## 16. Final Recommendation

Planner V1 should move forward in this order:

1. build the evaluation harness
2. baseline Chicago and one fixed sparse-data city with two isolated real-AI runs each
3. record the six core metric results plus diagnostic context
4. only then begin Planner quality tuning

That is the smallest serious next step that matches the current architecture.
