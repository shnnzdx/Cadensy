# PR-03R.3 — R3-only Real Provider Completion

## Final verdict: PASS (bounded protocol compatibility)

The separately authorized R3 follow-up sent exactly one real DeepSeek request. It returned a compatible `compatibility_probe` tool call under non-thinking and `tool_choice=required`. The one-request boundary rejected the possible second Agent turn before an additional HTTP dispatch. This completes only the R3 first-turn interoperability gap; it is not production integration, quality evaluation, or a comparison with the Legacy Runtime.

## Exact source state and preflight

| Check | Result |
| --- | --- |
| Branch | `feature/pr03r-deepseek-compatibility` |
| Base commit | `8c9d12b681828bbe511b5d0bbc3b1f82a5b8e194` |
| Source state | Reviewed uncommitted PR-03R test-only worktree plus R3-only Runner and parameterized R3 budget seam |
| Python / Pydantic AI / OpenAI SDK | 3.13.5 / 2.54.0 / 3.24.0 |
| Dependency consistency | `pip check` passed |
| Offline compatibility suite | **62 passed** in 4.16 seconds |
| Fake Evaluation Runner | **8/8 passed** in an independent process |
| Database | New local disposable `test_pr03r3_smoke`; runtime DB not used |
| Dataset | Frozen V1 unchanged |

| File | Execution-time SHA-256 |
| --- | --- |
| `backend/app/agents/provider_smoke_harness.py` | `33FF788B0A77447379DE1E01D666E005FA50A64CD77B091A9B4A229D518B09BD` |
| `backend/evals/run_r3_live_provider_smoke.py` | `3364C63FB680530CD7E0E701DC9907E0B480FC949BD45A383574B8211E343E42` |
| `backend/tests/test_live_provider_smoke_adapter.py` | `5026A029CBE5B9282E9B35661130C1CF066583C36439E346265B2FD7CF000A07` |

The new offline in-memory-transport regression was first red, then green. It proves that a fresh `GlobalUsageLedger(max_requests=1, max_total_tokens=500)` accepts the first R3 turn, records its usage, executes `compatibility_probe`, and blocks its second turn before network dispatch.

## Actual Provider configuration and result

| Control / success criterion | Evidence | Result |
| --- | --- | --- |
| Endpoint / model | `https://api.deepseek.com`; `deepseek-v4-flash` | Pass |
| Incremental request budget | 1 authorized; ledger recorded **1** | Pass |
| Token budget | 500 allowed; Provider reported **301** | Pass |
| Per-request cap / deadline | `max_tokens=128`; 8 seconds | Pass |
| Retries / streaming / background | zero / disabled / none introduced | Pass |
| Non-thinking | redacted wire contract: `reasoning_effort: "none"` | Pass |
| Required tool choice | redacted wire contract: `tool_choice: "required"` | Pass |
| First-turn tool call | `compatibility_probe` actually invoked | Pass |
| Domain read/write tools | no Domain tool registered or invoked | Pass |
| Second Provider request | none; request-limiter prevented it | Pass |
| Privacy / raw persistence | no violation; only redacted contract saved | Pass |
| Durable side effects | unchanged; counts `0/0/0/0` for PlanItem/Proposal/DecisionRound/Vote | Pass |
| Late work | `unfinished_late_tasks_at_close: 0` | Pass |

Provider usage was `283` prompt + `18` completion = `301` tokens. The transparent cache-miss cost estimate is $0.00005325 off-peak and $0.00010650 peak; provider billing, cache state, and invoice result remain unknown.

No timeout, cancellation, non-2xx response, retry, redirect, malformed tool call, Privacy Gate violation, or ledger terminal failure occurred. The secret was injected solely into the child process; it was not printed, persisted, committed, or recorded in the report. The result has no raw request/response, Authorization header, credential, membership ID, private fixture wording, or real user data.

## Overall PR-03R verdict and limits

**Overall PR-03R verdict: PASS for the bounded real-provider protocol scope.**

Accepted live evidence now covers R1 typed clarification, R2 Trip-scoped read-only tool plus typed preview, and R3 first-turn required-tool choice with non-thinking configuration. Host allowlisting, no retry, usage accounting, deadline boundary, privacy, no Domain writes, and cleanup remained in force.

This does not prove production readiness, model quality, A/B superiority, unbounded multi-turn behavior, billing correctness, cancellation of a remote request already accepted by the Provider, or Chat-route integration. No production runtime, Golden Dataset, AWS/RDS resource, deployment, Git push, or merge was changed. PR-04 remains outside this authorization.
