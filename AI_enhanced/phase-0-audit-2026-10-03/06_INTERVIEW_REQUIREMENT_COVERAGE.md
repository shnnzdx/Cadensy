# Cadensy Phase 0 — Interview Requirement Coverage

## 归属与表述规则

Cadensy 是团队 Capstone。以下“当前证据”证明项目仓库具有某能力，不自动证明为个人独立贡献。对外材料必须分别标记：

* **Historical team capability**：项目已有、来源可能跨团队；
* **Historical individual contribution**：只有用户可用本人提交记录、PR、设计记录或 teammate confirmation 证明时才写；本审计不推断；
* **New portfolio work**：未来由本用户主导且能以 PR/eval/release evidence 证明的工作；
* **Planned**：本文方案，尚未实现，不能写入简历的完成项。

## 能力覆盖矩阵

| 领域 | 状态 | Current evidence | 缺口 / proposed work | 面试价值与优先级 |
|---|---|---|---|---|
| Python fundamentals | Already Implemented | dataclass、typing、error classes、pure domain functions，如 `agents/base.py`、`constraints/engine.py`。 | 增加 typed adapter 和清晰 error taxonomy。 | 能解释可维护性；P1。 |
| FastAPI | Already Implemented | dependency injection、Pydantic request models、lifespan、HTTP mapping（`api/main.py`）。 | production config fail-fast、request correlation。 | Backend 基础；P0/P1。 |
| REST / HTTP | Partially Implemented | scoped routes、401/403/404/409/422 语义、CORS parser。 | idempotency key、stale revision、统一 error code。 | 可回答 API 语义；P1。 |
| Async / concurrency | Partially Implemented | FastAPI + scheduler `to_thread`。 | 修复 ThreadPool/Session timeout lifecycle；多副本 scheduler proof。 | 关键追问；P0。 |
| SQLAlchemy / PostgreSQL | Already Implemented | models、scope queries、transaction-per-request、unique/partial indexes。 | Session factory边界、pool metrics、migration discipline。 | Backend 强项，P1。 |
| Transaction / index / locking | Partially Implemented | vote/pending proposal uniqueness（`db/models.py:335-410`）。 | optimistic locking/idempotency/lease 或 row-lock tests。 | 系统设计亮点，P1。 |
| LLM fundamentals | Already Implemented | provider config、model limits、fallback、prompt/context separation。 | model/version/provider behavior report。 | 可解释成本/概率失败，P1。 |
| Function calling | Already Implemented | read-only tools、schema、guard/cache、candidate validation。 | typed Pydantic tools和 adversarial eval。 | AI Agent 核心，Must Have。 |
| ReAct / agent loop | Already Implemented | custom multi-round tool loop `call_agent()`。 | 不把它宣传为 multi-agent；补 trajectory metrics。 | 可展示底层理解，Must Have。 |
| Agent frameworks | Missing | requirements 未含主流 framework。 | Pydantic AI adapter + documented A/B。 | 目标岗位的明确差距，Must Have。 |
| Tool security | Partially Implemented | closure-bound scope、read-only handlers、guard limits。 | production auth header、fuzz/injection tests、deny-by-default DTO。 | 安全深度，P0/P1。 |
| Structured output | Partially Implemented | planner manual schema/parser。 | Pydantic output types/validators用于 chat，provider compatibility test。 | Applied AI 核心，Must Have。 |
| Agent state / memory | Partially Implemented | Plan/decision 数据持久化；chat short history。 | 明确不持久化或设计 scoped ConversationTurn。 | 需诚实说明，P1。 |
| RAG | Not Necessary for Current Product | 没有私有文档检索需求，现有事实为 relational。 | 仅在 travel documents 场景出现时做 metadata-filtered RAG。 | 说清“为何不加”比盲加更有价值；P2。 |
| MCP | Not Necessary for Current Product | 内部 tools 已满足。 | 只有跨系统可信 tool 协议需求才评估。 | 解释 tradeoff；P2。 |
| Multi-agent | Not Necessary for Current Product | Chat/Planner/Explainer 是 route，不是 delegation/shared-state agent team。 | whole-trip graph 真正需要分工时再评。 | 避免技术夸大；P2。 |
| Agent evaluation | Partially Implemented | real planner/tool harness、pytest。 | versioned golden set、A/B runner、failure taxonomy、human review。 | 最能提升作品可信度；Must Have。 |
| Prompt injection / privacy | Partially Implemented | private-aware tools、trip scope。 | red-team corpus、history trust boundary、logging tests。 | 高价值安全话题；P0/P1。 |
| Observability | Partially Implemented | trace IDs/JSONL scaffolding。 | model calls接线、OTel/CloudWatch、cost/latency dashboard。 | Production readiness；P1。 |
| Cloud deployment | Requires Verification | Docker/workflows/AWS docs 描述 ECS/ALB/RDS。 | read-only environment evidence、repo drift cleanup、rollback proof。 | 不可只靠 README；P1。 |
| System design | Partially Implemented | deterministic decision boundary、scope、hybrid planner fallback。 | ADR、load/failure/data-flow diagrams、concurrency design。 | 高级面试；P1。 |
| Fault tolerance | Partially Implemented | provider fallback、planner rules fallback、scheduler catch/retry。 | cancellation, idempotency, circuit/timeout metrics, failure injection。 | Production discussion；P0/P1。 |

## 最适合面试展示的真实故事

1. **“模型受限于业务规则，而不是被 prompt 假装限制。”** 展示 chat read-only、candidate IDs、classifier、human approval 的 code path。
2. **“为什么不直接上多智能体/RAG。”** 因为 product facts 是结构化 trip state；系统先解决正确性、隐私和可评测性。说明触发条件而非背框架名。
3. **“怎样把自研 agent runtime 升级为可证明的 framework integration。”** 保留 legacy control，Pydantic AI 从一个 typed no-write chain 开始，统一 eval、可回滚。
4. **“发现并修复一个真实后端可靠性问题。”** ThreadPool timeout 与 Session lifetime；用 failure injection、cancellation design、connection metrics 证明修复。
5. **“生产安全比 demo 更重要。”** `DEV_ALLOW_MEMBERSHIP_HEADER` 的 secure-by-default 改造，guest identity 设计、negative auth tests、deployment verification。

## Future resume/portfolio evidence checklist

以下都是**未来目标**，完成前不可使用过去式：

* PR 链接：P0 auth/cancellation、eval foundation、Pydantic adapter、A/B report；
* benchmark artifact：dataset hash、case count、runtime/model config、safety/latency/cost profile；
* test artifact：isolated DB and failure-injection green output；
* deployment artifact：commit→image digest→task definition→health/rollback 的只读证据链；
* ADR：为什么 Pydantic AI、为什么暂不 LangGraph/RAG/MCP、domain rules如何保持 owner；
* contribution evidence：本人负责的 modules、review、runbook 或 PR author，而不是笼统把 team project 全归给一人。

## Suggested interview-safe wording

可在后续真正完成、并带证据时说：

> Led a staged migration of a trip-planning chat preview from a custom tool-calling runtime to a typed Pydantic AI adapter, preserving deterministic approval rules; compared both paths with versioned safety, trajectory, latency, and cost evaluations.

现在只能说：

> Audited a team Capstone’s custom AI runtime and designed a staged, evaluation-first Pydantic AI integration plan.

后者是本 Phase 0 的真实成果；前者仍是 Planned。
