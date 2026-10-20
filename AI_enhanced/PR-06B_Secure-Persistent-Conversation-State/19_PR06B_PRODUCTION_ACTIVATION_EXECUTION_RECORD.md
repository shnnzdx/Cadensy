# PR-06B Production Activation Execution Record

## Purpose and evidence boundary

This document records the actual protected production operations completed for
PR-06B. It contains identifiers and non-secret configuration evidence only.
SecureString values, database connection strings, bearer tokens, and Provider
credentials are intentionally excluded.

The release procedure remains defined by
`18_PR06B_FULL_STACK_RELEASE_RUNBOOK.md`. Future production-gate execution
evidence must be appended here in the same operator turn, before reporting the
gate complete.

The read-only AWS resource and cost closeout is recorded separately in
`20_PR06B_RELEASE_CLOSEOUT_AWS_INVENTORY.md`.

## Current gate status — 2026-10-20 CDT

| Gate | Result | Evidence summary |
| --- | --- | --- |
| Gate A | **PASS** | Local real-browser + PostgreSQL + Fake Runtime product loop accepted. This was not a real Provider production validation. |
| Gate I1 | **PASS** | Encrypted snapshot/restore rehearsal, KMS, TLS, schema/catalog, integrity, and read-only compatibility evidence accepted separately. |
| Gate I2 | **PASS** | Production cut over to the encrypted RDS under the approved maintenance procedure; the original database and required snapshots were retained. |
| Gate M | **PASS** | Run `39446466889`; migration task `a8651677c2754d74a9a5500ebabf6c67`; final schema head `b7d3a1e9c642`. |
| Gate R rollback validation | **PASS** | Corrected isolated TD20 task `6bfe4f7a7f75422586a698b3250b9c14` exited `0` with all four required CloudWatch markers. |
| Gate R | **PASS** | Run `39458246388`; Backend TD23 stable at desired/running/pending `1/1/0`, ALB healthy, `/api/health` HTTP 200. |
| Gate F | **PASS** | Run `39462252381`; Frontend TD17 stable at desired/running/pending `1/1/0`, ALB healthy, approved digest running, and public routes HTTP 200. |
| Full-stack browser acceptance | **PASS WITH NON-BLOCKING OBSERVATIONS** | Real Chromium login, Trip navigation, Conversation create-or-resolve/history reload, and explicit Runtime-unavailable behavior passed without final-run console, page, network, or API errors. |
| Real Provider / Durable Runtime | **NOT APPROVED** | Provider credentials remain absent and Durable/Fake Runtime remain disabled. |

## Gate M provenance correction

PR #22 fixed Gate R provenance and rollback validation in
`.github/workflows/cd01-production-release.yml` and merged as
`139abd07c7ee91e8efd91a621e23f1feb6f713de`. The workflow now keeps the
following identities distinct:

- migration source SHA: `db95d76122c721626b77ed2701f4f395c4974f35`;
- Gate M workflow SHA: `461ece53f718a5dd750d7ad20af5bfc55ce78c7b`;
- Gate M workflow run: `39446466889`;
- Gate M migration task:
  `arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/a8651677c2754d74a9a5500ebabf6c67`;
- migration-runner digest:
  `sha256:d15c76c845d6e68c016dea114dec92b3d7c82421fdbba4d1f653a7039508fe91`.

The accepted historical Gate M Environment record was `state=skipped`; it is
retained as a process exception and is not described as an ordinary approval.
The maintenance-rule timing gap is likewise retained as follow-up evidence. The
successful schema migration was not rerun.

## TD20 rollback cold-start validation

The rollback baseline was:

- task definition:
  `arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:20`;
- image digest:
  `sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34`.

The first isolated validation task,
`3e9f8bba06f44ae389108a107085b702`, proved image pull, real Uvicorn startup,
localhost health, and graceful application shutdown, but the outer shell exited
`143` before emitting the required markers. This was classified as a validation
harness termination defect and was not accepted as PASS.

The separately authorized corrected task was:

- task ARN:
  `arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/6bfe4f7a7f75422586a698b3250b9c14`;
- task definition: `tripsync-backend:20`;
- platform: Fargate `1.4.0`;
- exit code: `0`;
- log stream:
  `ecs/tripsync-backend/6bfe4f7a7f75422586a698b3250b9c14`.

