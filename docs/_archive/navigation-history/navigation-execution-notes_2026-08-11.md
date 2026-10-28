# Navigation Execution Notes

Recorded on August 11, 2026 after the Candidate 1 migration plan was frozen and before
Phase 1 implementation.

## Shared Route Codec Ownership

The pure transformation:

```text
WorkspaceRouteRef <-> workspace route string
```

must not live under `trip/src/final/` if both runtimes need it.

Planned ownership:

```text
shared pure route codec
    ↓
frontend host-entry / preview adapter
    → iframe / browser navigation

shared pure route codec
    ↓
trip workspace navigation adapter
    → React Router navigate()
```

The existing preview contract in [shared/tripsync-preview-contract.js](C:/Users/zdxzh/Desktop/capstone/New/shared/tripsync-preview-contract.js)
already owns host/embed handoff concerns. The future shared route codec should remain pure
and should not perform browser navigation.

`navigation-normalizers` must reuse that shared route parser/serializer rather than
introducing a second independent `workspace path -> WorkspaceRouteRef` parser.

## Approved `TripState = "unknown"` Target-Policy Semantics

These rules are for the Phase 2 target-policy matrix. They do not change current runtime
behavior during Phase 1.

- Default landing with unknown state:
  choose the normal role-based safe landing; unknown state alone does not force a redirect
  away from the trip.
- Reachable trip route with unknown state:
  if the route is otherwise reachable by membership and role, unknown state does not block it.
- Role-restricted route with unknown state:
  role restrictions still apply; unknown state never widens access.
- Restored selection with unknown state:
  a restored selection may still win when the selected trip is otherwise reachable.
- Return target with unknown state:
  a valid reachable return target may still win when role-based reachability passes.
- Invite/join flow with unknown state:
  invite precedence and join destination policy should continue to work from invite and
  membership facts even when trip state is unknown.

These semantics stay within the frozen public contract and do not introduce extra API states.

## Invite Trip Identity Gap

As of Phase 5 on August 11, 2026, the current invite preview payload still does not
reliably carry `tripId`.

This remains intentionally unresolved by the shared workspace route codec:

```text
WorkspaceRouteRef <-> workspace route string
```

The codec must not infer invite-trip identity from route serialization or from the
invite token itself.

This stays a prerequisite for the later invite runtime cutover:

```text
repeated-invite classification requires reliable invite-trip identity
```
