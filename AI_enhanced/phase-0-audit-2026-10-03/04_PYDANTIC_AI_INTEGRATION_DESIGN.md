# Cadensy Phase 0 — Pydantic AI Integration Design

## 目标与非目标

**目标链路**：Pydantic AI-powered **Chat Change Request & Impact Preview**。它把自然语言解释、受限只读 tool calling、typed change suggestion、output validation 和受控 execution 放进一个可独立评测的 adapter；随后仍把结果交给既有 deterministic domain 分类。

**非目标**：不让模型写 `PlanItem` / vote / proposal；不替换 `constraints.engine`、`decisions.orchestrator`、session-runtime、navigation policy；不声称持久 memory；不在第一阶段迁移 Planner 或引入 multi-agent / RAG / MCP。

## 目标调用链

```text
POST /api/trips/{trip_id}/chat
  -> authenticate + TripScope (unchanged)
  -> ChatRuntimeSelector(flag)
      legacy: current base.call_agent control
      pydantic: PydanticChangePreviewAdapter.run()
          -> typed safe read tools (new DTO-only seam)
          -> ModelOutput (Pydantic validated model action only)
          -> server revalidates item/candidate/patch against fresh scoped facts
          -> server composes ChatPreviewResponse metadata
  -> existing orchestrator.classify_change() for deterministic preview
  -> explicit submit-change API later performs final reclassification + commit
```

这个链路满足现有 `main.py:832-859` 的“chat endpoint never calls `propose_change()`”契约。模型最终只能返回建议；domain rules 决定 NOTICE/ROUND/REOPEN_ROUND/CONFIRM，用户仍在显式 apply 之后进入已有决策路径。

## Planned contracts

以下为计划中的 DTO 草图，不是当前代码或 API schema。

```python
class ServerRunInput(BaseModel):
    trip_id: str                 # server-derived; never model supplied
    actor_membership_id: str     # server-derived; never model supplied
    message: str                 # untrusted user text
    history: tuple[HistoryTurn, ...]  # untrusted semantic context, bounded
    selected_item_id: str | None
    request_id: str              # server-generated correlation id

class ClarificationOutput(BaseModel):
    kind: Literal['clarification']
    reply: str

class ModelSuggestedAction(BaseModel):
    kind: Literal['change_suggestion']
    item_id: str
    requested_patch: SafePatch
    explanation: str

class NoChangeOutput(BaseModel):
    kind: Literal['no_change']
    reply: str

ModelOutput = ClarificationOutput | ModelSuggestedAction | NoChangeOutput

class ServerPreviewMetadata(BaseModel):
    plan_revision: str | None       # derived from trusted snapshot, never model output
    snapshot_hash: str | None       # temporary trusted metadata before revision exists
    generated_at: datetime
    request_id: str

class DeterministicPreview(BaseModel):
    classification: ClassificationDTO | None
    stale: bool

class ChatPreviewResponse(BaseModel):
    action: ModelOutput
    metadata: ServerPreviewMetadata
    deterministic: DeterministicPreview
```

责任严格分层：**Model-generated SuggestedAction** 只包含受限的建议/澄清；**Server-generated Preview Metadata** 从可信 current Plan snapshot 生成 revision 或临时 snapshot hash；**Deterministic Classification** 由现有 orchestrator 重新计算；**Transactional Apply** 只发生在既有 submit API，并在未来 revision/idempotency PR 中 compare-and-swap。模型永远不生成、选择或确认 `plan_revision`。

在 revision 尚未实现时，adapter 只能用 fresh authoritative read 再调用既有 classifier，并将 preview 标为可能过期；这不是 optimistic concurrency，也不能以此宣称已完成 optimistic locking。

## Dependency injection 与 SQLAlchemy Session 设计

绝不能把当前 request 的同步 SQLAlchemy `Session` 捕获进一个背景/线程 agent。当前设计正存在该风险（`backend/app/domain/chat/service.py:935-958`）。建议的 Pydantic dependency 是 immutable capability，而不是 ORM session：

```python
@dataclass(frozen=True)
class ChangePreviewDeps:
    trip_id: str
    actor_membership_id: str
    request_id: str
    trip_reader: ScopedTripReader  # opens/closes its own session per read
    clock: Clock
    policy: ChangePreviewPolicy
```

`ScopedTripReader` 的每个 read method 都：

