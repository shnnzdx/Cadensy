# Release B — Final Production Acceptance and Closeout

**Decision:** **RELEASE B — ACCEPTED AND CLOSED**  
**Closeout date:** 2026-10-22  
**Production changes made by this closeout:** none  
**New Provider requests made by this closeout:** 0

## 1. Executive Summary

Cadensy Release B enabled the already-reviewed Durable Legacy Chat runtime in
production with DeepSeek only. The protected activation, bounded real-provider
acceptance, durable persistence, server-owned context recovery, idempotency,
safe telemetry checks, and production health verification all passed. Release B
is closed; it is not an open infrastructure experiment or an authorization for
additional deployment work.

## 2. Release B Scope

Release B activated the production runtime only. It reused the verified Release
A backend image, registered a new backend task definition, attached one
approved DeepSeek SSM secret reference without reading its value, and enabled
the two Durable Conversation gates. It did not change RDS schema, IAM, KMS,
networking, frontend code, Protected Apply, or other business-write paths.

The activation control plane is documented in
`00_CD02_RELEASE_B_CONTROLLED_ACTIVATION_IMPLEMENTATION.md`. The successful
controlled workflow was [run 39827327329](https://github.com/shnnzdx/Cadensy/actions/runs/39827327329),
using tooling SHA `72eda0d919d75a7ed40561313203d52f39ecffd7` and normal `Main`
Environment approval.

## 3. Production Architecture and Runtime Profile

| Surface | Accepted production state |
| --- | --- |
| AWS account / region | `448678332746` / `us-east-1` |
| ECS cluster / service | `tripsync-cluster` / `tripsync-backend-service` |
| Active backend task definition | `tripsync-backend:26` |
| Runtime-off rollback baseline | `tripsync-backend:25` |
| Runtime | Legacy Durable Chat |
| Provider / model | DeepSeek / `deepseek-flash` |
| Thinking | Disabled |
| `MOCK_AI` / Fake E2E runtime | `0` / `0` |
| Durable Runtime base gate / production latch | `1` / `1` |
| Provider credential | One approved ECS SSM reference; value was never read or recorded |
| Generated activation profile | DeepSeek-only; Ollama environment configuration and secret references: `0` |

## 4. Infrastructure Activation Evidence

- CD-02 workflow conclusion: **SUCCESS**.
- `Main` Environment approval: **APPROVED**.
- Backend task definition became `tripsync-backend:26`; no rollback occurred.
- The activation preserved the fixed Release A backend image digest and the
  existing database, HMAC, IAM roles, networking, and logging configuration.
- The production service reached the expected state: desired/running/pending
  `1/1/0`, primary rollout `COMPLETED`, ALB target healthy, and `/api/health`
  HTTP `200`.

## 5. Real DeepSeek Acceptance Evidence

Two approved, serial, non-sensitive production submissions were made through
the Cadensy production API using the existing fictional test account and Trip.
No automatic HTTP retry or submission recovery was used.

| Evidence | Result |
| --- | --- |
| Turn 1 terminal state | `completed` |
| Turn 2 terminal state | `completed` |
| Provider / model / invocation source | `deepseek` / `deepseek-flash` / `provider_sdk` |
| Provider calls | 3 total: Turn 1 = 2; Turn 2 = 1 |
| Tool-call telemetry | 2 controlled calls in Turn 1; 0 in Turn 2 |
| Provider failure categories | None |
| Unplanned provider retries | None observed |

Opaque submission identifiers, credentials, prompts, assistant text, tool
arguments, and tool results are intentionally omitted from this record.

## 6. Durable Conversation and Context Recovery

Turn 1 created exactly one persisted User message and one Assistant message.
After a fresh production authentication session, the same server-owned Personal
Conversation was recovered with those two messages. Turn 2 then completed and
the durable History became:

```text
user → assistant → user → assistant
```

The Turn 2 reply satisfied the non-sensitive continuity heuristic against the
previous Assistant reply. This is evidence of production API re-authentication
and server-side PostgreSQL context reconstruction; it is **not** a new Chromium
browser E2E certification.

## 7. Idempotency and Privacy Validation

- Replaying each submission with the same Idempotency Key returned the same
  submission identity and did not add a User message, Assistant message, or
  Provider invocation.
- Targeted CloudWatch scans for the two opaque submission correlations found
  zero matches for the two test-prompt patterns, `Authorization`, `Bearer`, or
  `DEEPSEEK_API_KEY`.
- Only allowlisted telemetry metadata was queried: provider/model/source,
  invocation outcome, token counts, elapsed time, tool classification, terminal
  state, and failure category. No raw log message, credential, prompt, tool
  argument, tool result, or user content was exported.
- The acceptance did not execute Apply, Vote, Booking, migration, or other
  Protected Write operations.

## 8. Token Usage and Estimated Cost

| Usage evidence | Value |
| --- | ---: |
| Prompt tokens | 8,452 |
| Completion tokens | 364 |
| Total reported tokens | 8,816 |
| Cross-submission manual stop threshold | 20,000 |
| Threshold status | Below threshold |

Using the official DeepSeek Flash pricing applicable at the verification time,
the estimated charge is approximately **$0.00149** if all input tokens were
cache misses, or approximately **$0.00024** if all input tokens were cache
hits. This is a test-call estimate, not an AWS operating-cost total or a billing
statement. The provider usage did not expose a cache-token breakdown.

The 20,000-token control was a human acceptance stop threshold. It was not a
strictly enforced cross-request spending cap; the runtime's own per-submission
round/token controls are separate safeguards.

## 9. Production Health

Health was checked after both real-provider submissions:

- Backend: `tripsync-backend:26`
- ECS desired/running/pending: `1/1/0`
- ECS primary rollout: `COMPLETED`
- ALB target health: `healthy`
- API health: HTTP `200`

No production health regression was observed.

## 10. Known Limitations

1. This acceptance did not repeat Chromium UI E2E; it verified fresh API
   authentication and durable server history.
2. Cross-request Provider-call and token/cost budgets remain procedural,
   operator-enforced limits rather than hard product-level quotas.
3. Provider usage monitoring, rate limiting, and abuse safeguards require
   separate product/operations work before broad real-user rollout.
4. This personal-conversation acceptance did not seek to manufacture a Plan
   Assistant candidate option or invoke Protected Apply.

## 11. Rollback Strategy

The active service remains on `tripsync-backend:26`. The retained emergency
Runtime-off baseline is the exact existing `tripsync-backend:25` task definition.
No rollback is to be performed merely because that historical baseline retains
legacy Ollama environment fields. It is a known working Runtime-off recovery
point, not evidence that Ollama is running.

If a real production incident warrants rollback, obtain separate authorization
and use the protected CD-02 `runtime_off_rollback` operation. That procedure
must verify that both Durable gates return to `0`, the DeepSeek credential is
not attached, and ECS/ALB/API health recovers. It must preserve Durable History
and must not depend on the DeepSeek secret.

## 12. Outstanding Non-blocking Operational Items

- Historical Ollama AWS cleanup is **DEFERRED — NON-BLOCKING**. Do not delete
  the legacy SSM parameter, change IAM, deregister historical task definitions,
  or alter `:25` in this phase.
- A future approved cleanup needs a DeepSeek-only Runtime-off baseline with no
  Ollama namespace before it can retire the current fallback baseline.
- Provider usage monitoring, rate limiting, hard cost budgets, and abuse
  protection are future operational work, not Release B blockers.

See `AWS/DEEPSEEK_ONLY_OLLAMA_RETIREMENT_PLAN.md` for the metadata-only
inventory and separately authorized retirement order.

## 13. Final Acceptance Decision

| Decision area | Result |
| --- | --- |
| Release B Infrastructure | **PASS** |
| Release B Functional Acceptance | **PASS** |
| Release B Final Decision | **CLOSED** |

**RELEASE B — ACCEPTED AND CLOSED.** The active DeepSeek Durable Chat deployment
is frozen as the successful production baseline until separately authorized
product or operational work is scheduled.

## 14. Handoff to Next Phase

The next phase is product development, not another Release B architecture or
deployment review. Independently scoped candidates are:

1. AI Chat UI/UX and Conversation History experience.
2. Agent Suggestion presentation and Plan Assistant collaboration flow.
3. Loading, pending, failure, refresh, and re-login UX refinement.
4. Provider-usage monitoring, rate limiting, hard token/cost budgets, and
   real-user abuse protection.
5. Deferred, separately authorized Ollama AWS resource retirement after a
   clean DeepSeek-only Runtime-off rollback baseline exists.

None of these items reopens Release B or authorizes an ECS, RDS, IAM, KMS,
Secret, deployment, Provider, or rollback operation.
