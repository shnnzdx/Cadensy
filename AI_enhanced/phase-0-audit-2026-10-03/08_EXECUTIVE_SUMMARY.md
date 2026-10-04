# Cadensy Phase 0 — Executive Summary

## 1. Cadensy 当前真正的优势是什么？

它的优势不是“用了 AI”，而是把 AI 放在正确的边界内：Chat 可以理解/建议，Planner 只能从 canonical candidate IDs 选择，而 constraints/decisions/domain 仍控制 Notice、Round、Reopen Round、Confirm 和最终 mutation。Trip scope、private-safe tools、planner post-validation 和 rules fallback 形成了一个可解释的 hybrid AI/backend architecture。

这是面试中比“接了一个聊天 API”更有价值的基础。证据在 `backend/app/agents/tools.py`、`planner.py`、`domain/constraints/engine.py`、`domain/decisions/orchestrator.py`，详见 [current audit](01_CURRENT_STATE_AUDIT.md)。

## 2. 当前最值得优先解决的五个问题

1. **P0 identity 安全**：`DEV_ALLOW_MEMBERSHIP_HEADER` 在代码默认和部署 workflow 都为 enabled，生产必须 secure-by-default。
2. **P0 timeout 生命周期**：ThreadPool timeout 不等于取消；后台模型工具可能继续持有已结束请求的 SQLAlchemy Session。
3. **评测/测试可信度**：先修 5 个红色源码契约测试，建立 versioned golden dataset、failure injection 和 fair runtime A/B。
4. **数据库演进与并发**：引入 migration、stale preview revision/idempotency、并验证多副本 scheduler 行为。
5. **观测与云端证据**：接通 model-call telemetry，并以 read-only evidence 解决 Cadensy vs `cap_stone` 部署文档漂移。

这些是代码/配置证据驱动的优先级，不是泛泛“最好加 RAG/多 Agent”的建议。详见 [gap analysis](02_ENGINEERING_GAP_ANALYSIS.md)。

## 3. 是否正式推荐 Pydantic AI？为什么？

**是，正式推荐作为第一个集成框架，但仅用于 Chat Change Request & Impact Preview。**

理由：当前是 Python/FastAPI/Pydantic/DeepSeek/OpenAI-compatible 环境；Pydantic AI 原生强调 typed dependencies、function tools、structured outputs/validators 和 code-first evals。它可替代模型侧手写 adapter，同时保持现有 deterministic domain 的业务裁决权与 legacy runtime control group。

不能跳过 compatibility PoC：DeepSeek V4 的 thinking/tool-choice/structured-output 和 stateless Responses caveat 都需在专用 DB、成本上限下测试。[官方 DeepSeek 文档](https://pydantic.dev/docs/ai/models/deepseek/) 是选择依据，不是已有集成的证明。具体设计见 [integration design](04_PYDANTIC_AI_INTEGRATION_DESIGN.md)。

## 4. 是否需要 LangGraph？什么条件下需要？

**当前不需要。** 它的价值出现在可恢复的 whole-trip replanning：snapshot、候选检索、draft、validation、bounded repair、checkpoint/resume、human review、revalidate-before-commit。

只有能够证明这些是用户价值、并已经定义单一 state owner、checkpoint privacy、idempotency/compensation 与 eval 计划时，才采用 LangGraph。即时 Chat preview 没有该复杂度需求。详见 [framework evaluation](03_FRAMEWORK_EVALUATION.md)。

## 5. 最值得深入开发的核心业务链路是什么？

**“自然语言提出变更 → typed impact preview → deterministic classification → human approval → optimistic-concurrency apply”**。

它直接使用 Cadensy 的差异化产品规则，也能展示 Agent tools、Pydantic contracts、authorization、transaction、staleness、evaluation、observability 与 cloud release，而不是做一个孤立聊天机器人。

## 6. 如何通过证据而不是自我宣传证明质量？

* 保留 legacy runtime，使用相同 fixture/model budget/output contract 做 A/B；
* 版本化 golden dataset，保留 output、tool trajectory、failure class、latency、tokens/cost；
* 以 P0 failure-injection 证明 timeout/cancellation/session cleanup；
* 以 negative tests 证明跨 trip、private data、prompt injection 和 write attempts 被拒绝；
* 以 migration/concurrency tests 证明重复请求和 stale preview 不会静默破坏计划；
* 以 commit→image digest→task definition→health/rollback 的可审计链证明 cloud，且区分 historical team capability、个人贡献和 planned work。

## 7. 如果只能完成前三个优先工作单元，应交付什么？

Phase 0.5 已将原先过大的 P0 PR 拆开，因此“前三个”不是一次性关闭所有风险：

1. **PR-01A auth/guest characterization**：在专用测试库中证明 header/guest 依赖，交付 ADR 和环境矩阵；不直接改生产行为。
2. **PR-01C lifecycle/session isolation**：消除 request Session 跨 agent work 的 ownership，并用 cooperative/non-cooperative failure injection 证明无持久化副作用和安全 late completion 处理。
3. **PR-02 evaluation foundation**：契约测试恢复绿色、versioned synthetic dataset、mock/failure-injection runner、现有 planner harness 的统一报告契约。

随后才是 `PR-03` compatibility gate、`PR-04` Pydantic AI adapter；安全 guest identity 的 `PR-01B` 依赖 PR-01A，可与 PR-01C/02 以受控方式并行。即使尚未做 LangGraph、RAG、持久 memory 或完整云端升级，这条顺序也会留下有主流框架准备度、可靠性思考和可验证证据的 AI Agent + Python Backend 基础。

## 本 Phase 0 的实际交付与停止点

已交付审计/设计文档及 Phase 0.5 修订；未修改任何业务源码、未安装依赖、未运行后端破坏性测试或真实 DeepSeek、未调用 AWS、未 commit/push。下一步只有在获得明确授权后，按 [revised roadmap](07_IMPLEMENTATION_ROADMAP.md) 从 PR-01A 开始。
