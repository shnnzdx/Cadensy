# Cadensy Phase 0.5 — Audit Review and Corrections

审查范围：对 Phase 0 文档的一致性、可实施性和可验证性进行修订。本轮没有修改业务源码、依赖、数据库、云资源或真实 provider 配置。

## Cross-document corrections

| Original finding / design | Identified issue | Corrected design | Affected document | Implementation consequence | Final recommendation |
|---|---|---|---|---|---|
| 原 `PR-01` 同时处理 auth security、guest compatibility、agent timeout。 | 改动面过大；关闭 header 前未证明 guest 依赖；不同失败模式难以单独评审/回滚。 | 拆成 `PR-01A` auth/guest characterization、`PR-01B` bounded guest identity + secure default、`PR-01C` agent deadline/session isolation。 | `02`, `07` | 先通过 isolated test/ADR 理解 guest proof，再改 production-safe default；生命周期修复可独立推进。 | 不可直接关生产 flag；先批准 PR-01A。 |
| `base_plan_revision` 曾出现在 model `ChangeSuggestionOutput` 草图。 | 这会暗示模型可生成或决定 concurrency token，违反信任边界。 | 模型只产生 `ModelSuggestedAction`；server 从可信 Plan snapshot 生成 `ServerPreviewMetadata.plan_revision/snapshot_hash`；orchestrator 产生 classification；apply transaction 比较 revision/idempotency。 | `04`, `05`, `07` | 在 PR-05B 前，preview 必须标为可能 stale；不能声称 optimistic concurrency 已完成。 | revision 只能 server-generated，LLM 永不拥有该字段。 |
| 原路线图将 Alembic、concurrency/idempotency、telemetry 合为 PR-05，且与前置条件不一致。 | migration baseline 必须先于任何新持久化；revision/idempotency 是独立业务/API contract；telemetry 不应等 schema work。 | `PR-05A` Alembic baseline，`PR-05B` revision/idempotency；`PR-06A` telemetry，`PR-06B` conversation decision。 | `07` | PR-05A 可与 runtime work 并行；PR-05B/06B 硬依赖 migration。 | 采用 DAG，不以编号假装线性依赖。 |
| 原 PR-01 rollback 提到尚未实现的 `AI_CHAT_RUNTIME` flag。 | 回滚计划依赖未来资产，不可执行。 | PR-01C 的回滚是 forward-safe degraded model skip 或整 PR revert；不允许回到 shared Session/thread 设计。runtime selector 只从 PR-04 起存在。 | `07` | P0 生命周期修复必须自身包含安全降级路径。 | 回滚不可依赖未实施的 feature flag。 |
| Pydantic AI 设计直接进入 adapter。 | 文档研究不能替代特定版本/DeepSeek model/API setting 验证。 | 增加隔离的 `PR-03` compatibility gate：pin version、fake-model/transport tests；真实 DeepSeek 为单独的用户授权 gate。 | `03`, `04`, `07` | adapter PR 被硬阻塞在 PR-03 通过之后。 | 先验证 compatibility，再集成。 |
| Golden dataset 泛称 expected output，示例“周三博物馆改到上午”固定期望 `change_suggestion + confirm`。 | 未给唯一 item、目标时间、booking/constraint 等 fixture 条件时没有充分 ground truth；强制路径会惩罚合规 clarification。 | case 必须具备 `fixture_preconditions`、known/unknown facts、allowed outputs、deterministic domain oracle source；缺关键事实时 clarification 是合格输出。 | `05`, `07` | evaluator 从比较文本改为比较 allowed outcome set + tool/security/domain invariants。 | 不用不充分 prompt 创造伪精确 oracle。 |
| Framework matrix 混合多项技术维度，且没有独立招聘关键词列。 | 总分难复算，也容易误让关键词价值主导选择。 | 采用可复算五维：业务适配30、工程收益25、维护性20、Interview/JD10、成本15。 | `03` | 关键词只影响 10%，移除该维度后 Pydantic 仍居首。 | 保留 Pydantic recommendation；不因此引入所有框架。 |
| 现有 build workflow 被列为验证基础。 | 当前 `build-validation.yml` 只手动触发；手动成功不是 PR CI/required check。 | 增加 `PR-00`：PR/push trigger、required checks、isolated DB tests、artifacts、manual real-provider/deploy 分离。 | `07` | branch protection 的实际设置须 read-only verification；在验证前标 Requires Verification。 | 把 CI gate 做成早期 Must Have。 |
| PR-01B 曾把 Guest session storage 视为 “only if needed”。 | PR-01A 的静态/特征证据表明 Guest 目前只有 browser-persisted raw membership ID；Invite revocation 不会撤销已加入 Guest 的访问。要实现 credential TTL/revocation，服务器必须持久化 token hash、membership/trip binding、expiry 和 revocation state。 | PR-01B 选择独立、可撤销的 guest-session record；其 schema 变更必须在 Alembic baseline 后实施。 | `07`, PR-01A ADR | `PR-05A → PR-01B` 是硬前置，不可在 ad-hoc `create_all` 中引入。 | 不复用裸 membership ID 作为 credential；先完成 migration baseline。 |
| 先前记录 Node 全量 sweep 为 111/116、5 failures。 | 当前完整执行为 134/141 pass、7 failures；受限 `npm test` 虽 13/13 pass，但未覆盖全量 characterization。 | PR-00 将精确保留 7 个失败断言、每次上传完整 artifact，并在修复为稳定 seam 测试后才提升为 Required Check。 | `05`, `07` | CI 不能以局部 green 或删除/skip 断言宣称全绿。 | 以当前 7-failure baseline 为准并逐项关闭。 |

