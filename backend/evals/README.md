# Cadensy Evaluation Foundation

`evals/` is the versioned business-contract layer for Agent evaluation. It is
not a model benchmark and does not install or import Pydantic AI.

## Contents

* `datasets/chat_change_preview_v1.json` — synthetic Golden cases with fixture
  preconditions, known/unknown facts, allowed output kinds, deterministic
  business oracle, expected tool contract, and safety invariants.
* `runner.py` — normalizes Legacy Runtime observations and emits reports. A
  future adapter must emit the same observation shape; it must not alter the
  Golden Dataset to improve its score.
* `graders.py` — deterministic outcome, tool, decision, safety, fallback,
  latency, usage, and failure-taxonomy graders.
* `reports/legacy_baseline_v1/` — the recorded Fake-Provider Legacy baseline.

## Deterministic CI command

```powershell
cd backend
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_eval_local_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
.\.venv\Scripts\python.exe -m pytest -q tests/test_evaluation_foundation.py
```

The pytest fixture validates the target as a disposable local PostgreSQL
database and recreates it. It uses only synthetic rows and Fake Provider
responses.

## Generate a local report

```powershell
cd backend
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr02_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
$env:CADENSY_EVAL_DEPENDENCY_LOCK='requirements-legacy-regression.lock.txt'
.\.venv\Scripts\python.exe -m evals.runner --output evals\reports\local\legacy-check
```

The command refuses a non-local or non-test-named `TEST_DATABASE_URL` before
it opens a connection. It records `manifest.json`, `summary.json`, and
`cases.jsonl`. Fake results have `token_usage: null`, `cost: null`, and
`provider_invocation_budget_seconds: null`; the report never invents metering
data. The manifest records the selected repository-owned dependency lock and
its SHA-256, the Python version, dataset hash, runtime, grader version, and
source commit. Never target an archived baseline directory such as
`reports/legacy_baseline_v1`; CI uses a unique run-and-attempt output path and
uploads the resulting three files as an artifact only after its privacy checks
pass.

`run_planner_eval.py` and `run_real_trip_tools_trace.py` remain the existing
Planner and real-provider/manual harnesses. PR-02 does not duplicate the
Planner generator and does not execute the real-provider script.
