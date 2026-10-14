# PR-04E Controlled Pydantic Application Composition — Implementation

## 1. Verified base

Implementation started on `codex/pr04e-controlled-pydantic-composition` at
`717d14d945ec9e89838c0b59719519238c02cb4b`, equal to `origin/main`. The only
pre-existing worktree change was the approved PR-04E design record
`39_PR04E_CONTROLLED_PYDANTIC_COMPOSITION_DESIGN.md`.

## 2. Exact changed files

- `.github/workflows/build-validation.yml`
- `backend/.env.example`
- `backend/LOCAL_DEV.md`
- `backend/README.md`
- `backend/app/agents/pydantic_composition.py`
- `backend/app/agents/runtime_factory.py`
- `backend/requirements.txt`
- `backend/tests/test_ci_workflow_contract.py`
- `backend/tests/test_pydantic_composition.py`
- `backend/tests/test_runtime_composition.py`
- `AI_enhanced/phase-0-audit-2026-10-03/39_PR04E_CONTROLLED_PYDANTIC_COMPOSITION_DESIGN.md`
- this implementation record

## 3. Composition path before and after

Before, a missing/empty/`legacy` selector built `LegacyChatAgentRuntime`; an
explicit Pydantic selector required an injected factory and otherwise failed.

After, the Legacy path is unchanged. On explicit `CHAT_AGENT_RUNTIME=pydantic`
without an injected test factory, `runtime_factory` lazily imports
`pydantic_composition`, which validates configuration and builds
`PydanticChatAgentRuntime`. No selected Pydantic failure can select Legacy.

## 4. Production dependency change

`backend/requirements.txt` now adds exactly:

```text
pydantic-ai-slim[openai]==2.54.0
```

Neither the Legacy regression lock nor either isolated Pydantic lock changed.

## 5. Configuration source

Composition calls the existing `base.provider_catalog()[base.DEEPSEEK_PROVIDER]`
surface. It introduces no second environment-variable family. It requires a
nonblank key, a model (including the existing default), and the official direct
provider endpoint `https://api.deepseek.com` (a trailing slash is accepted).
Custom endpoint configuration fails closed.

## 6. Lazy import proof

`runtime_factory` imports only Legacy/runtime-contract code at module load.
The production Pydantic composition import is inside the explicit Pydantic,
non-injected branch. In the Legacy-only environment, both unset and explicit
`legacy` selection built `LegacyChatAgentRuntime` while
`app.agents.pydantic_composition` remained absent from `sys.modules`.

## 7. Constructor no-I/O proof

The Pydantic composition test patches both synchronous and asynchronous HTTP
dispatch methods to fail if called. Explicit Pydantic factory construction with
a placeholder key created a real `PydanticChatAgentRuntime` and made zero HTTP
dispatches. The test does not call `runtime.run` or `Agent.run`.

## 8. Fail-closed behavior

The public factory translates any production composition/import/constructor
exception to the stable, non-sensitive
`ChatRuntimeConfigurationError("Pydantic Chat Runtime configuration is unavailable")`.
Tests prove no Legacy constructor is used after a simulated unavailable
dependency or composition exception.

## 9. `MOCK_AI` behavior

`CHAT_AGENT_RUNTIME=pydantic` with `MOCK_AI=1` raises a composition error. It
does not create a `TestModel`, `FunctionModel`, fake Runtime, or Legacy Runtime.

## 10. Endpoint and thinking policy

The direct `DeepSeekProvider` path accepts only the official endpoint because
it has no reviewed arbitrary-base-URL constructor route. The composed model is
constructed with `OpenAIChatModelSettings(thinking=False)` and does not inherit
Legacy's `DEEPSEEK_THINKING` setting.

## 11. Test evidence

Completed offline checks:

- Pydantic composition + CI workflow contracts: **17 passed**.
- Pydantic adapter's database-free first ten Runtime-contract cases:
  **10 passed**.
- Selector-focused `test_runtime_composition.py` in the Pydantic environment:
  **11 passed**.
- The same selector-focused suite in the Legacy-only environment: **11 passed**;
  `pydantic_ai` was confirmed absent.
- `pip check` passed in both isolated environments.
- `py_compile` passed for the new composition, factory, and new test in the
  Pydantic environment, and for the composition/factory in the Legacy
  environment.
- `git diff --check` passed.

## 12. Legacy no-Pydantic evidence

The Legacy regression environment reported `pydantic_ai` absent. It imported
the Chat Service/factory, built Legacy for unset and explicit `legacy`, and
explicit Pydantic selection with valid placeholder configuration failed with the
stable public configuration error rather than importing a usable dependency or
falling back.

## 13. Production Docker proof

CI now has a separate **Backend — Production Pydantic Image Proof** job. It
builds `backend/Dockerfile`, then runs with `--network none`:

```text
python -m pip check
python -c "import pydantic_ai"
python -c "import app.agents.pydantic_composition"
python -c "import app.domain.chat.service"
```

No credential is supplied and no Runtime is constructed or executed. The
local Docker executable was unavailable, so this image proof was not run
locally; the static CI contract test passed.

## 14. Frozen-file proof

No frozen D3 dataset, harness, grader, or archived formal A/B artifact was
modified. `git diff --check` passed and the changed-file review contains no
evaluation, Domain, Service, or Pydantic adapter change.

## 15. Limitations

This is offline composition evidence only. It does not prove credential
validity, remote model availability, response quality, cost, latency, or
provider reliability. Full database-backed partitions could not run locally
because localhost PostgreSQL was unavailable; the test configuration used only
the explicit disposable localhost `cadensy_pr04e_test` target and made no
remote database attempt.

## 16. Anti-goals confirmed

No real DeepSeek request, AWS/RDS operation, deployment, default switch,
Legacy replacement, Domain change, concurrency change, telemetry change,
replacement/candidate expansion, patch-field change, or fake production model
was introduced. `service.py` and `pydantic_runtime.py` are unchanged.

## 17. Review verdict

**Implementation complete, conditional on CI running the remaining
database-backed partitions and the new Docker image proof.** The composition
boundary is narrow, opt-in, lazy, official-endpoint-only, thinking-off, and
fail-closed with no Legacy fallback.
