# Cadensy Phase 0 — Framework Evaluation

## 先区分概念

下表避免把“框架”“运行时”“工作流”“观测平台”混为一谈。

| 概念 | Cadensy 当前 | 本次选择的含义 |
|---|---|---|
| Agent runtime | `backend/app/agents/base.py` 的自研 OpenAI-compatible tool loop | 当前 baseline/control group，不能在比较前删除。 |
| Agent framework | 当前没有 Pydantic AI/LangChain/LangGraph/Agents SDK 依赖（`backend/requirements.txt:1-8`） | 首选用 Pydantic AI 为 Chat Change Preview 提供 typed tools/output/deps。 |
| Workflow orchestration | domain service + generator + scheduler；不是 durable graph | LangGraph 只在可恢复 whole-trip replanning 证明需要时评估。 |
| State/memory | Plan/decision DB 有状态；chat 仅 frontend + 10 条客户端短期 history | 不把短 history 误称 memory；持久 conversation 是独立产品/隐私设计。 |
| Tool protocol | 进程内 `AgentTool`、Python closure scope、JSON schema | 先保留内部 tool semantics；MCP 不是本阶段目标。 |
| Evaluation | pytest + custom real-provider planner/trace scripts | Pydantic Evals 可作为统一 eval layer，但无框架强制绑定。 |
| Observability | JSONL agent trace；未接通 `record_model_call()` | 需要 vendor-neutral structured telemetry / OTel strategy。 |

## 评价标准与带权矩阵

评分是架构适配度（1–5），不是社区规模或“功能越多越好”。为避免把招聘关键词与实际价值混在一起，矩阵重组为五个独立维度；成本列的高分表示更低的学习/迁移成本。总分公式为 `Σ(评分/5 × 权重)`，因此可复算为 /100。没有通过真实 provider PoC 的能力都不算已实现。

| 独立维度 | 权重 | Custom runtime | Pydantic AI | LangGraph | LangChain | OpenAI Agents SDK | LlamaIndex |
|---|---:|---:|---:|---:|---:|---:|---:|
| 实际业务适配度：当前 Chat Preview / DeepSeek / domain boundary | 30 | 5.0 | 5.0 | 2.5 | 3.0 | 3.5 | 2.0 |
| 技术工程收益：typed contract、eval、timeout/tool controls | 25 | 3.0 | 5.0 | 4.5 | 3.5 | 4.0 | 3.0 |
| 可维护性：scope 明确、可测试、依赖/状态复杂度 | 20 | 3.0 | 4.5 | 3.0 | 3.0 | 3.0 | 3.0 |
| Interview / JD keyword coverage（独立、非决定性） | 10 | 2.0 | 4.5 | 4.5 | 4.0 | 4.5 | 3.5 |
| 学习与迁移成本（低成本为高分） | 15 | 5.0 | 4.0 | 2.5 | 3.0 | 3.0 | 3.0 |
| **可复算总分 /100** | **100** | **76.0** | **94.0** | **66.0** | **64.5** | **71.0** | **55.0** |

招聘关键词只占 10%。即使把该列权重设为 0 并按其余 90 分归一，Pydantic AI 仍因实际业务适配、typed engineering 和维护性领先；LangGraph 也不会因为关键词覆盖就自动进入实施路线。该矩阵是决策辅助，不是对项目已实现能力的评分。

评分理由在于当前系统需要“受限的 typed agent adapter”，不是立即重建成多智能体或 RAG 平台。每个候选的官方资料和适配结论如下。

## 候选逐项判断

### 1. 现有 Custom Runtime — 保留为 legacy baseline

已有能力不应被抹去：AgentTool cache/guard、fallback、round/token limit 与 current trace 已存在（`backend/app/agents/base.py:71-79,458-701`），tools 又将模型可见 schema 与真实 identity 分离（`backend/app/agents/tools.py:2-47`）。它很适合作为 A/B control group。

不足：tool/output contracts 是手写 JSON/datataclass，Async cancellation/Session lifetime 有 P0 风险；model-call tracing 没有实际调用点；没有通用 eval dataset/report contract。结论不是“替换它”，而是**冻结可比 adapter seam，保留回滚能力**。

### 2. Pydantic AI — 正式推荐为第一个框架