It cold-pulled the original digest without the historical `schema1 conversion`
error, started the real Uvicorn application, received HTTP 200 from localhost
`/api/health`, verified the Alembic head through a read-only transaction,
validated Runtime-off and HMAC availability without printing the key, sent the
child process SIGTERM, and observed graceful shutdown. CloudWatch contains each
required marker exactly once:

```text
ROLLBACK_COLD_START_OK
ROLLBACK_SCHEMA_HEAD=b7d3a1e9c642
ROLLBACK_HEALTH_OK
ROLLBACK_RUNTIME_OFF_OK
```

## Gate R actual production release

Gate R used `CD-01 — Manually Approved Production Backend Release` from the
repaired `main` workflow with one normal `Main` Environment `state=approved`
review. No administrator bypass or bulk approval was used.

| Field | Actual value |
| --- | --- |
| GitHub Actions run | `39458246388` — `success` |
| Workflow head SHA | `139abd07c7ee91e8efd91a621e23f1feb6f713de` |
| Approved application source SHA | `db95d76122c721626b77ed2701f4f395c4974f35` |
| Runtime digest | `sha256:ba76821c460de90e2dbc3491fb8d3c7b23e3184db2a34e131186084f01f8b602` |
| Migration-runner digest | `sha256:d15c76c845d6e68c016dea114dec92b3d7c82421fdbba4d1f653a7039508fe91` |
| Read-only schema preflight task | `arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/a750ff3a4b5b493a84d8decaabda146f` |
| Schema preflight TD / exit | `tripsync-backend:22` / `0` |
| Verified schema head | `b7d3a1e9c642` |
| New production backend TD | `arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:23` |
| Running production task | `arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/9626d080faca4ef1aebb2b420e22663f` |
| ECS final capacity | desired `1`, running `1`, pending `0` |
| Deployment | `COMPLETED`; circuit breaker and rollback enabled; bounds `100/200` |
| ALB target | Backend target — `healthy` |
| API smoke check | `https://app.cadensy.top/api/health` — HTTP 200, `{"ok":true}` |

The deployed profile was independently re-read from TD23 after stabilization:

- `APP_ENV=production`;
- `MOCK_AI=0`;
- `CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=0`;
- `CADENSY_E2E_FAKE_RUNTIME=0`;
- `DISABLE_SCHEDULER=1`;
- `DEV_ALLOW_MEMBERSHIP_HEADER=0`;
- `DATABASE_URL` and `CONVERSATION_SUBMISSION_HMAC_KEY` remain secret
  references;
- no known Provider credential environment or secret entry is present.

ECS recorded deployment completion and steady state. The circuit breaker did
not roll back, and the TD23 CloudWatch stream showed normal Uvicorn startup and
repeated health-check responses without a new application error.

## Gate F actual production release

Gate F used `CD-01 — Manually Approved Production Frontend Release` from the
current `main` workflow. Only run `39462252381` received one normal `Main`
Environment `state=approved` review by an authorized reviewer; no administrator
bypass or bulk approval was used.

| Field | Actual value |
| --- | --- |
| GitHub Actions run | `39462252381` — `success` |
| Workflow head SHA | `139abd07c7ee91e8efd91a621e23f1feb6f713de` |
| Approved application source SHA | `db95d76122c721626b77ed2701f4f395c4974f35` |
| Approved frontend digest | `sha256:f78297e5bbabd14e469da9e022558603e078389d11ba0b7ebfba6c06bad171ec` |
| New production frontend TD | `arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-frontend:17` |
| Running production task | `arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/0f19900287ea42fcb25d4fb2689abdcd` |
| ECS final capacity | desired `1`, running `1`, pending `0` |
| Deployment | `COMPLETED`; one deployment; circuit breaker did not roll back |
| ALB target | Frontend target — `healthy` |
| Direct route checks | `/`, `/login`, `/trip-app/index.html`, `/trip`, and `/api/health` — HTTP 200 |
| Release job interval | `2026-10-20T16:59:59Z` to `2026-10-20T17:03:25Z` |

Before approval, the rollback baseline was verified independently rather than
accepted from its task-definition reference alone:

