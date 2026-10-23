# v0.3.0-01 Final Implementation Review

**Review date:** 2026-10-22 (updated by v0.3.0-01C)
**Worktree:** `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0301-trusted-tool-evidence`
**Branch:** `codex/v0301-trusted-tool-evidence`
**HEAD / baseline (`origin/main`):** `d9b97de32632ef20b6ee39c8d72f73e44c35de26`
**Comparison method:** `origin/main` plus the uncommitted working tree. `HEAD` and
`origin/main` are identical, so the v0.3.0-01 implementation is intentionally
reviewed as a working-tree diff rather than as a commit range.

## A. Architecture summary

v0.3.0-01 moves Preview replacement provenance from the Legacy Preview
capability's private raw tool-result hook to the framework-neutral
`RuntimeResult` contract. The intended boundary is:

```text
authorized scoped Legacy tool dispatch
  -> validated detached ToolExecutionEvidence
  -> RuntimeResult
  -> revision-bracketed Preview exact-match gate
  -> existing scoped lookup and deterministic classification
```

Legacy maps only approved tool dispatches and validates replacement candidates.
Pydantic records only its existing successful `get_current_plan` completion; it
does not receive a replacement tool or any new production role. Preview no
longer reads raw Legacy tool dictionaries, preserves its membership/Plan/revision
checks, and admits replacement fields only through matching replacement evidence.

## B. Final changed-file inventory

Working-tree status at review start:

```text
 M backend/app/agents/legacy_runtime.py
 M backend/app/agents/pydantic_runtime.py
 M backend/app/agents/runtime_contract.py
 M backend/app/domain/preview_apply.py
 M backend/tests/test_b3_revision_bracketed_preview.py
 M backend/tests/test_pydantic_runtime.py
 M backend/tests/test_runtime_contract.py
?? AI_enhanced/PR-09_Pydantic-Production-Migration/
```

Implementation and test files reviewed in full:

| Path | Review result |
| --- | --- |
| `backend/app/agents/runtime_contract.py` | Adds evidence DTOs and the `RuntimeResult` field; v0.3.0-01C now detaches list inputs into validated tuples. |
| `backend/app/agents/legacy_runtime.py` | Maps approved Legacy dispatch facts to evidence and retains the pre-existing raw hook only for the older Chat service. |
| `backend/app/agents/pydantic_runtime.py` | Adds evidence for the existing successful plan read and fixed failure markers only. |
| `backend/app/domain/preview_apply.py` | Replaces raw Legacy provenance with evidence, while retaining scope, membership, revision, and deterministic classification checks. |
| `backend/tests/test_runtime_contract.py` | Covers approved/guard/cache/unsupported mapping, failure markers, DTO deep immutability, and `RuntimeResult` aliasing. |
| `backend/tests/test_pydantic_runtime.py` | Covers successful plan-read and timeout/cancellation evidence markers. |
| `backend/tests/test_b3_revision_bracketed_preview.py` | Covers exact field matching, raw-dictionary rejection, source-list injection resistance, and real Legacy execution into Preview. |
| `AI_enhanced/PR-09_Pydantic-Production-Migration/00_V0301_TRUSTED_TOOL_EVIDENCE_DESIGN.md` | New design record; reviewed as an untracked file. |
| `AI_enhanced/PR-09_Pydantic-Production-Migration/01_V0301_FINAL_IMPLEMENTATION_REVIEW.md` | This final review record. |

`git diff --check` and Python compilation passed. The corrected implementation
diff contains 697 additions and 53 deletions across the seven tracked
implementation/test files; the Markdown records and complete patch are
untracked package artifacts.

## C. Security trust-boundary analysis

### Confirmed controls

- The Legacy mapper allow-lists evidence tool names and turns unknown model tool
  names into a fixed `unsupported` marker (`legacy_runtime.py:325-362`).
- Guard-rejected calls have no candidates; mapper failures and deadline exits
  return fixed failure markers with no candidates (`legacy_runtime.py:344-360`,
  `474-508`; `pydantic_runtime.py:530-553`).
- Preview rejects raw dictionaries, requires `ToolExecutionEvidence`, accepts
  only `succeeded`/`cache_hit` replacement-search evidence, and exactly compares
  title, place, latitude, longitude, and optional price (`preview_apply.py:603-639`).
- Scoped Preview still captures active membership and a single Plan
  (`preview_apply.py:347-375`), rechecks captured revisions before classification
  and once more before return (`393-417`, `475-477`), and invokes the existing
  deterministic domain classifier (`405-416`, `453-461`).
