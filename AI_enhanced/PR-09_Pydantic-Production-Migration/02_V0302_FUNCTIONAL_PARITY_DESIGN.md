# v0.3.0-02: Pydantic AI Functional Parity Design

## Scope and baseline

This work starts from `5ce3782` on `codex/v0302-pydantic-functional-parity`.
It keeps the production selector on Legacy. Pydantic is exercised only through
the existing injected `FunctionModel` and scoped read-only capability seam.

## End-user workflows

- Answer a plan question after reading the scoped Current Plan.
- Read safe trip facts without exposing membership identifiers, private
  constraints, ORM objects, or a Session.
- Read a scoped item, look up a replacement, and return a typed venue patch
  only when the returned candidate becomes immutable execution evidence.
- Ask the existing `propose_options` tool for bounded, advisory alternatives;
  the existing Preview and Durable Conversation paths remain their authority.
- Treat `selected_item_ref` only as an instruction-level hint. A suggestion
  still has to reference an item returned by the scoped Current Plan.

## Tool exposure and policy

Pydantic exposes only the existing capability-bound handlers:

| Tool | Policy | Evidence |
| --- | --- | --- |
| `get_current_plan` | Starts/extends the scoped read trajectory. | `succeeded` or same-run `cache_hit`. |
| `get_trip_facts` | Safe independent read. | `succeeded` or same-run `cache_hit`. |
| `find_replacement_place` | Requires a Current Plan read and an item id observed in that read. | Candidate evidence only on success/cache hit. |
| `propose_options` | Requires a Current Plan read; output is advisory. | Dispatch evidence only. |

The adapter owns a small per-run policy state: inspected plan item ids,
approved tool results, and a cache keyed by tool name plus canonical arguments.
It is created inside `_run_async`, cannot cross capability/execution/revision
boundaries, and never exposes a generic query/write API. Tool wrappers validate
their typed arguments before calling the existing handler and use the existing
PR-01C `before_tool`/`after_tool` deadline boundary. Guard rejections append
only `guard_rejected` evidence and cannot call a handler.

## Typed output and options

The suggested-change output admits only Preview-approved scalar fields:
`start_hour`, `day_date`, `duration_min`, `title`, `place`, `lat`, `lng`, and
`price_per_person`. Validation rejects unknown/nested values, bool-as-number,
non-finite values, invalid dates/coordinates/prices, and incomplete venue
groups. Typed admission is syntax validation only; Preview remains the business
and replacement-provenance authority.

Candidate options are taken from the existing scoped `propose_options` output,
not invented as a new persistence format. The adapter converts only complete,
single-item, syntactically valid options into `AgentCandidateOption`. Preview
continues to scope, revalidate revisions, classify, and omit unsafe options.
`propose_options` does not grant replacement authority: an option containing
venue fields is omitted unless an earlier same-run replacement lookup supplied
the exact candidate evidence. Cross-day prerequisite checks in the existing
Legacy policy are attached to its agent-visible `classify_change` tool; that
tool is deliberately not exposed here, and RevisionBracketedPreview performs
the authoritative classification under captured revisions instead.

## Trusted evidence and classification

Replacement candidates are detached through the v0.3.0-01 immutable DTOs only
after an approved handler returns. The typed patch must then exact-match that
evidence in the existing `_replacement_provenance_allows` gate. Raw model text,
tool-shaped dictionaries, guard rejection, timeout, cancellation, failure, or
late completion cannot authorize a venue change.

`classify_change` is intentionally not exposed to Pydantic. Existing
`RevisionBracketedPreview` executes deterministic classification under current
scope and revisions; a model-visible duplicate would add latency without adding
authority or a required business capability.

## Changed-file plan

- `backend/app/agents/pydantic_runtime.py`: tool wrappers, per-run policy/cache,
  typed patch validation, evidence mapping, and option conversion.
- `backend/tests/test_pydantic_runtime.py`: deterministic fake-model unit and
  policy coverage, PostgreSQL Preview integration, and Durable Coordinator
  integration.
- `03_V0302_FROZEN_AB_EVALUATION.json`: a new, versioned execution artifact
  over the frozen PR-04D3 input; it does not modify historical input or reports.
- This design record; frozen PR-04 inputs/reports remain unchanged.

## Security and compatibility test plan

Tests will use `FunctionModel` only and assert actual scoped handler calls for
Trip Facts, replacement search, and options. They cover prerequisite failures,
unknown item ids, invalid arguments, same-run cache behavior, forged evidence,
deadline/failure normalization, selected-item hints, immutable evidence,
Preview no-write behavior, and Legacy regression. PostgreSQL/Durable Conversation
tests are run only with a newly verified disposable cluster. No production
provider, selector, schema, API, dependency, or deployment change is in scope.
