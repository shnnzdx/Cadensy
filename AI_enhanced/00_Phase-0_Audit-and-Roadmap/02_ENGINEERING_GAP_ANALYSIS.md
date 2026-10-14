# Cadensy Phase 0 — Engineering Gap Analysis

## 评级方法

* **P0**：上线前必须关闭的安全、数据隔离或不可控可靠性风险；这里的“P0”是修复优先级，不是宣称已经造成生产事故。
* **P1**：显著影响生产质量、后续迁移可信度或作品集可证明性的缺口。
* **P2**：有价值但不应抢占核心安全、评测和业务链路资源的扩展。

“已验证”仅表示存在静态代码/配置证据；“影响已发生”需要另行受控复现。

## P0 — 在任何新 Agent 框架之前处理

### P0-1：生产可接受任意 membership header 的身份回退

| 项目 | 内容 |
|---|---|
| Evidence | `current_membership()` 与 `current_account_user()` 在无 bearer token 时，若 `DEV_ALLOW_MEMBERSHIP_HEADER` 为 `1`，直接按 `X-Membership-Id` 取 membership/user；默认值也是 `"1"`（`backend/app/api/main.py:97-124,127-153`）。部署工作流把该环境变量固定为 `1`（`.github/workflows/backend-ai-runtime-config.yml:172-191,263-264`）；AWS README 也称云端需要它（`AWS/README.md:40-42`）。 |
| Actual / potential risk | 代码和部署配置的组合是已验证的 insecure-by-default 设计。若该 workflow 所描述的 task definition 确实在生产使用，知道有效 membership ID 的攻击者可在无 token 情况下模拟该身份；是否已被利用未知。 |
| 安全复现策略 | 只在新建、无生产数据的 Postgres DB：启动 API 三次，分别设 `DEV_ALLOW_MEMBERSHIP_HEADER=0`、未设置、`1`，以无 bearer/有效 header 请求受保护路由。断言前两者 401，最后一个仅在测试/dev 明确许可时可用。不得在公网或云端执行。 |
| Proposed solution | 将默认改为 secure (`0`)；production config 强制 `0` 并在启动时对 `APP_ENV=production && 1` fail-fast；为 guest invite 设计受限、可撤销、签名的 invite/session identity，而不是全局 membership lookup。前端兼容 header 只保留给显式 local profile。 |
| Acceptance criteria | production deployment manifest 中该变量为 `0`；缺少 bearer 的任意 membership header 都为 401；guest invite 的预览、加入、重开均由受测 token/session 完成；CI 有上述环境矩阵和负向 cross-trip 测试；任何运行时变更均有回滚 task definition。 |

### P0-2：聊天超时后工作线程可能继续使用请求 Session

| 项目 | 内容 |
|---|---|
| Evidence | chat 构建捕获 `db` 的 tools 后，将 `base.call_agent` 提交给 `ThreadPoolExecutor`（`backend/app/domain/chat/service.py:927-952`）；30 秒超时后只调用 `future.cancel()` 和 `shutdown(wait=False)`（:953-958）。底层 provider timeout 为 90 秒（`backend/app/agents/base.py:432-434`）。FastAPI request 的 `get_session()` 在请求结束时退出上下文（`backend/app/db/session.py:41-43`）。现有测试只证明调用者快速得到降级回复（`backend/tests/test_chat_agent_branch.py:234-258`），未断言 worker 结束或不再访问 DB。Python 官方文档说明已运行的 `Future.cancel()` 可能返回 `False`；SQLAlchemy 规定同一 `Session` 不能在并发 thread/task 共享。 |
| Actual / potential risk | 已验证的是生命周期/ownership 不匹配，而不是已发生的数据泄露：HTTP 等待超时、async task 收到取消、同步 thread/provider 真正结束是三个不同事件。已运行线程和被远端收到的请求都不能被应用强制证明“已停止”。风险包括 request Session 并发/关闭后访问、局部线程/连接资源滞留，以及未来若工具被改成写入时扩大副作用。 |
| 安全复现策略 | 在 isolated DB 注入两个 fake：可协作 async provider（观察 `CancelledError` / client close）与不可协作阻塞 sync provider（证明它可能晚完成）。记录 request deadline、cancel signal、tool start/end、Session create/close、late completion。断言的是下列不变量，不是“远端一定停止”：没有 request-scoped Session 被 agent 使用；超时后没有 durable DB write/plan change；任何已开始的晚到只读操作使用自己已 scope 的短 Session 并关闭；晚到结果被丢弃且可观测。 |
| Proposed solution | 模型调用优先使用 async provider path，并用整体、provider、tool 三层 deadline。每个同步 read tool 若必须 `to_thread()`，只传 immutable capability，在线程内新建/关闭独立 Session；外层取消只停止 await，不能假定能杀死该 worker。加入 shared cancellation marker 以阻止尚未开始的 tool，并让所有 delayed completion 只产生安全 telemetry。所有 mutation 继续只在正常 API transaction 中发生。 |
| Acceptance criteria | failure-injection 证明上述四项安全不变量；取消/timeout 用稳定错误码、trace id、in-flight/late-completion 指标记录；连接池/线程指标在 provider client 的本地最大 deadline 后回落；工具保持只读。不得把“远端 provider 一定取消”作为验收项。任何未来写工具必须独立 transaction/outbox 设计。 |

