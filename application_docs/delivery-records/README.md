# Delivery, QA and Release Records

Status: current index of point-in-time evidence
Last reviewed: 2026-11-23

This directory preserves what happened during bounded delivery tasks: pull requests, merges, reviews, QA, artifact publication, migrations, releases and production checks. These reports describe their recorded dates and evidence sources; they are not current operating instructions.

## Records

| Record | What it preserves | Evidence caveat |
| --- | --- | --- |
| [MERGE-PHASE1 — 2026-11-05](MERGE-PHASE1_2026-11-05.md) | Merge sequence and audit trail for PR #49 followed by PR #48 | Merged to Main is distinct from production deployment; the record explicitly says neither merge was deployed or independently verified in production at that time |
| [P0 Activation Funnel — 2026-11-22](P0-ACTIVATION-FUNNEL-FINAL-RECORD_2026-11-22.md) | Owner decisions, PR #54/#55 evidence, backend artifact-release incident, migration and production release history | Several details are DD-reported; raw migration log, PR #57 / 3c3ce0f6 team QA, and real-account smoke are marked NOT VERIFIED in the record |

## Evidence vocabulary

For every future delivery record, distinguish at least:

1. **Merged:** the source change is present on the target branch.
2. **Artifact published:** a build output or immutable image was created/pushed.
3. **Deployed:** the intended production release workflow completed against the stated target.
4. **Independently verified:** a separate QA/observation confirms the expected behavior.

Do not infer one level from another. Record exact source SHA, PR and workflow run IDs, target environment, verifier, timestamps/time zone, test scope, failures and explicit unknowns. A skipped check is not a passing test.

Keep the original point-in-time report intact when new evidence arrives. Add a follow-up record or update this index rather than silently rewriting the historical conclusion.
