# PR-04E Controlled Pydantic Application Composition — Design

## 1. Verified base

| Item | Value |
| --- | --- |
| Canonical source | `origin/main` |
| Verified base SHA | `717d14d945ec9e89838c0b59719519238c02cb4b` |
| Design branch | `codex/pr04e-controlled-pydantic-composition` |
| Design worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04e-controlled-pydantic-composition` |
| Start status | clean; `HEAD == origin/main` |
| Pydantic inspection environment | existing isolated `backend/.venv-pydantic-poc`, Pydantic AI `2.54.0` |

This document is an implementation contract only. No production dependency was
installed, no real Provider request was sent, no AWS/RDS resource was touched,
and no Pydantic Runtime was made the default.

## 2. Current call path

The current real Chat path is:

```text
respond_to_trip_chat
  -> deterministic Application preflight
  -> RuntimeRequest + LegacyReadTripCapability
  -> build_chat_runtime(system_prompt=...)
  -> selected runtime.run(...)
  -> Application validation / deterministic Domain reclassification
```

`service.py` does not pass a production Pydantic factory. Its broad runtime
exception boundary returns `_degraded_reply(...)`; a Runtime failure observation
uses the same safe, deterministic reply. It does not make a second model call
or select Legacy as a retry.

`runtime_factory.py` currently has this reviewed selector contract:

| `CHAT_AGENT_RUNTIME` | Current result |
| --- | --- |
| missing, empty, `legacy` | `LegacyChatAgentRuntime` |
| `pydantic` with injected factory | injected result |
| `pydantic` without injected factory | `ChatRuntimeConfigurationError` |
| any other value | `ChatRuntimeConfigurationError` |

The injected factory seam must remain. It is the approved test seam for a
`FunctionModel`/fake runtime and must not cause any implicit fake model in the
application path.

## 3. Exact current blockers

1. `backend/app/agents/runtime_factory.py` intentionally has no reviewed
   production Pydantic composition. Explicit Pydantic selection without an
   injected factory fails closed.
2. `backend/requirements.txt` does not contain `pydantic-ai-slim`; therefore
   the production Docker image cannot import Pydantic AI.
3. `backend/Dockerfile` copies and installs only `requirements.txt`, so there
   is no alternate production dependency path.
4. The PR-03 helper `pydantic_poc.build_deepseek_chat_model()` hard-codes the
   model and is documented as PR-03-only compatibility code. It cannot be
   repurposed as the production composition boundary.
5. `DeepSeekProvider` in locked Pydantic AI `2.54.0` does not accept a
   configurable `base_url` parameter. A non-default `DEEPSEEK_BASE_URL`
   therefore cannot be silently honored through that direct provider path.

## 4. Production dependency findings

| Location | Finding |
| --- | --- |
| `requirements.txt` | no Pydantic AI dependency |
| `requirements-pydantic-ai-poc.txt` | `pydantic-ai-slim[openai]==2.54.0` |
| `requirements-pydantic-ai-poc.lock.txt` | locked Pydantic AI `2.54.0`, Pydantic `2.13.4`, OpenAI SDK `3.24.0` |
| `requirements-legacy-regression.lock.txt` | explicitly excludes Pydantic AI |
| `backend/Dockerfile` | installs only `requirements.txt` |
| Legacy CI | intentionally excludes Pydantic-only tests and has a zero-skip contract |
| Pydantic CI | installs only the separate Pydantic lock and runs the isolated compatibility suite |

The absence of Pydantic AI from `requirements.txt` is a hard composition
blocker, not a reason to fall back to Legacy after explicit Pydantic selection.

## 5. Provider-construction findings

The locked Pydantic AI API was inspected locally without a request:

```text
DeepSeekProvider(*, api_key=None, openai_client=None, http_client=None)
OpenAIProvider(base_url=None, api_key=None, openai_client=None, http_client=None)
OpenAIChatModel(model_name, *, provider=..., profile=None, settings=None)
```

`DeepSeekProvider.base_url` is the fixed official endpoint
`https://api.deepseek.com`; its direct constructor has no `base_url` argument.
It can accept a preconstructed `AsyncOpenAI` client, but using that route to
override an endpoint would add a new OpenAI-compatible transport/composition
policy not reviewed for the Chat application. PR-04E should instead enforce
the official DeepSeek endpoint for the direct `DeepSeekProvider` path.

The reusable concept from the PoC and offline smoke harness is narrow:

```python
OpenAIChatModel(
    model_name,
    provider=DeepSeekProvider(api_key=api_key),
    settings=OpenAIChatModelSettings(thinking=False),
)
```

The PoC module itself must remain isolated. It includes PR-03-only probes and
must not become the production composition module.

`DEEPSEEK_MODEL` should supply `model_name`; missing/blank retains the current
repository default `deepseek-v4-flash` through the existing provider catalog.
`DEEPSEEK_THINKING` must not enable thinking for this Pydantic Chat path:
composition pins `OpenAIChatModelSettings(thinking=False)`. This is required
for the reviewed typed-output/tool behavior. Existing Legacy handling of
`DEEPSEEK_THINKING` is unchanged.