1. 从 factory 取得新的短生命周期 sync `Session`；
2. 调用 `for_membership(session, membership).require_*` 或等价 query；
3. 转成无 identity/raw-private-text 的 DTO；
4. 在同一个函数结束前关闭 session；
5. 不提供 add/delete/commit 接口。

使用 async Pydantic tools 包装同步 reader（例如受控 `asyncio.to_thread`），或在后续统一改为 AsyncSession；两种情况下都不得跨 task/thread 复用 FastAPI request Session。`to_thread()` 仅把同步函数移出 event loop；取消 await 不会强制终止已开始 worker，因此所有 worker 都必须只拥有自己的短 Session，且其晚到结果不能进入 response 或 mutation 路径。Pydantic AI dependencies 通过 `RunContext` 提供给 instructions/tools/output validators 的模型正适合这一 capability contract，见[官方 dependencies 文档](https://pydantic.dev/docs/ai/core-concepts/dependencies/)。

## Tool mapping

| 现有 tool | 新 adapter 的计划工具 | 安全要求 |
|---|---|---|
| `get_current_plan` | `read_current_plan(day)` | 返回 model-safe `PlanSnapshotDTO`，无 membership IDs、私密原因或未授权 item。 |
| `get_trip_facts` | `read_trip_facts()` | 只返回 destination/date/status/count/currency/budget summary。 |
| `classify_change` | `preview_domain_impact(item_id, SafePatch)` | 调用既有 read-only classification；不创建 proposal/notice/round。 |
| `find_replacement_place` | `find_scoped_replacements(item_id, keywords)` | 只返回 canonical provider/cache candidates；关键词限制和 current trip item scope。 |
| `propose_options` | 暂缓或转为 `read_safe_options` | 仅允许 server-validated suggestions；如果模型不能给安全 DTO，先不暴露该工具。 |

工具函数应显式 typed、窄 schema、独立 authz；不把通用 ORM/SQL/HTTP 工具暴露给模型。Pydantic AI 的 function tool schema 和 `RunContext` 支持使这个映射可行，但工具 schema 不是授权机制；授权仍须在 domain/read model 内执行。[官方 function-tools 文档](https://pydantic.dev/docs/ai/tools-toolsets/tools/)

## Structured output 与最终验证

`output_type=ModelOutput` 只验证模型动作的 syntax/type；它不赋予业务合法性，也不产生 server metadata。Pydantic AI 将 Pydantic models 转为 output schema 并验证返回数据，[官方 output 文档](https://pydantic.dev/docs/ai/core-concepts/output/) 说明默认 structured output 借助 tool calling。

因此输出 validator 的职责是：

* `item_id` 属于 current trip/current active plan；
* patch 仅含显式 allowlist，值范围有效；
* replacement candidate 仍存在且属于当前检索 scope；
* model 没有声称已应用、已投票或暴露私密理由；
* server 在模型输出之外从 fresh trusted snapshot 生成 `ServerPreviewMetadata`；模型 input/output 中没有 revision，故没有可供模型比较、生成或回显的 concurrency token；
* 将任何 decision path 重新交给 `orchestrator.classify_change()`，不相信模型自报的 path。

validator 失败应产生有限 retry；retry 耗尽返回已定义的 safe degraded result，不将 validation exception 直接暴露给 UI。

## DeepSeek model / provider compatibility

当前源码/运行说明使用 `deepseek-v4-flash`（`backend/app/agents/AI_AGENT_SUMMARY.md:13-27`）。Pydantic 官方 DeepSeek 文档展示了 `OpenAIChatModel` 与 `DeepSeekProvider`，并直接列出此模型名。[官方 provider 文档](https://pydantic.dev/docs/ai/models/deepseek/)

适配器的第一版应以 DeepSeek Chat Completions 的显式 model/provider 为目标，pin 依赖版本与 HTTP deadline。重要 caveat：文档说明 V4 thinking 默认开启时，forced tool choice 不受支持；可靠 structured output 场景需试验 `thinking=False`。Responses API 也可能静默忽略不支持字段，且是无状态服务。因此：

* 不将“OpenAI-compatible”当成所有参数/功能都相容；
* history 仍由本服务以受限的、脱敏的方式每次传递；
* 不假设 server conversation、background mode、compaction 或 NativeOutput 已可用；
* provider contract test 需包含 tool call、typed output、bad schema、timeout、usage 和 fallback；无 PoC 前标为 **Requires Verification**。

## Deadline、usage、error policy

计划采用三层明确 budget，而非当前 ThreadPool wrapper：

| 层 | 计划策略 |
|---|---|
| Request deadline | async request lifecycle 使用整体 25–30 秒 cancelable deadline；客户端断开时取消任务。 |
| Provider deadline | http client connect/read/write/pool deadline 小于整体 deadline；不能保留 90 秒 request 在 30 秒 API response 后继续跑。 |
| Tool deadline | 每个 read tool 有较短 budget；超时只产生 safe unavailable result，不写库。 |
| Usage limits | 明确 requests、tool calls、input/output/total tokens 限制；实际 API/字段以 pin 后的 Pydantic AI version 为准。 |
| Retry | schema/可纠正 output 最多有限 retry；auth、scope、timeout、provider 4xx 不能盲重试。 |

Pydantic AI 将 timeout、retry 和 usage limits 作为明确运行控制面；其官方 timeout 文档特别说明 whole-run wall-clock 需要调用方以 `asyncio.timeout` 等方式包裹，且底层 model setting 是否转发取决于 model class。具体 constructor/API 名称必须以实施时 pin 的版本为准，而不能照抄本文。参考[官方 timeout 文档](https://pydantic.dev/docs/ai/core-concepts/timeouts/)、[retry 文档](https://pydantic.dev/docs/ai/core-concepts/retries/)和[output/usage 说明](https://pydantic.dev/docs/ai/core-concepts/output/)。

错误映射建议：scope/auth failure→不泄露资源的 401/404；model timeout/provider unavailable→可重试的 degraded answer + trace id；invalid output→safe clarification/fallback；usage cap→明确 budget reason。任何错误都不改变 plan/decision state。

## Observability 与 privacy

每次 run 记录（不记录 raw prompt/history/private constraint）：

```text
request_id, trace_id, runtime=legacy|pydantic, route, provider, model,
prompt_version, output_contract_version, dataset_case_id(optional),
deadline outcome, retry_count, tool names/counts, guard/validation result,
token usage, latency, fallback reason, domain classification
```

输出进入现有结构化 logger，未来导出 OpenTelemetry/CloudWatch。Pydantic Evals 可评最终 output 与 tool trajectory，[官方 Evals 文档](https://pydantic.dev/docs/ai/evals/evals/)；是否启用 Logfire 是单独的数据处理/成本决定，不是自动前提。

## Feature flag、回滚与 A/B

新增 server-only runtime selection：`AI_CHAT_RUNTIME=legacy|pydantic`，默认 `legacy`。不得由浏览器任意指定；production 切换应在变更单/配置审查下完成。`pydantic_shadow` 仅在用户明确批准成本、privacy 和采样策略后才考虑，且绝不影响 response 或执行 mutation。

回滚即把 flag 改回 legacy，保留相同 API response/domain contract；不要删除 `base.py`，直到等价 dataset、failure-injection、real-provider eval 达标并完成一段稳定观察期。

## Planned files and phased sequence

| Phase | 计划文件（非本轮改动） | 交付物 |
|---|---|---|
| Contract first | `backend/app/agents/contracts.py`，`backend/tests/test_change_preview_contracts.py` | safe DTO、golden mock tests、无 framework 行为基线。 |
| Read capability | `backend/app/agents/scoped_reader.py`，`backend/tests/test_scoped_reader.py` | per-call Session + cross-trip/private-data negative tests。 |
| Compatibility gate | isolated `backend/compat/pydantic_ai/` tests/report（路径待实施确认） | pin package/API；fake model/transport 验证 tool、output、thinking setting、timeout/cancel、usage、error mapping；无 Chat route wiring。 |
| Adapter | `backend/app/agents/pydantic_change_preview.py`，`backend/app/agents/runtime_selector.py` | typed Pydantic AI agent，no-write tools，deadline/usage/error mapping。 |
| Service wiring | `backend/app/domain/chat/service.py`（最小 selector seam） | legacy default，response contract unchanged。 |
| Evaluation | `backend/evals/...`，tests/fixtures | legacy vs Pydantic same input/report。 |
| Controlled provider PoC | 兼容性 gate 的独立 test/eval command，专用 DB/config | 仅在用户付费授权后保存 provider matrix；未通过或 blocked 不进入 Adapter。 |

每个 phase 独立 PR、独立 test/rollback；任何一个未通过则停在 legacy runtime。完整 PR 顺序见 `07_IMPLEMENTATION_ROADMAP.md`。
