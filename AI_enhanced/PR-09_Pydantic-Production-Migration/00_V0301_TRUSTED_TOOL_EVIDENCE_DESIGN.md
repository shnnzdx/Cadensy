# v0.3.0-01: Trusted Tool Execution Evidence & Policy Boundary

## Intent and scope

This change removes the revision-bracketed Preview boundary's dependency on
Legacy Runtime raw tool-result dictionaries. It does not activate the Pydantic
runtime, add a tool, alter routing, alter persistence, or change an HTTP DTO.

## Evidence contract

`backend/app/agents/runtime_contract.py` owns two framework-neutral immutable
DTOs:

- `ToolExecutionEvidence`: approved tool name, dispatch status, and detached
  replacement candidates only when a scoped `find_replacement_place` dispatch
  succeeded or hit the same-run cache.
- `ReplacementCandidateEvidence`: candidate id, title, place, coordinates, and
  optional price. Construction rejects malformed names, coordinate ranges, and
  invalid prices.

The status vocabulary represents success, guard rejection, failure, timeout,
cancellation, same-run cache hit, and unsupported dispatch. A failed, timed-out,
or cancelled execution gets a fixed `agent_execution` marker with no candidates;
it never returns usable successful tool evidence. A late result is never
accepted by the established deadline boundary.

## Ownership and lifetime

Only the Legacy runtime adapter reads `base.AgentRunResult.tool_results`. It
maps approved completed calls to detached contract data, emits a fixed
`unsupported` marker for unknown calls, and leaves existing Legacy tool guards
and per-`call_agent` cache semantics untouched. Raw results remain available to
the older Legacy chat application service only; Preview no longer requests or
stores them.

Preview receives evidence with the `RuntimeResult` produced inside its captured
Plan/facts revision bracket. It accepts replacement fields only when every
replacement value exactly matches one candidate in successful or same-run-cache
`find_replacement_place` evidence. It rechecks the captured revisions before
classification and again before returning. Evidence is neither persisted nor
sent in the HTTP response, so it cannot be replayed across runs, scopes, or
facts revisions.

## Compatibility and rollout

The Pydantic runtime consumes the same `RuntimeResult` contract, records only
its existing scoped `get_current_plan` completion, and has no replacement
evidence or new tools. No provider, dependency, feature flag, database schema,
telemetry schema, or API contract changes in this work item.