Pydantic AI's provider constructor creates an `AsyncOpenAI` client, and
`OpenAIChatModel` only preloads that client's `chat.completions` resource.
Neither source path issues an HTTP request. Provider traffic begins only from
`Agent.run` / model request execution.

## 6. MOCK_AI decision

**Decision: fail closed (option A).**

For `CHAT_AGENT_RUNTIME=pydantic` plus `MOCK_AI=1`, production composition
must raise a configuration failure. It must not silently construct a
`TestModel`, `FunctionModel`, or another fake model.

The repository's test seams already inject a fake Runtime/model explicitly.
Keeping application composition real-provider-shaped makes test behavior
intentional, preserves the D3 comparison boundary, and prevents a deployment
setting from accidentally changing runtime semantics.

## 7. Proposed module boundary

Add `backend/app/agents/pydantic_composition.py` with only these
responsibilities:

- obtain the sanitized current DeepSeek configuration from the existing
  repository-owned configuration surface;
- validate the explicit Pydantic composition prerequisites;
- construct the official-endpoint Pydantic AI model with thinking disabled;
- construct and return `PydanticChatAgentRuntime(model=..., system_prompt=...)`;
- raise an internal composition error without credentials, headers, raw
  Provider bodies, prompts, or tool output.

It must not make a request, register tools, own a Session, do Domain work,
write data, select Legacy, or create a fake model.

`runtime_factory.py` remains the sole selector owner. On the explicit
`pydantic` path it lazily imports the composition function only when no
injected test factory was supplied. The import chain is:

```text
Chat Service import
  -> runtime_factory import
       -> Legacy/runtime contract only

explicit CHAT_AGENT_RUNTIME=pydantic, no injected factory
  -> lazy import pydantic_composition
       -> lazy Pydantic AI + pydantic_runtime imports inside composition path
```

Thus missing/empty/`legacy` never imports `pydantic_runtime`, `pydantic_ai`, or
a Pydantic provider module. The injected factory path likewise remains usable
by tests without importing the production composition module.

## 8. Fail-closed behavior

At the public factory seam, all production composition failures normalize to
`ChatRuntimeConfigurationError` with a stable, non-sensitive message. Preserve
the original exception only as an internal exception cause; do not interpolate
environment values or Provider exception text.

| Condition | Factory result | Chat Service result |
| --- | --- | --- |
| invalid selector | `ChatRuntimeConfigurationError` | deterministic degraded reply |
| Pydantic selected with `MOCK_AI=1` | configuration error | deterministic degraded reply |
| Pydantic dependency unavailable | configuration error | deterministic degraded reply |
| absent/blank API key | configuration error | deterministic degraded reply |
| non-official configured base URL | configuration error | deterministic degraded reply |
| model/runtime construction exception | configuration error | deterministic degraded reply |
| unknown remote model rejected only during execution | existing normalized Runtime failure | deterministic degraded reply |

The model name cannot be proven remotely valid without a Provider request.
PR-04E should validate non-empty local configuration and use the current
default, but must not claim offline model-availability validation. Any runtime
Provider rejection remains fail-closed and never triggers Legacy fallback.

For the endpoint check, missing/blank `DEEPSEEK_BASE_URL` resolves through the
existing default to `https://api.deepseek.com`; any other normalized URL is
unsupported for this direct-provider implementation. Do not introduce an
OpenAI-compatible wrapper merely to preserve custom endpoints in PR-04E.

## 9. Dependency and lockfile strategy

The smallest production change is to add this exact direct dependency to
`backend/requirements.txt`:

```text
pydantic-ai-slim[openai]==2.54.0
```

Do not change:

- `requirements-legacy-regression.lock.txt`; it must keep proving Legacy
  import/execution without Pydantic AI;
- `requirements-pydantic-ai-poc.txt` or
  `requirements-pydantic-ai-poc.lock.txt`; they remain the reviewed isolated
  compatibility environment;
- the frozen evaluation dependency contract or its artifact.

No Dockerfile edit is required: it already installs `requirements.txt`. Add a
CI Docker build/import proof after the requirement is added so the production
image, rather than an accidental developer environment, proves it contains the
composition dependency. That proof must import/build only; it must not execute
an Agent request or use a credential.

## 10. Test and CI matrix

| Case | Environment / test boundary | Expected result |
| --- | --- | --- |
| selector missing | Legacy regression | Legacy; no Pydantic import |
| selector `legacy` | Legacy regression | Legacy; no Pydantic import |
| selector `pydantic` + injected fake factory | existing composition seam | injected object used; production composition not imported |
| Pydantic + valid placeholder config + `MOCK_AI=0` | Pydantic-only composition test | real `PydanticChatAgentRuntime` object; HTTP denied/not attempted |
| Pydantic + `MOCK_AI=1` | Pydantic-only composition test | configuration failure, no fake substitute |
| absent/blank key, unsupported base URL | Pydantic-only composition test | configuration failure with no secret in message |
| dependency/import failure | factory test with import seam patched | configuration failure, no Legacy fallback |
| construction failure | factory test with composition seam patched | configuration failure, no Legacy fallback |
| invalid selector | Legacy regression | configuration failure |
| selected composition failure through Service | service test | deterministic degraded reply; selected runtime only |
| composed fake/model Runtime execution | Pydantic isolated test, explicit injected `FunctionModel` | common `RuntimeRequest`, capability, execution, and Application reclassification boundary preserved |
| production image import | Docker CI build/import check | exact production dependency is importable; no Provider request |
| Legacy-only import | Legacy CI subprocess/import test | Chat Service imports with no `pydantic_ai`; no collected skips |

