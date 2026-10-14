# PR-06A — Final Acceptance Checklist

- [x] Closed registry rejects dataclass subclasses and serializes fixed approved fields.
- [x] Default stdout sink failure is swallowed without stderr/recursive logging.
- [x] Model, tool, fallback, classification, request-start, and request-end hooks
  use typed context and closed values; construction/mapping/emitter/sink failures
  are fail-open for the business path.
- [x] API terminal categories are structured facts with one start and one terminal
  event per entered handler: success, deterministic clarification, degraded fallback,
  403, 404, 409, 503, and unhandled error.
- [x] Pydantic wrapper returns are observed before post-return consumption rejection;
  Pydantic usage is conservatively `not_reported` until provider provenance exists.
- [x] Legacy in-memory provenance and `AgentRunResult.rounds` remain compatible.
- [x] Schema distinguishes null usage from reported zero and rejects invalid tokens.
- [x] No raw prompts, tool arguments/results, model output, provider body, exception
  text, identifiers beyond opaque correlation ids, or secrets are telemetry fields.
- [x] No AWS, deployment, migration, merge, or API/domain/transaction/idempotency
  redesign occurred.
- [ ] Hosted CI for this final-corrections commit — pending push and GitHub execution:
  [CI workflow](https://github.com/shnnzdx/Cadensy/actions/workflows/build-validation.yml).

## Required final review evidence

Run targeted telemetry/privacy/runtime tests, the existing chat and lifecycle suites,
then the repository's CI-equivalent regression command in a healthy test database.
Do not label any suite successful unless pytest produced its terminal success summary.

The source is ready for final independent review, not independently approved for
production deployment. **Production AWS Verification Pending Separate Authorization.**
Cloud delivery configuration and a full clean CI run remain deployment/review evidence,
not assertions made by this source change. `record_agent_lifecycle()` remains disabled,
therefore PR-06A must not be independently promoted to production while lifecycle
telemetry remains disabled.
