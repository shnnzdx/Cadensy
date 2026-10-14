# PR-06A — Final Acceptance Checklist

- [x] Closed registry rejects dataclass subclasses and serializes fixed approved fields.
- [x] Default stdout sink failure is swallowed without stderr/recursive logging.
- [x] Model, tool, fallback, classification, request-start, and request-end hooks
  use typed context and closed values; construction/mapping/emitter/sink failures
  are fail-open for the business path.
- [x] API terminal categories are structured facts with one start and one terminal
  event per entered handler: success, deterministic clarification, degraded fallback,
  403, 404, 409, runtime-failure 503, typed feature-unavailable 503, and unhandled
  error.
- [x] Pydantic wrapper returns are observed before post-return consumption rejection;
  Pydantic usage is conservatively `not_reported` until provider provenance exists.
- [x] Legacy in-memory provenance and `AgentRunResult.rounds` remain compatible.
- [x] Schema distinguishes null usage from reported zero and rejects invalid tokens.
- [x] No raw prompts, tool arguments/results, model output, provider body, exception
  text, identifiers beyond opaque correlation ids, or secrets are telemetry fields.
- [x] No AWS, deployment, migration, merge, or API/domain/transaction/idempotency
  redesign occurred.
- [x] Hosted CI for `4d7a9236f1a255e88e14831e573a273d3c732648` completed:
  [CI Regression Gates #76](https://github.com/shnnzdx/Cadensy/actions/runs/38644609017)
  passed its Legacy full regression, isolated Pydantic compatibility, production
  Pydantic image proof, migration-runner image proof, and frontend regression/build.
- [x] Hosted CI for `7c0566a866fea55d06cead45493bb67143a4425c` completed:
  [CI Regression Gates #78](https://github.com/shnnzdx/Cadensy/actions/runs/38650331806)
  passed **1043 Legacy tests** and **121 isolated Pydantic tests**, plus the
  production Pydantic image, migration-runner image, and frontend regression/build.
- [x] Hosted CI for `d854225a980cf7a7ed1cea0f6e4eca070b4866c7` completed:
  [CI Regression Gates #80](https://github.com/shnnzdx/Cadensy/actions/runs/38651149761)
  passed **1043 Legacy tests** and **122 isolated Pydantic tests**, plus the
  production Pydantic image, migration-runner image, and frontend regression/build.

## Correlation and lifecycle limits

- `domain.classification_completed` receives the request context but intentionally
  has only `request_id` on deterministic service/history and Preview classification
  paths. It does not fabricate `trace_id` or `execution_id`; agent model/tool events
  carry those IDs only at their actual runtime boundary.
- Legacy `record_agent_lifecycle()` is a compatibility trace no-op. New safe lifecycle
  events are separately wired from real `AgentLifecycleEvent` phases through the
  no-throw telemetry emitter. These statements are not a claim of legacy trace output.
- Production CloudWatch/stdout delivery has **not** been verified. No AWS operation,
  deployment, production-database access, or paid-provider request was performed.

## Required final review evidence

Run targeted telemetry/privacy/runtime tests, the existing chat and lifecycle suites,
then the repository's CI-equivalent regression command in a healthy test database.
Do not label any suite successful unless pytest produced its terminal success summary.

The source is ready for final independent review, not independently approved for
production deployment. **Production AWS Verification Pending Separate Authorization.**
Cloud delivery configuration remains deployment/review evidence, not an assertion made
by this source change. `record_agent_lifecycle()` remains disabled, therefore PR-06A
must not be independently promoted to production while lifecycle telemetry remains
disabled.