Add a Pydantic-only test module, for example
`backend/tests/test_pydantic_composition.py`. Explicitly ignore it in the
Legacy job and explicitly execute it in the Pydantic job. Update
`test_ci_workflow_contract.py` to freeze both routing decisions. Do not convert
the Legacy partition into dependency skips.

The constructor-no-I/O test should patch/deny HTTP before invoking the
composition function, construct with a non-secret placeholder, and assert no
request dispatch occurred. It must not call `runtime.run`.

## 11. Expected changed files

| File | Intended change |
| --- | --- |
| `backend/app/agents/pydantic_composition.py` | new narrow production composition module |
| `backend/app/agents/runtime_factory.py` | lazy default composition for explicit Pydantic selection; retain injected seam |
| `backend/requirements.txt` | exact production Pydantic AI dependency |
| `backend/.env.example` | document opt-in selector and Pydantic's official-endpoint / thinking policy without a key |
| `backend/README.md` and/or `backend/LOCAL_DEV.md` | document opt-in composition and fail-closed local behavior |
| `backend/tests/test_runtime_composition.py` | retain/extend Legacy import and no-fallback contracts |
| `backend/tests/test_pydantic_composition.py` | new Pydantic-only construction/configuration/no-I/O coverage |
| `backend/tests/test_ci_workflow_contract.py` | freeze new CI partition routing and production dependency proof |
| `.github/workflows/build-validation.yml` | route Pydantic-only tests; add production Docker build/import proof |
| `AI_enhanced/.../39_PR04E_CONTROLLED_PYDANTIC_COMPOSITION_DESIGN.md` | this reviewed design record |

## 12. Explicitly unchanged files and behavior

PR-04E must not modify:

- Dataset V1, symmetric harness semantics, A/B grader semantics, or D3 artifact;
- Domain classification policy, mutation endpoint, or replacement provenance;
- candidate-option behavior, replacement/candidate parity, patch-field set, or
  concurrency/revision semantics;
- telemetry, conversation persistence, AWS/ECS/RDS configuration, deployment,
  or real-Provider execution;
- Legacy default selection or Legacy CI's missing-Pydantic proof.

`backend/app/domain/chat/service.py` need not change for the composition
boundary: it already creates the Runtime via the factory and maps factory/run
failure to a safe degraded reply. Any Service test added is evidence only, not
a policy rewrite.

## 13. Risks

1. Adding the package to production requirements makes Pydantic importable in
   the image, so the lazy selector boundary—not package absence—becomes the
   protection for default Legacy execution.
2. Direct `DeepSeekProvider` cannot honor arbitrary existing base URLs. The
   explicit official-endpoint restriction is intentionally fail-closed and
   needs operational review before enabling the selector where a custom endpoint
   is configured.
3. Model construction without I/O does not prove remote model availability,
   output quality, cost, latency, credential validity, or reliability.
4. The Pydantic Runtime remains read-only and schedule-field-bounded. It does
   not establish candidate or replacement parity, full Legacy parity, fresh
   state, or production concurrency protection.
5. A configuration error intentionally degrades the Chat response rather than
   selecting Legacy. Operators need explicit configuration awareness before
   setting `CHAT_AGENT_RUNTIME=pydantic`.

## 14. Implementation sequence

1. Add the exact production dependency and the narrow `pydantic_composition`
   module, with no request operation.
2. Update `runtime_factory` only: preserve direct Legacy default and injected
   test factory; add lazy production composition for explicit Pydantic only.
3. Add Pydantic-only construction/no-I/O/configuration tests and retain Legacy
   no-import/fail-closed tests.
4. Update CI routing and add the Docker production dependency/import proof.
5. Run Legacy and Pydantic isolated partitions against disposable local
   PostgreSQL with Provider credentials blank; run no real Provider test.
6. Inspect the exact diff against this contract, confirm frozen D3 components
   are unchanged, then seek independent PR review.

## 15. Final recommendation

**GO — narrow PR-04E implementation is justified, conditional on this
fail-closed composition contract.**

The D3 result supplies the required bounded Runtime evidence. The current
blockers are understood and local: production dependency absence and missing
composition. The implementation must preserve Legacy as the default, retain
the injected fake seam exclusively for tests, enforce the official direct
DeepSeek endpoint, pin thinking off, and convert every explicit Pydantic
composition problem into a safe degraded response without fallback.

This GO authorizes only a separately reviewed implementation of the files and
tests above. It does not authorize rollout, a default switch, full Legacy or
replacement parity, real Provider validation, AWS/RDS work, deployment, or a
next integration stage.