- task definition: `tripsync-frontend:16`;
- image tag: `6e3a4efb32d864d7240a1e4b8ee043d51d13a3fd`;
- resolved image digest:
  `sha256:00869e84b2bcb3dd9b238e1400996feaf0e014ca4f7aacb83c372f6a45007608`;
- the digest resolved to one unique Docker schema-2 manifest despite carrying
  two tags;
- its config object and all 11 image layers were `AVAILABLE` in ECR; and
- the frontend service's 30 most recent events contained no image-pull or
  schema-1 conversion failure.

TD17 cold-pulled the approved digest, became the sole healthy deployment, and
drained TD16 normally. The frontend service recorded no rollback/failure event.
Backend TD23 remained `COMPLETED` at desired/running/pending `1/1/0`; Gate F did
not modify the Backend, database, Alembic state, SSM, RDS, KMS, or Provider
configuration. A final name-only task-definition inspection confirmed that the
AI Provider selector/model/base-URL settings remain present but no DeepSeek,
OpenAI, or Anthropic credential entry is attached. The separate
`GEOAPIFY_API_KEY` secret supports map data, not model execution. With
`MOCK_AI=0`, Durable Runtime `0`, Fake Runtime `0`, and the scheduler disabled,
Gate F did not make a real AI invocation path available.

The TD17 CloudWatch stream
`ecs/tripsync-frontend/0f19900287ea42fcb25d4fb2689abdcd` showed normal vinext
production startup. During synthetic route changes it also recorded three
non-fatal static-file `Premature close` lines for media/image streams. The task
remained healthy and the final browser run observed no failed same-origin
request; these lines are retained as a non-blocking operational observation.

## Final full-stack browser acceptance

The final acceptance used the repository's existing fixed Organizer account
through a real installed Playwright Chromium executable. Credentials and bearer
tokens were consumed in-process and were not printed or written to this record.
No Provider or fake Runtime was enabled.

| Check | Actual result |
| --- | --- |
| Account login | `POST /api/auth/login` HTTP 200; browser reached `/trip` with an authorized Trip and Membership |
| Trip navigation | Real UI navigation reached Plan and personal Chat before and after a full page reload |
| Conversation create-or-resolve | Two UI-triggered requests returned HTTP 201 |
| History | Two UI-triggered message-history reads returned HTTP 200 |
| Durable identity | Both browser passes resolved the same Conversation ID; the ID itself is intentionally omitted |
| Runtime-unavailable behavior | Exact unavailable message visible; composer disabled before and after reload |
| Unsafe fallback | New-message POST count `0`; legacy `/api/trips/{trip_id}/chat` POST count `0` |
| Endless loading | No `Request saved. Waiting for Cadensy...` state remained |
| Browser health | Final run: page errors `0`, blocking console errors `0`, failed same-origin requests `0`, observed same-origin 5xx responses `0` |
| Backend continuity | `/api/health` HTTP 200 and Backend TD23 remained `1/1/0`, `COMPLETED` |

One earlier stress-style run clicked Plan and immediately switched to Chat,
then refreshed. It captured one Leaflet teardown error:
`Cannot read properties of undefined (reading '_leaflet_pos')`. A repeat using
normal render waits and the product's actual post-refresh navigation did not
reproduce it and completed with zero browser errors. No automatic code change
was made; the transient rapid-navigation race and the three static-stream
`Premature close` log lines remain explicit non-blocking follow-up observations.

PR-06B Gate F and the approved Runtime-off full-stack browser acceptance are
therefore complete. This does not validate a real model response and does not
authorize Provider credentials, Durable Runtime, Fake Runtime, further
production releases, or infrastructure cleanup.

## Release closeout and data-minimization review

This evidence record retains the approved source/workflow identities, GitHub
Run IDs, Task Definition and task identities, immutable image digests, schema
head, approval states, health results, browser results, and non-blocking
observations required to audit the release. Ephemeral ALB target IP addresses
and the reviewer username were removed because they are not needed to prove the
gate result.

The document contains no SecureString value, database connection string,
password, bearer token, Provider credential, customer content, Conversation
text, Conversation ID, Trip ID, Membership ID, or test-account email. The
closeout changed documentation only and performed no additional production
deployment or runtime activation.
