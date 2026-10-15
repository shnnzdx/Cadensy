# PR-06C.1B — Offline Proof and Test Record

**Recorded:** 2026-10-15T00:20:00Z
**Result:** offline controls pass; live recovery semantics remain unproven.

## Test seam

`scripts/cd01/capacity_recovery_contract.py` is a pure contract over explicit
GitHub-dispatch values plus saved `describe-services` and
`describe-task-definition` JSON. It imports no AWS SDK, subprocess, socket, or
HTTP client. It cannot perform an AWS write. The workflow is tested as YAML and
shell-text structure; no AWS CLI command was executed by this test suite.

## Red-to-green evidence

Before the workflow patch, the new static test failed because CD-01 exposed
only source/runtime/migration inputs and had no zero-capacity mode, baseline
proof, `--desired-count`, or pre-mutation recheck. That is the intended red
state. It now passes after the minimal workflow and contract implementation.

Executed locally with test-only scheduler/mock settings:

```text
backend\\.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_cd01_delivery_safety.py -q
12 passed in 0.12s
```

`python -m py_compile` also completed for both CD-01 Python helpers.

## Covered offline cases

| Case | Evidence |
| --- | --- |
| Unauthorized zero-capacity restoration | standard mode with baseline zero, omitted recovery evidence, zero, or count two is rejected. |
| Approved narrow recovery request | only explicit mode, expected count zero, recovery count one, valid ARN/digest/evidence SHA returns release count one. |
| Live service drift | non-zero desired/running, changed task definition, pending work, multiple/non-completed deployment, or changed safety configuration is rejected. |
| Image and gate drift | non-approved/changed digest and malformed or enabled protected gate are rejected. |
| Offline-only seam | AST check rejects AWS/network/process client imports. |
| Workflow approval and ordering | static test requires protected Main environment, explicit recovery inputs, schema preflight before registration, final baseline recheck before `UpdateService`, and explicit `--desired-count`. |
| Existing CD-01 safeguards | digest pinning, circuit breaker, ALB health, API health, and no implicit migration remain asserted. |

## What this does not prove

Fixture JSON is not a mocked AWS semantic claim. It cannot prove ECS rollout,
circuit-breaker behavior from a zero baseline, old-image execution, ALB
registration, schema compatibility, CloudWatch delivery, costs, or production
health. Those remain production/isolated-environment validation items.