## Thread cancellation and Session lifecycle: precise model

三个事件必须单独记录，不能互相替代：

| Event | 能证明什么 | 不能证明什么 |
|---|---|---|
| HTTP request deadline elapsed | 应用不再等待本次请求，可返回 timeout/degraded response。`asyncio.timeout` 通过取消当前 coroutine 转成 `TimeoutError`。 | 不能证明 worker thread、HTTP socket 或远端 provider 已停止。 |
| Local async task receives cancellation | cooperative coroutine 在下一取消点得到 `CancelledError` 并可在 `finally` 清理本地资源。 | 不能强杀同步函数或已接收的远端请求。 |
| Sync worker/provider actually finishes | local thread/client operation 已返回；可观察 `finished_at`/exception/connection cleanup。 | 即使本地 client 取消，也不能从应用侧证明远端模型没有继续计算。 |

官方依据：Python 的 [`Future.cancel()`](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel) 对已运行/已完成的 callable 可能返回 `False`；[`asyncio.to_thread()`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread) 的职责是把 blocking function 移至线程，不是暴力终止它；SQLAlchemy 要求 mutable Session 在一个 thread/task 内使用，推荐 Session per thread、AsyncSession per task。[SQLAlchemy Session concurrency](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks)

Pydantic AI 同样不应被误解为远端强制停止：其[timeout 文档](https://pydantic.dev/docs/ai/core-concepts/timeouts/)说明 whole-run wall-clock deadline 要由调用方包裹，usage limit 与 request/tool limits 解决的是不同层次问题。

### Failure-injection safety invariants

下列不变量是可测的，也比“所有工作必定停止”更诚实：

1. **No shared request Session**：agent/model/thread tool 从不接收 FastAPI request `Session`；每个同步 read tool 在自己的 worker 中创建、scope、关闭自己的 Session。
2. **No durable side effect after deadline**：agent tools 没有 add/commit/delete；超时/late completion 不触发 proposal、vote、PlanItem 或 audit state 的持久化；apply 仍只在正常 transactional API 中发生。
3. **Late result is not consumed**：deadline 后的 provider/tool completion 被标记并丢弃，不能写 response、不能改变 classification/apply outcome。
4. **Late read remains bounded and authorized**：若不可协作 worker 已经开始，最多完成其已经开始的、trip-scoped、短 Session read；它关闭本地 DB resource，不能跨 trip 或暴露 private DTO。
5. **Local cleanup is observable**：trace 有 `deadline_elapsed`、`cancel_requested`、`worker_finished/late_completion`、Session open/close counts、in-flight metric；本地 client deadline 后资源指标回落。远端完成状态为 unknown，不伪造为 cancelled。

### Required failure injection cases

| Fake | Procedure | Assertions |
|---|---|---|
| Cooperative async provider | wait on event; let `asyncio.timeout` expire; provider sees cancellation and runs `finally`. | request reports deadline; cancellation/cleanup event present; no tool/write afterward. |
| Non-cooperative sync provider | block in `to_thread`/executor past HTTP deadline; release after response. | response returns safely; no request Session passed; late completion recorded/discarded; no durable write; test does not assert worker was killed. |
| Tool starts before timeout | hold safe scoped read after tool begin, then expire request. | session is independent and closes; result cannot reach response/mutation; trip scope retained. |
| Provider error transport | inject 401, 429, 5xx, malformed payload and connection/read timeout. | stable provider failure class, bounded retry/usage behavior, safe UI message key, no write. |

## Pydantic AI Compatibility Gate

`PR-03` is a gate, not the adapter. It must record the exact pinned package/version, Python version, model/provider base URL class, Pydantic settings and test timestamp. The fake gate covers function tool argument validation, structured output/output retry, `UsageLimits`, unknown/malformed tools, timeout/cancellation mapping, and fake HTTP error mapping. Pydantic’s retry behavior must be budgeted explicitly because tool and output retries have distinct behavior; see its [official retry documentation](https://pydantic.dev/docs/ai/core-concepts/retries/).

Only after a separate approval may a dedicated, budget-capped, non-production real-DeepSeek test exercise tool calling, thinking mode, structured output, timeout/usage and error mapping. Results are `Pass`, `Fail`, or `Blocked`; a `Blocked` result never becomes an implicit pass. The full adapter cannot be represented as provider-compatible before that gate.

## Final PR dependency graph

```text
PR-00 CI/release gate foundation ──────────────────────────────────────────────> PR-08 evidence release
PR-01A auth/guest compatibility characterization ─────────────┬> PR-01B bounded guest identity ─> PR-08
PR-05A Alembic baseline ───────────────────────────────────────┘
PR-01C agent deadline/session isolation ─> PR-02 evaluation foundation ─┬> PR-04 Pydantic adapter ─> PR-06A telemetry/rollout ─> PR-08
PR-03 Pydantic isolated compatibility PoC ────────────────────────────────┘
PR-05A Alembic baseline ─> PR-05B revision/idempotency ────────────────────────> PR-08
PR-05A Alembic baseline ─> PR-06B conversation product decision ──────────────> PR-08
PR-05B + PR-06A + approved product ADR ─> PR-07 optional LangGraph evaluation
```

Hard gates: `PR-01A + PR-05A → PR-01B`; `PR-01C + PR-02 + PR-03 → PR-04`; `PR-05A → PR-05B`; `PR-05A → persistent conversation`; `PR-05B + PR-06A + ADR → PR-07`. The other adjacent lanes may run in parallel if they do not modify the same contract.

## First PR Execution Contract: PR-01A

**Name:** Authentication / Guest Compatibility Characterization.

**Scope authorized when implementation is approved:** add isolated tests and an ADR/runbook only. It may inspect code/config key names, but it must not modify production config, invoke AWS, read/print secrets, call real AI, or change authentication behavior.

**Inputs:** a confirmed disposable `TEST_DATABASE_URL`; `MOCK_AI=1`; `DISABLE_SCHEDULER=1`; existing source code and synthetic fixtures. If the disposable DB cannot be proven safe, stop before running tests.

**Test matrix:**

```text
setting = unset | 0 | 1
identity = bearer account | valid bounded guest fixture | absent | forged membership ID
flow = trip read | invite preview | invite join | reopen | logout | cross-trip access
```

**Required outputs:**

* a behavior matrix distinguishing current legacy behavior from intended secure behavior;
* an ADR naming the guest credential proof, trip binding, expiry/revocation and transition plan;
* tests that demonstrate the current exposure without normalizing it as acceptable;
* no source/runtime/environment/AWS modification.

**Acceptance:** reviewers can decide PR-01B without guessing whether `DEV_ALLOW_MEMBERSHIP_HEADER=0` breaks guest flows. If any case is ambiguous, the outcome is “blocked for product/auth decision,” not a speculative implementation.

**Rollback:** revert only additive test/document changes. There is no production rollback because PR-01A makes no production behavior change.

## Final recommendation

Approve implementation only one unit at a time, beginning with PR-01A. Do not authorize a Pydantic package installation or real provider request as part of Phase 0.5; those are explicitly deferred to PR-03 under separate authority.