## P1 — 高价值的生产与作品集缺口

### P1-1：没有可演进的 schema migration 路径

* **Evidence**：初始化采用 `Base.metadata.create_all()` 及少量 additive ALTER（`backend/app/db/init_schema.py:16-45`）；未发现 Alembic 配置或 revision 历史。
* **Risk**：新的 conversation、evaluation、idempotency 或 version columns 无法以审计、可回滚、可升级的方式部署；`create_all` 不处理复杂数据迁移。
* **Reproduction**：在 disposable DB 对比旧/新 schema，模拟 nullable-to-not-null、index/backfill 与 downgrade；不接触 RDS。
* **Solution**：先引入 Alembic baseline revision，CI 使用空库 upgrade 和 production-like upgrade；每一张新表都带 migration/rollback note。
* **Acceptance**：`alembic upgrade head` 可在干净和前一版快照 DB 完成，schema diff 为零，迁移有锁/时长/回滚 runbook。

### P1-2：Preview/Apply 没有显式 optimistic concurrency 或 idempotency 合同

* **Evidence**：Plan/PlanItem 没有 revision/version 字段；`PlanChange` 是 append-only（`backend/app/db/models.py:305-410`）。partial uniqueness 可以阻止同一 item 的 open round/pending proposal，但不能表达“这个 preview 基于哪一版计划”。提交端重新分类是正确的第二道保护（`backend/app/domain/decisions/orchestrator.py:437-512`）。
* **Risk**：重复点击/网络重试或多人并发修改可能造成用户看到 stale preview、得到意外 reclassification，或难以去重；这不是已证明的规则绕过。
* **Reproduction**：在 isolated DB 并发提交同 item 的相同/不同 request，并在 preview 和 apply 中间更改 PlanItem；记录 409、重复 records 和最终决策路径。
* **Solution**：加入 plan/item revision（或 canonical snapshot hash）和 Idempotency-Key；preview output 带 base revision，apply transaction 以 compare-and-swap 验证，过期返回明确 409 及刷新提示。
* **Acceptance**：同 key 的重复请求可安全重放；旧 revision 不会应用且有 machine-readable stale code；竞争测试稳定通过。

### P1-3：观测有 trace 文件但缺少完整模型调用、请求关联与集中化出口

* **Evidence**：`record_model_call()` 定义于 `backend/app/agents/trace.py:56-119`，本轮 `rg` 未找到调用点；round trace 会写本地 JSONL（:121-176）。API response 未建立统一 request/trace correlation contract。
* **Risk**：无法可靠计算 provider latency、fallback、token/cost 或将 chat request 与 CloudWatch 日志关联；本地容器文件也不是生产持久 telemetry。
* **Reproduction**：MOCK 模式下触发 success/fallback/timeout/tool guard，验证每一条都有 request_id、trace_id、route、provider/model、usage、failure class，且没有 raw private text。
* **Solution**：定义 vendor-neutral structured events，以 OpenTelemetry-compatible spans/exporter 进入 CloudWatch；敏感 payload 默认不记录；trace id 在 API response/debug header 中可控暴露。
* **Acceptance**：一个 eval case 能连起 request→model→tool→domain outcome；fallback 和 error 有分类；redaction regression tests 通过；生产 retention/access policy 有文档。

### P1-4：聊天 history 是不持久且客户端提供的短期上下文

* **Evidence**：前端 state 保存 messages（`trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:73-192`），请求仅 `history.slice(-10)`（`TripAppState.jsx:1032-1038`）；API 限制 history 最多 10（`backend/app/api/main.py:278-287`）。
* **Risk**：刷新、换设备和新抽屉会丢失上下文；model 看到的是不可信输入，历史 candidate options 需持续由 server revalidate。不能把它写成长期记忆。
* **Reproduction**：刷新/reopen/change item 的 UI test；提交带注入文本、跨 trip item id、过期 candidate option 的 API tests。
* **Solution**：先定义最小 server-side `ConversationTurn` 的权限、保留期和 redaction policy，或明确维持无持久聊天；无论哪种，server 用当前 scope/plan 重建事实，history 只能作语义线索。
* **Acceptance**：明确产品选择；若持久化，成员/Trip scope、deletion/retention、migration 和 privacy tests 完整；若不持久化，UI 明示有限上下文且所有引用仍 revalidate。