- The Legacy cache is created within each `base.call_agent` invocation, so
  mapped `cache_hit` evidence is limited to that execution (`legacy_runtime.py:328-334`).

### Original P1 root cause — mutable evidence containers (resolved in v0.3.0-01C)

`@dataclass(frozen=True)` prevents attribute reassignment but does not freeze a
mutable value passed into an attribute. `ToolExecutionEvidence` accepts a list
for `replacement_candidates`: it validates only the elements and retains the
list (`runtime_contract.py:192-211`). `RuntimeResult` likewise accepts an
unvalidated mutable list for `tool_execution_evidence` (`runtime_contract.py:245-262`).
Preview subsequently accepts each entry based on `isinstance` and consumes its
candidate attributes (`preview_apply.py:615-638`).

A caller can therefore construct a valid frozen DTO with a list, mutate that
list after construction, and inject a validated-looking candidate before
Preview reads it. This breaks the stated immutable-evidence contract and means
the provenance boundary is not complete. It is a concrete in-process trust
boundary defect; it is not a claim that the current model wire format directly
does so.

This root cause was retained as review history and corrected by the narrowly
scoped v0.3.0-01C implementation below.

### v0.3.0-01C remediation and verified invariants

`ToolExecutionEvidence.__post_init__` now accepts only a `tuple` or `list`,
copies it immediately to a tuple, requires every member to be the exact
`ReplacementCandidateEvidence` DTO type, and stores that detached tuple using
`object.__setattr__` (`runtime_contract.py:194-215`). `RuntimeResult` applies
the same list/tuple-only, exact-`ToolExecutionEvidence` validation and detached
tuple storage before Preview can consume it (`runtime_contract.py:259-274`).
No arbitrary iterable is accepted.

`ReplacementCandidateEvidence` now accepts only built-in immutable text and
numeric primitives (while retaining bool, NaN, infinity, range, and negative
price rejection), so a subclass with mutable behavior cannot become a nested
evidence value (`runtime_contract.py:155-178`). Candidate DTOs remain frozen and
contain no mutable fields.

The resulting runtime invariant is:

- source candidate/evidence lists may change after construction without changing
  either DTO or `RuntimeResult`;
- stored collections are tuples and candidate fields cannot be assigned through
  the DTO;
- unvalidated objects, non-list/tuple containers, and non-exact DTO subclasses
  are rejected at construction; and
- Preview can only inspect the detached snapshot, so a fabricated candidate
  appended to a caller-owned source list cannot authorize a replacement.

Focused mutation regressions were added in
`test_runtime_contract.py` and
`test_b3_revision_bracketed_preview.py` for caller-list mutation, direct tuple
mutation, nested-candidate mutation, `RuntimeResult` aliasing, invalid entries,
and the actual evidence-to-Preview injection path. The existing real Legacy
positive and Pydantic `get_current_plan` tests additionally assert tuple-backed
evidence.

### Documentation accuracy note (P3, resolved)

`legacy_runtime.py:103-109` still says replacement Preview provenance is absent
from the framework-neutral contract. That statement is stale after this change:
raw Legacy results remain only for older Chat-service compatibility, while
Preview uses the new detached evidence. v0.3.0-01C corrects that comment at
`legacy_runtime.py:103-108`.

## D. Legacy compatibility analysis

Existing Legacy reply, suggested-change, candidate-option, guard, deadline, and
safe-degradation paths remain in place. The new mapper is additive for normal
successful results; stopped or empty results continue to return failure-shaped
`RuntimeResult`s without usable tool evidence (`legacy_runtime.py:285-321`).
The old raw-results recorder remains available only to capabilities that expose
the existing private compatibility hook; revision-bracketed Preview no longer
does. No Legacy provider selection, prompt, tool set, persistence, or HTTP
contract changed.

**Compatibility result:** PASS.

## E. Pydantic compatibility analysis

Pydantic adds no new tool. It appends evidence only after the existing scoped
`get_current_plan` handler has succeeded (`pydantic_runtime.py:317-334`), returns
a tuple in every successful mapping branch (`370-395`), and emits only fixed
non-candidate markers after failure (`530-553`). It has no replacement evidence,
provider activation, routing, schema, dependency, or production configuration
change.

**Compatibility result:** PASS.

## F. Real Tool Execution integration-test evidence

`test_preview_accepts_real_scoped_legacy_replacement_evidence_without_writing_plan`
in `backend/tests/test_b3_revision_bracketed_preview.py` exercises the actual
Legacy runtime loop with scoped `get_current_plan`,
`find_replacement_place`, and `classify_change` handlers; it then passes the
real mapped evidence through Preview. It asserts:

