# v0.3.0-02 Final Local Review

## Scope, branch, and baseline

- Branch: `codex/v0302-pydantic-functional-parity`
- HEAD / merge-base with `origin/main`: `5ce3782df8222cc87fd468bf1cf649a6d4aefe69`
- Baseline working tree: clean before this work.
- Production selector, schema, API contracts, providers, dependencies, and
  deployment configuration were not changed. No real DeepSeek request ran.

## Architecture and implementation summary

`PydanticChatAgentRuntime` now exposes four existing capability-bound,
read-only handlers: `get_current_plan`, `get_trip_facts`,
`find_replacement_place`, and `propose_options`. It still never receives a
Session, ORM instance, generic query interface, credentials, or writer.

The runtime keeps per-execution policy state only: the Current Plan item ids
observed in that execution, a canonical-argument cache of validated handler
projections, candidate options, and detached `ToolExecutionEvidence`. A
downstream lookup/options call requires a prior plan read; replacement lookup
also requires its exact observed item id. Guard rejection, handler failure,
deadline exhaustion, and malformed projections cannot create successful or
cache-hit evidence. Cache values are retained only after public projection
validation and are discarded at the end of the run.

Venue patches admit only complete `title`/`place`/`lat`/`lng` groups and must
exact-match immutable replacement candidate evidence from a successful or
same-run cache-hit lookup. `propose_options` supplies advisory options through
the existing tool; it cannot introduce venue fields without that same evidence.
`RevisionBracketedPreview` remains the sole domain/classification/revision
authority. `classify_change` remains intentionally unexposed to Pydantic,
because Preview already runs deterministic classification with current scope
and revisions.

`selected_item_ref` is included as a non-authoritative instruction hint only.
It never grounds a suggestion; the output validator still requires an item id
returned by the scoped Current Plan read.

## Changed-file inventory

- `backend/app/agents/pydantic_runtime.py`
  - constrained tool registration, per-run policy/cache, typed validation,
    evidence normalization, and safe option mapping.
- `backend/tests/test_pydantic_runtime.py`
  - FunctionModel policy/typed-output coverage, real scoped-handler Preview
    integration, and real Durable Coordinator persistence integration.
- `02_V0302_FUNCTIONAL_PARITY_DESIGN.md`
  - design and classification/exposure decision record.
- `03_V0302_FROZEN_AB_EVALUATION.json`
  - new execution artifact from the unmodified frozen PR-04D3 dataset.
- This review document.

No `.env`, credentials, database data, cache directory, generated log, or
production artifact is included.

## Security and compatibility review

- Trusted evidence uses the existing v0.3.0-01 frozen DTOs. Pydantic creates
  replacement evidence only from a successful scoped lookup response; the
  candidate tuple is immutable.
- Model text, a fabricated option, a partial venue group, an unknown item, a
  rejected call, a failed handler, timeout, cancellation, or late result cannot
  become accepted replacement provenance.
- Exact patch/evidence matching is enforced in Pydantic and independently again
  by Preview's existing `_replacement_provenance_allows` gate.
- Numeric inputs reject bools, NaN, infinity, invalid coordinate ranges,
  negative prices, invalid ISO dates, nested values, blank values, and unknown
  fields.
- Preview independently retains plan scope, active membership, revision
  bracketing, deterministic classification, and no-write behavior.
- Legacy code was not altered. The default runtime selector remains Legacy.

## Validation evidence

All commands were run with `APP_ENV=test`, `MOCK_AI=1`, scheduler disabled,
provider credential variables removed, and only the disposable local test
database on port `55434`.