### P1-5：测试套件已有 5 个红色的源码字符串契约

* **Evidence**：本轮安全 Node run 的结果为 111/116 pass，5 fail；失败文件与行号见 `01_CURRENT_STATE_AUDIT.md`。它们主要期望旧 helper 名称/结构（例如 `session-runtime-request-identity-cutover.test.mjs:22-26`）。

> PR-01A preparation update (2026-10-03): the original Phase 0 result above is retained as historical evidence. A newer full frontend characterization sweep measured 134/141 pass and 7 failures; the additional, exact current baseline and the no-delete/no-skip repair rule are in `07_IMPLEMENTATION_ROADMAP.md` and `10_PR01A_AUTH_GUEST_BASELINE.md`.
* **Risk**：CI 若包含这些测试会失去可信绿灯；源码 regex 容易因合法重构失效。
* **Reproduction**：已完成的非破坏性 Node command；无需环境或服务。
* **Solution**：先由行为测试覆盖 shared session/nav public contract，再只保留必要的 architecture boundary assertions；更新旧 cutover test，不能简单删除断言。
* **Acceptance**：全部测试绿；每个替换断言清楚保护一个 frozen boundary；CI 命令和本地命令一致。

### P1-6：部署事实和仓库身份漂移，云端状态需要重新证明

* **Evidence**：本地 remote/GitHub 是 `shnnzdx/Cadensy`，而 AWS URL/README 仍列出 `shnnzdx/cap_stone`（`AWS/TRIPSYNC_AWS_URLS.md:20-51`）；文档还同时包含旧 dual-provider 与 DeepSeek-only 描述。
* **Risk**：错误仓库或旧 task definition 可能被当作 release source；运行手册不再可作为审计证据。
* **Reproduction**：获得授权后，以只读 IAM identity 核验 ECS task image digest、task definition、environment key names、ALB health、Actions workflow repository；不打印 secret value。
* **Solution**：建立版本化 deployment manifest 和一次 read-only evidence capture；将历史 runbook 标为 archived；把 source repo/commit/image digest 写入 release artifact。
* **Acceptance**：每个 live URL、workflow、image digest 和 commit 有同一来源链；无凭据泄露；演练 rollback 到前一 task definition。

### P1-7：调度器在多副本运行时缺少显式 leader/lease 证据

* **Evidence**：每个 FastAPI lifespan 可启动 scheduler（`backend/app/api/main.py:63-74`）；scheduler 每 tick `asyncio.to_thread(run_once)`，捕获并记录异常（`backend/app/jobs/scheduler.py:25-51`）。
* **Risk**：多 ECS task 可能并发结算同一 round/proposal。部分 domain 操作或许幂等，但未见 leader/lease 或多副本 integration proof。
* **Solution / acceptance**：在 transaction lock、DB lease 或独立 worker 中择一；在两实例测试中验证只产生一次可见 outcome，失败可重试。

### P1-8：prompt injection 与日志红线需要主动测试，而不只靠 prompt/tool 注释

* **Evidence**：当前 tools 有强 scope 和 safe schema（`backend/app/agents/tools.py:2-5,38-47`），但模型会收到 user message/history；trace redaction 是自维护代码。不存在已验证的 adversarial corpus/deny-by-default payload schema。
* **Risk**：新字段、历史文本或 provider 行为变化可能让模型尝试越权工具或让日志包含敏感理由。
* **Solution / acceptance**：建立 injection、cross-trip ID、private constraint、tool argument fuzz tests；所有 tool output 使用显式 model-safe DTO；测试确保模型不能得到 raw private text/identity，guard reject 不改变 DB。

## P2 — 只在业务证据出现时做

| 事项 | 为什么不是当前必须 | 触发条件 |
|---|---|---|
| LlamaIndex/RAG | 当前主要事实来自关系型 Trip/Plan/Place 数据，未看到私有文档检索产品需求。 | 用户需要检索 booking confirmation、政策、附件或旅行文档，并能实现 trip/membership metadata filter、citation、deletion/retention。 |
| MCP | 内部 typed read-only tools 已能表达现有能力；多一个 protocol 不会自动提高安全性。 | 需要跨边界给可信外部客户端/服务提供受审计工具，且 capability auth/sandbox 已设计。 |
| CrewAI / AutoGen 多智能体 | 三条现有 AI route 不具备 delegation/shared state，强行改名会误导面试。 | 有真正独立角色、handoff、共享状态、成本预算和 end-to-end 评测证明单 agent 不够。 |
| LangGraph 可恢复 replan | 很适合长期可恢复工作流，但当前 Change Preview 不需要它。 | 有异步、长时、可暂停/恢复的 whole-trip replanning，且明确单一 state owner、checkpoint/compensation 和 human review。 |