- `find_replacement_place` evidence is `succeeded` and contains the detached,
  expected candidate;
- Preview admits the matching replacement candidate; and
- the authoritative Plan revision and Plan item remain unchanged.

The focused module result after the test was added was `14 passed in 1.50s`.

## G. Regression results and exact commands

The original v0.3.0-01 results below are retained for history only. The
v0.3.0-01C results that follow were run after the corrective change from
`backend/` against a **new** task-owned local PostgreSQL cluster at
`localhost:55433/test_cadensy_v0301c`; the prior stopped cluster was not reused.
Test-mode flags, mocked AI, disabled scheduler, and a cleared provider/AWS
credential environment were used. No production database or provider was
contacted.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_contract.py tests/test_b3_revision_bracketed_preview.py tests/test_runtime_composition.py
# 49 passed

.\.venv\Scripts\python.exe -m pytest -q tests/test_pydantic_runtime.py tests/test_pr06b3_runtime.py tests/test_pr06b3_context.py
# 34 passed

.\.venv\Scripts\python.exe -m pytest -q tests/test_agents_tools.py tests/test_agents_call_agent.py tests/test_telemetry_privacy.py
# 50 passed

.\.venv\Scripts\python.exe -m pytest -q tests/test_pr06b2_conversation_api.py tests/test_pr06b2_conversation_concurrency_postgres.py tests/test_pr06b_infrastructure_recovery.py tests/test_auth.py tests/test_invites.py tests/test_chat.py
# 71 passed

.\.venv\Scripts\python.exe -m pytest -q tests/test_b3_revision_bracketed_preview.py
# 14 passed in 1.50s

.\.venv\Scripts\python.exe -m pytest -q
# 1311 passed, 10 warnings in 67.60s; exit code 0
```

Corrective verification used the existing project dependency baseline at
`C:\Users\zdxzh\Desktop\capstone\New\backend\.venv\Scripts\python.exe`
because this worktree has no local `.venv`. No dependency was installed or
upgraded.

```powershell
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_contract.py tests/test_b3_revision_bracketed_preview.py tests/test_pydantic_runtime.py
# 52 passed in 2.99s; exit code 0

C:\Users\zdxzh\Desktop\capstone\New\backend\.venv\Scripts\python.exe -m pytest -q tests/test_agents_tools.py tests/test_agents_call_agent.py tests/test_telemetry_privacy.py tests/test_pr06b2_conversation_api.py tests/test_pr06b2_conversation_concurrency_postgres.py tests/test_pr06b_infrastructure_recovery.py tests/test_auth.py tests/test_invites.py tests/test_chat.py
# 121 passed in 7.73s; exit code 0

C:\Users\zdxzh\Desktop\capstone\New\backend\.venv\Scripts\python.exe -m pytest -q
# 1314 passed, 10 warnings in 67.07s; exit code 0
```

The 10 warnings are the existing Alembic configuration deprecation concerning
`path_separator`; they are not introduced by this change. `git diff --check`
and Python compilation of the modified code/tests also passed.

## H. Remaining limitations deferred to v0.3.0-02

- Pydantic replacement tools, new tool surface, provider activation, schema
  changes, and production rollout remain explicitly out of scope.

There is no remaining v0.3.0-01C security blocker. No v0.3.0-02 work has been
started by this correction.

## I. Production configuration unchanged confirmation

No production configuration, feature flag, provider selection, dependency,
database schema, telemetry schema, API DTO, deployment resource, or external
service was changed. No real DeepSeek call, production operation, push, PR,
merge, or commit was performed.

## J. Final review verdict

The minimum corrective implementation now satisfies the required runtime
immutability invariant, preserves Legacy/Pydantic behavior, and is
regression-green. No scope beyond the evidence contract, its compatibility
comment, tests, review record, and regenerated patch was changed.

```text
LOCAL IMPLEMENTATION GATE: PASS
FULL REGRESSION GATE: PASS
SECURITY REVIEW: PASS
LEGACY COMPATIBILITY: PASS
READY FOR LOCAL COMMIT: YES
READY FOR v0.3.0-02: PENDING FINAL APPROVAL
PRODUCTION CHANGE AUTHORIZATION: NOT GRANTED
```

## Review method

The review inspected the complete working-tree diff and the untracked design
record, rather than only `git diff` output. The original P1 finding was
reproduced, corrected, and covered by focused mutation tests plus the full
backend suite. No other concrete behavioral or standards defect was identified.
