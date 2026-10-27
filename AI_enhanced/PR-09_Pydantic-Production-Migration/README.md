# PR-09 — Pydantic Production Migration Evidence

## Current release status — 2026-10-27

The Pydantic AI migration is complete and released as
[`v0.3.0`](https://github.com/shnnzdx/Cadensy/releases/tag/v0.3.0) at main
commit `ea7c9e3c8c4da3c4a518e47ffc528df5ed199233`.

Production is currently running:

| Surface | Current verified state |
| --- | --- |
| ECS service | `tripsync-backend-service` |
| Task definition | `tripsync-backend:31` |
| Runtime selector | `CHAT_AGENT_RUNTIME=pydantic` |
| Durable Conversation production latch | `1` |
| Backend image | `tripsync-backend@sha256:a9f9ddfbdd506284c505c09a20bc864efefb1792408771016e83c0f69761d9c3` |
| Service counts | running/desired/pending `1/1/0` |

The protected activation is recorded in
[CD-03 run 40382252075](https://github.com/shnnzdx/Cadensy/actions/runs/40382252075).
The frontend closeout and production browser acceptance are included in the
v0.3.0 release, but this directory remains focused on backend migration
evidence.

## Reading historical evidence correctly

Files `00_` through `30_` are preserved, point-in-time engineering evidence.
They intentionally retain the authorization and deployment state at the time
of each review. In particular, earlier phrases such as “PR #31 unmerged”,
“production change authorization not granted”, or “Pydantic activation not
authorized” must not be read as the current deployment state.

The migration chronology is:

1. `00_`–`10_`: Trusted Tool Execution Evidence and functional-parity design,
   implementation, evaluation, and review.
2. `13_`–`18_`: durable lifecycle acceptance and the deep-immutability
   evidence closure.
3. `19_`–`24_`: concurrency, idempotency, recovery, and fencing acceptance.
4. `25_`–`30_`: formal Legacy/Pydantic evaluation and PostgreSQL fixture
   isolation closure.

No document in this directory contains credentials, raw provider payloads, or
private Trip data.
