# PR-03R.2 — Controlled Real DeepSeek Smoke Verification

## Final verdict: Partial

The bounded real-provider run sent exactly the authorized four HTTP requests
and did not retry any Case. R1 and R2 completed successfully. R3's first
request was sent as the fourth request, but its Provider-reported usage moved
the aggregate above the 2,000-token operational limit. The Usage Ledger
stopped before the R3 response could enter the Agent graph or invoke its
compatibility tool. No fifth request was sent.

This is a narrow protocol result only. It is not a production integration,
model-quality evaluation, A/B conclusion, or claim that Pydantic AI is better
than the Legacy Runtime.

## Exact source and preflight

| Check | Result |
| --- | --- |
| Branch | `feature/pr03r-deepseek-compatibility` |
| Base commit | `8c9d12b681828bbe511b5d0bbc3b1f82a5b8e194` |
| Source state | The reviewed PR-03R.1/PR-03R.1a worktree plus this uncommitted test-only Live Adapter and Runner |
| Python | 3.13.5 |
| Pydantic AI | 2.54.0 |
| OpenAI SDK | 3.24.0 |
| Pydantic/Smoke/Evaluation/CI-contract preflight | 61 passed in 4.01 seconds |
| Dependency consistency | `pip check` passed |
| PostgreSQL | local disposable `test_pr03r2_smoke`, confirmed accepting connections |
| Dataset | Frozen V1 unchanged |

The Key was loaded only into the one child process that performed the run. It
was never printed, written to a report, added to source control, or copied
into an artifact. The report does not include an Authorization header, raw
request, raw response, database identifier, or private fixture wording.

## Actual Provider configuration

| Control | Actual setting |
| --- | --- |
| Host allowlist | `https://api.deepseek.com` only |
| Model identifier sent | `deepseek-v4-flash` |
| Official model status | Documented legacy alias; DeepSeek routes it to Flash |
| OpenAI client | `AsyncOpenAI(max_retries=0)` |
| HTTP client | `trust_env=False`, `follow_redirects=False` |
| Global request budget | 4 |
| Reported-token operational limit | 2,000 |
| Per-request output cap | 128 |
| Provider deadline | 8 seconds |
| Agent/tool/output retries | 0 |
| Streaming/background execution | disabled / none introduced |
| Domain tools | test-only read capability; no write-capable tool |

The separately implemented `LiveProviderTransport` is not a credential swap
for the Mock transport. It performs HTTPS-host admission, payload Privacy Gate,
request accounting, response Usage accounting, and redacted wire-contract
capture before/around the actual network transport. Its injected-memory
contract tests cover host rejection, privacy rejection before dispatch,
counter/Usage accounting, redirect refusal, and deadline terminal behavior.

## Actual execution

| Case | Authorized maximum | Actual requests | Result |
| --- | ---: | ---: | --- |
| R1 — typed clarification | 1 | 1 | Pass |
| R2 — scoped read-only tool + typed preview | 2 | 2 | Pass |
| R3 — non-thinking required-tool first turn | 1 | 1 | Inconclusive: Usage Ledger stopped before Agent tool/output handling |
| Total | 4 | **4** | No retry; no fifth request |

### Typed output and tool evidence

- R1 returned the typed `clarification` output kind.
- R2 returned typed `change_preview` with symbolic
  `item_ref: "scoped_item"` and `new_start_hour: 15.5`. It did not expose
  a raw PlanItem ID.
- R2 consumed exactly two Provider turns. Its output validator only accepts a
  preview after the scoped read tool has completed, which is direct runtime
  evidence that the test-only independent, Trip-scoped reader was invoked.
- R3's first Provider request was admitted, but the response was rejected at
  the Usage Ledger boundary before `compatibility_probe` could run. Therefore
  there is no acceptable R3 tool-call or final-output success claim.

No write-capable Domain tool was registered or called.

## Provider-reported usage and cost

The Provider reported the following non-content counters:

| Request | Prompt tokens | Completion tokens | Total |
| ---: | ---: | ---: | ---: |
| 1 | 311 | 76 | 387 |
| 2 | 619 | 39 | 658 |
| 3 | 705 | 81 | 786 |
| 4 | 283 | 18 | 301 |
| Aggregate | **1,918** | **214** | **2,132** |

The fourth response took the aggregate from 1,831 to 2,132. The 2,000-token
limit is a response-accounted operational stop, not a pre-paid hard cap; the
boundary retained the observed 301-token response and prevented all later
requests.

DeepSeek's official Models & Pricing page states that
`deepseek-v4-flash` is a legacy accepted name routed to Flash, and lists
Flash cache-miss input/output pricing of $0.15/$0.60 per million tokens
off-peak and $0.30/$1.20 peak. Applying the reported prompt/completion
counters produces:

| Calculation | USD |
| --- | ---: |
| Off-peak cache-miss estimate | $0.00041610 |
| Peak cache-miss upper estimate | $0.00083220 |
| Provider invoice / billing confirmation | Unknown |

The estimates do not claim cache behavior, taxes, account balance deduction,
or invoice truth; only a Provider billing surface can establish those. Source:
[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/).

## Deadline, privacy, and cleanup evidence

- No provider/request/tool timeout occurred in this run.
- No Privacy Gate violation occurred.
- No redirect was followed and no retry was attempted.
- The live execution result contains no raw wire data. A post-run reporting
  path issue meant the already-redacted per-request wire assertions were not
  persisted when the terminal Usage exception occurred; this was repaired and
  covered by a fresh offline regression, but the four-request budget forbids a
  second live run to reconstruct them.
- Synthetic fixture cleanup preserved the durable-row counts.
- R2 was supplied an independent worker `Session` factory and a
  Trip-scoped capability; no request-scoped Session was passed to the tool.
- No PlanItem, Proposal, Vote, or other durable Domain mutation was made.

## Failure classification

| Field | Value |
| --- | --- |
| Technical failure | `usage_budget_exceeded` |
| Normalized evaluation failure | `usage_limit` |
| Ledger terminal reason | `reported_token_budget_exceeded` |
| Actual requests after terminal stop | 0 |

## Remaining compatibility risks

- R3's `reasoning_effort=none`, `tool_choice=required`, and
  `compatibility_probe` first-turn behavior are not Real Provider verified in
  this run.
- The reported-token budget was exceeded by the final permitted response; a
  future R3 verification needs a separately authorized budget design, not a
  hidden retry.
- This run does not prove real-provider cancellation semantics, billing,
  model-routing identity beyond the documented alias behavior, or performance
  on the Golden Dataset.
- No production Route, Runtime Selector, Pydantic migration, deployment,
  AWS/RDS access, or Git Push/Merge was performed.

## Recommendation

Accept R1 and R2 as controlled real-provider compatibility evidence. Keep R3
as **inconclusive**. Do not claim a full PR-03R Real Provider Pass or begin
PR-04. Any R3 follow-up requires a new authorization with an explicit
incremental request/token/cost budget and must preserve the same fail-closed
Live Adapter boundary.