Pydantic AI 的官方 DeepSeek 页面明确支持 `deepseek:deepseek-v4-flash`，也展示 `OpenAIChatModel` + `DeepSeekProvider` 配置方式；它与当前模型名直接吻合。[官方 DeepSeek 文档](https://pydantic.dev/docs/ai/models/deepseek/) 还提示一个本项目必须实测的 caveat：V4 默认 thinking，thinking 开启时不能强制 tool choice；若需要可靠 structured output，应关闭 thinking，并且 Responses API 中有功能会被静默忽略。

它最匹配当前链路的四个优势：

1. `deps_type` / `RunContext` 给 trip-scoped、安全可替换依赖一个显式 typed contract；官方依赖文档强调 dependencies 可以被 tools、instructions 和 output validators 使用。[官方 dependencies 文档](https://pydantic.dev/docs/ai/core-concepts/dependencies/)
2. `output_type` 可用 Pydantic model 生成 schema 并验证模型数据，output validator 可要求 retry；这适合把“解释、澄清、候选 change preview”变成可验证 DTO，而不是让模型触及写库。[官方 output 文档](https://pydantic.dev/docs/ai/core-concepts/output/)
3. 可设置 usage/request/tool-call limits、retry 和 async deadline，替换当前 ThreadPool timeout 设计，而不改变 domain decision authority。
4. Pydantic Evals 是 code-first 的 dataset/case/evaluator 框架，既能评最终结果也能评 tool trajectory，可承接 legacy/Pydantic A/B。[官方 Evals 文档](https://pydantic.dev/docs/ai/evals/evals/)

**推荐位置**：只接管 `Chat Change Request & Impact Preview` 的模型侧解析、只读工具、typed output 和 execution controls。`main.py`、constraint engine、decision orchestrator、database mutation、session/navigation ownership 不应被框架替代。

**必须先做的 provider compatibility PoC**：在专用 DB、显式成本上限、无生产 traffic 下，验证 DeepSeek V4 Chat Completions 的 tool calling、`thinking=False` structured output、usage accounting、timeout/cancellation、malformed tool output、provider error mapping。Pydantic AI 的兼容文档是集成理由，不是本项目 provider 成功的证明。

### 3. LangGraph — 有条件的第二框架，不进入 Phase 1

LangGraph 官方定位就是将确定性与 agentic steps 组合成有 persistence、human-in-the-loop、memory、durable execution 的低层 workflow runtime。[官方 overview](https://docs.langchain.com/oss/python/langgraph/overview/) 说明这些收益。它契合的不是现在的 chat preview，而是以下未来产品：

```text
Trip snapshot -> affected-day analysis -> candidate retrieval
-> draft regeneration -> deterministic validation -> bounded repair
-> checkpoint -> human review -> revalidate-before-commit
```

仅当满足全部条件才引入：

* 业务已经需要长时/可恢复的 whole-trip replan，而非即时 preview；
* 先定义 graph state 与 PostgreSQL plan/decision state 的单一 owner，避免双状态源；
* checkpoint 不含原始私密约束/成员身份，或拥有加密、访问、删除策略；
* 能说明失败补偿、resume、幂等和 revalidation-before-commit；
* 用原有 deterministic generator/validation 作不变量测试，证明确有价值大于复杂度。

目前没有这些证据。Pydantic Graph 也可在将来作为轻量 graph 比较对象；不应因为 LangGraph 更“知名”就提前引入。

### 4. LangChain — 生态适配但当前收益不足

官方将 LangChain 描述为由 model、tools、prompt、middleware 组成的可配置 agent harness，并明确把高级 deterministic+agentic workflow 交给 LangGraph。[官方 overview](https://docs.langchain.com/oss/python/langchain/overview/)

它能实现当前任务，但 Cadensy 已有受限 tool runtime；再引入一层 LangChain abstraction 既不比 Pydantic AI 更贴合现有 Pydantic/FastAPI 类型边界，也会带来新版本面/迁移面。保留为“团队未来已有 LangChain skill/connector 才重新评估”的备选，不作为第一迁移。

### 5. OpenAI Agents SDK — 功能成熟，provider 与 telemetry 边界不优先

官方 SDK 提供 agents、handoffs、guardrails、function-tool Pydantic validation、sessions 和 tracing。[官方文档](https://openai.github.io/openai-agents-python/) 是成熟候选。但 Cadensy 当前优先使用 DeepSeek OpenAI-compatible path，且需要避免错误地将 OpenAI-oriented sessions/tracing 视为适用于全部 provider 的已验证能力。要采用它需另做 provider compatibility、trace data destination/privacy、cost/lock-in 和 Chat Completions/Responses behavior PoC。

它适合作为将来产品明确迁到 OpenAI models、需要 native handoff/realtime、并能接受其 telemetry/服务边界时的重新选择；现在不优于 Pydantic AI。

### 6. LlamaIndex — 对 document retrieval 有条件价值，当前不是缺失框架

LlamaIndex 的概念/生态重点是 data connectors、indexes、retrievers 与 vector stores，[官方 concepts](https://developers.llamaindex.ai/python/framework/getting_started/concepts/) 也反映了这一定位。当前 agent 的核心数据是结构化、trip-scoped relational facts：PlanItem、constraint、decision、place cache；没有“私有旅行文件问答”的明确产品需求。

只有要检索 booking confirmations、航空/酒店条款、附件或旅行 policy 时，才设计 RAG：trip/membership metadata filter 必须在 retrieval 前执行，回答要有 citation，原文件/embedding 的 retention/deletion/PII policy 先完成。否则引入 vector store 会扩大数据面，不能替代现有 canonical place/decision 逻辑。

## 该保留、逐步替换和明确不做的内容

| 类别 | 决策 |
|---|---|
| 必须保留 | constraint engine、decision orchestrator、candidate ID/canonical places、trip scope、session-runtime、navigation policy、显式 API commit、legacy runtime。 |
| 可逐步替换 | Chat 的 model loop、tool schema adapter、typed model output/retry/usage instrumentation；先用 flag 在单个 route 切换。 |
| 暂不替换 | Planner generator 和写入路径；它们已有丰富 deterministic guard 与 fallback，需先从 eval 证明特定痛点。 |
| 明确不做 | 为简历关键词加入 RAG/MCP/CrewAI/AutoGen，或把现有三条 route 误包装成 multi-agent collaboration。 |

## 最终选择

**第一框架：Pydantic AI。** 选择依据是最贴近当前 Python/Pydantic/FastAPI/DeepSeek 的 typed adapter 需求，且能用 Pydantic Evals 建立证据链；不是因为它能取代业务规则。

**第二框架：LangGraph，只有触发条件满足时才进入。** 它解决的是 durable replan workflow，而不是当前 chat 的结构化 preview。若无法证明长时、checkpoint/resume、human review 的真实产品价值，则不引入。

## 研究限制

官方文档在审计日可访问，API 会演进。没有安装依赖、没有真实 DeepSeek 调用，因此版本精确行为、thinking/provider 参数和成本仍为 **Requires Verification**。后续实施必须 pin 版本、保存 docs/version evidence，并将 PoC 结果纳入 A/B report。
