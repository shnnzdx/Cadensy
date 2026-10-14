# PR-06A — Final Acceptance Checklist

- [x] Closed registry rejects dataclass subclasses and serializes fixed approved fields.
- [x] Default stdout sink failure is swallowed without stderr/recursive logging.
- [x] Model, tool, lifecycle, fallback, classification, request-start, and request-end
  hooks use typed context and closed values.
- [x] Legacy in-memory provenance and `AgentRunResult.rounds` remain compatible.
- [x] Schema distinguishes null usage from reported zero and rejects invalid tokens.
- [x] No raw prompts, tool arguments/results, model output, provider body, exception
  text, identifiers beyond opaque correlation ids, or secrets are telemetry fields.
- [x] No AWS, deployment, migration, merge, or API/domain/transaction/idempotency
  redesign occurred.

## Required final review evidence

Run targeted telemetry/privacy/runtime tests, the existing chat and lifecycle suites,
then the repository's CI-equivalent regression command in a healthy test database.
Do not label any suite successful unless pytest produced its terminal success summary.

The source is ready for final independent review, not independently approved for
production deployment. Cloud delivery configuration and a full clean CI run remain
deployment/review evidence, not assertions made by this source change.