| Command scope | Result |
| --- | --- |
| `pytest -q backend/tests/test_pydantic_runtime.py` | `37 passed` |
| Pydantic + Runtime Contract + Composition + B.3 Preview focused suite | `80 passed` |
| Durable Context/Runtime, conversation races, Legacy tool/agent, telemetry, CI-contract focused suite | `126 passed` |
| `python -m evals.formal_ab_runner --output .../03_V0302_FROZEN_AB_EVALUATION.json` | exit `0`; 16 observations, no network |
| `pytest -q backend/tests` | `1331 passed, 10 warnings` in `68.27s`, exit `0` |
| `python -m compileall -q backend/app/agents/pydantic_runtime.py` | exit `0` |
| `git diff --check` | exit `0` |

The PostgreSQL integration is in
`test_pydantic_real_scoped_replacement_evidence_reaches_revision_bracketed_preview`:
FunctionModel → actual revision-bracketed scoped handler → immutable evidence
→ typed patch → actual Preview candidate. The Durable Coordinator test proves
Context → Pydantic → Preview → fenced assistant/option persistence without a
Plan mutation.

## Frozen functional evaluation

The frozen PR-04D3 inputs and historical reports were unchanged. The new
artifact records eight cases for each runtime. Both Legacy and Pydantic had the
same two business-grader failures: `ambiguous-time` (safe-degraded,
`unexpected_exception`) and `privacy-injection` (clarification). All sixteen
observations passed the evaluation safety checks and made zero network calls.

This is not evidence that v0.3.0-02 caused a Legacy regression; the outcomes
are symmetric. It does mean the strict v0.3.0-02 acceptance condition that the
functional evaluation fully meet all business criteria is not established.
No attempt was made to change frozen inputs, labels, or grading to improve that
result.

## Deferred work

- Investigate the two symmetric frozen-evaluation business gaps under a
  separately scoped follow-up; do not alter the frozen dataset/report history.
- Real-provider behavior, provider token/cost accounting, browser acceptance,
  production selector activation, and deployment remain unexecuted.

## Verdict

The implementation and full backend regression are locally sound, and the
Pydantic tool/evidence/Preview/Durable paths are covered with deterministic
fake models and disposable PostgreSQL. However, the frozen functional-eval
business criteria are not all green, so this stage is not marked fully complete.

`v0.3.0-02 IMPLEMENTATION: INCOMPLETE`

`FUNCTIONAL PARITY: PARTIAL`

`TOOL POLICY SECURITY: PASS`

`TRUSTED EVIDENCE INTEGRATION: PASS`

`PYDANTIC PREVIEW INTEGRATION: PASS`

`DURABLE CONVERSATION INTEGRATION: PASS`

`LEGACY REGRESSION: PASS`

`FULL BACKEND REGRESSION: PASS`

`READY FOR CODE REVIEW: NO`

`PRODUCTION CHANGE AUTHORIZATION: NOT GRANTED`

## v0.3.0-02B closure update (2026-10-22)

The historical verdict above is retained as the accurate status at the end of
v0.3.0-02 initial implementation. v0.3.0-02B then closed the demonstrated
candidate-order and targeted Options exposure gaps without changing Legacy,
Preview authority, schema, API, production configuration, or the frozen
historical artifacts.

- Candidate matching now searches all exact evidence candidates before rejecting
  an omitted-price or priced patch; order is no longer authoritative.
- Pydantic now exposes bounded typed `conflict_item_ids` and suggestions to the
  existing Domain options validator. Foreign conflict ids are guard-rejected;
  invalid model suggestions are rejected by the existing handler.
- `06_V0302B_FROZEN_EVALUATION_DIAGNOSTIC.md` traces both historical 6/8
  business mismatches to unchanged shared Application preflight behavior.
- `07_V0302B_FUNCTIONAL_EVALUATION.json` records 11 new workflow scenarios,
  all passing.
- v0.3.0-02B validation: Pydantic Runtime `43 passed`; relevant cross-cutting
  suite `242 passed`; complete backend suite `1337 passed, 10 warnings`.

The historical Golden business gap remains a separate product-contract item;
it is not a Pydantic-specific regression. The latest detailed verdict is in
`05_V0302B_FUNCTIONAL_GAP_CLOSURE.md`.
