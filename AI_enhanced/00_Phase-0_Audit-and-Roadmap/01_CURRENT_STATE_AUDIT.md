# Cadensy Phase 0 — Current-State Audit

审计日期：2026-10-03
范围：只读代码、Git、文档、官方框架资料和非破坏性测试。本文不代表任何后续设计已经实现。

## 结论先行

Cadensy 已不是“单纯调用 LLM”的 demo：它把模型限制在候选地点选择、受限只读工具和解释/建议层，关键的行程变更路径仍由确定性约束与人工确认控制。最值得保留的设计是 **AI proposes, rules protect, humans decide**。

当前短板也很具体：生产身份回退开关不安全、聊天超时的线程/Session 生命周期不可靠、数据库迁移与并发版本控制缺失、模型观测没有完整接线，且部分历史部署/测试文档与当前仓库事实不一致。它们应先于新框架迁移被处理。

## 审计边界与验证状态

| 项目 | 状态 | 依据 |
|---|---|---|
| 本地分支/工作区 | 已验证 | `main`，开始与本次文档写入前均无既有未提交修改；HEAD 为 `6a55854d7899c77fa9fa6bab864b194abf4dd16c`。 |
| 与 GitHub 对比 | 已验证 | `origin/main...HEAD = 0/0`；GitHub 同一提交可见于 [commit 6a55854](https://github.com/shnnzdx/Cadensy/commit/6a55854d7899c77fa9fa6bab864b194abf4dd16c)。 |
| 后端完整 pytest | 未运行 | `backend/tests/conftest.py:38-118` 会把运行时 DB 指向 `TEST_DATABASE_URL`，终止其连接并重建该库；Phase 0 未配置已授权的独立库。 |
| 真实 DeepSeek 评测 | 未运行 | 两个脚本都要求 `MOCK_AI=0`；会产生付费外部调用，未经授权。 |
| AWS/ECS/RDS 实际状态 | 未验证 | 未调用 AWS、未读凭据、未触发工作流。下文所有云端状态均是仓库文档或工作流静态证据。 |

本轮新增的仅是本目录中的 Markdown 文档；不含包安装、配置、业务代码、数据库、云端或 Git 历史改动。

## 当前真实架构

### 技术栈

| 层 | 当前证据 |
|---|---|
| 产品与工作区 | `frontend/` 使用 React 19 / Vinext / Next；`trip/` 使用 React 18、Vite、React Router 和 Leaflet（`frontend/package.json:1-47`、`trip/package.json:1-22`）。两个前端应用的分离是既有架构决定。 |
| API/持久化 | FastAPI、同步 SQLAlchemy 2、PostgreSQL/psycopg（`backend/requirements.txt:1-8`；`backend/app/db/session.py:37-43`）。 |
| 模型调用 | OpenAI Python SDK 的 OpenAI-compatible 客户端；当前运行说明将 Chat/Planner/Explainer 都路由至 DeepSeek，默认模型为 `deepseek-v4-flash`（`backend/app/agents/base.py:323,434`；`backend/app/agents/AI_AGENT_SUMMARY.md:13-27`）。这不是 Pydantic AI。 |
| 部署工件 | 后端与前端 Dockerfile、GitHub Actions build validation；AWS 文档描述 ECS Fargate/ECR/ALB/private RDS，但当前实际云端状态未在本轮调用中验证。 |

### 关键职责边界

```text
Trip UI
  -> FastAPI chat / classify / submit endpoints
      -> Chat service: 意图、指代、只读工具和降级文案
          -> Custom runtime: 模型循环、schema、guard、cache、round/token 上限
              -> Read-only trip tools
      -> Constraint + decision domain: NOTICE / ROUND / REOPEN_ROUND / CONFIRM
          -> 明确的 API mutation + transaction commit
              -> PostgreSQL plan / change / vote / proposal records
```

* `backend/app/api/main.py:832-859` 明确声明 `/chat` 不调用 `propose_change()`；聊天不能直接写计划。
* `backend/app/agents/tools.py:38-47` 把 trip 与 membership identity 捕获在 Python closure 内，模型 schema 看不到 ID；工具定义为只读。
* `backend/app/domain/constraints/engine.py:109-205` 是纯确定性约束违反和分类逻辑；`backend/app/domain/decisions/orchestrator.py:437-512` 把分类与真正 proposal/change 分开。
* `backend/app/api/main.py:1259-1485` 由 API 成功路径显式 `db.commit()`，而不是由模型或工具提交事务。
* 共享会话与导航边界没有被此次规划改动：`shared/session-runtime/index.js:285-335` 统一组装身份头；导航策略仍在 `shared/trip-navigation-policy/`。

### Agent Runtime 与 Chat

自研 runtime 位于 `backend/app/agents/base.py`：`call_agent()` 从 :489 开始，具备 provider fallback、每轮 tool schema/arguments、tool guard、缓存、guard rejection limit、轮数和 token 限制（:458-620、:668-701）。工具 trace 使用 `trace.record_agent_round()`（:547、:643）。

Chat 路由的工作方式是先做确定性解析/快速路径，然后可能进入模型工具循环；配置为 30 秒外层超时、8 轮、120,000 总 token（`backend/app/domain/chat/service.py:19-21,72-110,927-958`）。当前前端将最近 10 条 messages 作为 history 发送（`trip/src/final/TripAppState.jsx:1032-1038`），抽屉消息在 React state 中并会在 item/mode 切换时清空（`trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:73,96-100`）。

因此当前 memory 的准确表述是：

* 前端 in-memory 会话状态；
* 每次请求最多 10 条的、客户端提交的短期上下文；
* **不是** server-persistent conversation，也不是 long-term semantic memory（未发现 Chat/Conversation 持久化模型）。

### Planner 与地点数据

Planner 不是多智能体系统。它是“每天一次受限模型选择”嵌在确定性生成器中的混合管道：

```text
preferences + required constraints + provider/cache places
 -> legal candidates
 -> model selects candidate_id + start_hour
 -> parser revalidates
 -> meals / full-plan validation
 -> rules fallback when unusable
 -> PlanItem persistence
```

模型只能输出不透明 `candidate_id` 和 `start_hour`，未知 ID、非法时间、营业时间、类别窗口、重复候选等均会被拒绝（`backend/app/agents/planner.py:12-19,147-152,285-341`）。生成器仍控制候选、规则 fallback 与写入（`backend/app/domain/plans/generator.py:160-162,497-709`）；地点缓存/Geoapify 结果转换成 canonical record（`backend/app/domain/places/service.py:75-130,325-339`）。这是必须保留的抗幻觉边界。

## 已实现能力与限制

| 能力 | 当前状态 | 证据 / 限制 |
|---|---|---|
| Trip scope authorization | 已实现 | `TripScope` 对 path trip 返回跨 trip 403，对非嵌套资源采用 scoped-not-found（`backend/app/domain/access/trip_scope.py:24-119`）。但见 P0 的 dev header 回退。 |
| 私人约束保护 | 已实现且需回归守护 | 工具模块注释和 schema 明确排除 member ID、名字、原始偏好；安全文本也替换私密措辞（`backend/app/agents/tools.py:2-5,452-516,608-612`）。这不等于所有未来字段都天然安全。 |
| Agent read-only / tool cache | 已实现 | `AgentTool` 有 guard/cache，执行路径只调用 handlers（`backend/app/agents/base.py:71-79,593-620`）；本轮静态搜索未发现当前 tools/base/chat 中的 `db.add/delete/commit/flush`。 |
| 结构化 planner 解析和 repair | 已实现 | 固定 schema + post-parse validation + deterministic fallback。 |
| Change preview 再分类 | 部分实现 | chat preview 不写；提交 change 时 domain 会再进行分类。缺的是防止 stale preview 的显式 revision/idempotency contract。 |
| Auth 基础 | 已实现 | 密码 PBKDF2-SHA256/210k rounds、服务端 token hash、14 天 session（`backend/app/domain/auth.py:22-24,62-92,113-183`）。登录限流、部署开关和审计仍需加强。 |
| 数据库并发保护 | 部分实现 | open round、pending proposal、每成员一票已有 unique/partial indexes（`backend/app/db/models.py:335-410`）；未看到 plan/version 或通用 request idempotency。 |
| Schema 演进 | 部分实现 | 运行 `Base.metadata.create_all()` 加 additive ALTER（`backend/app/db/init_schema.py:16-45`），未发现 Alembic migration 历史。 |
| 可观测性 | 部分实现 | agent round trace 写入本地 JSONL；`record_model_call()` 被定义但代码搜索没有调用点（`backend/app/agents/trace.py:56-176`）。没有已验证的 request correlation/cost dashboard。 |

## 测试与评测证据

已审查的后端测试覆盖 agent base/tools/call agent、chat、planner、decision、scope、auth 与 schema。测试数量不是质量结论，且完整后端执行未在本轮进行。

唯一执行的是不启动应用、数据库、网络或构建的 Node 会话/导航契约测试。结果为 **116 tests：111 pass、5 fail**。失败均来自读取源码并匹配旧 helper 文本的 cutover/characterization 测试：

* `frontend/tests/session-runtime-request-identity-cutover.test.mjs:19-28`
* `frontend/tests/trip-navigation-characterization.test.mjs:25-87`
* `frontend/tests/trip-navigation-login-fact-availability.test.mjs:106-116`
* `frontend/tests/trip-navigation-restoration-cutover.test.mjs:114-136`

例如仍期待 `accountRequestJson(...)` 的旧写法，而当前实现已抽到 `sessionRequestJson(...)`。这证明这些 5 项当前不绿；不能把它们解释为功能已经回归，也不能借此断言功能错误。应在后续 PR 把断言迁移到稳定的公开行为/边界。

现有评测工具也有价值但本轮未运行：

* `backend/app/agents/agent-server/run_planner_eval.py:1627-1683` 对 Chicago rich-data 与 Tokyo sparse-data 两个 fixture scenario 各执行两次，捕捉合法性、fallback、完整性、差异性、地理一致性等指标。每个 run 的数据 transaction 会 rollback，但 script 在此前 `Base.metadata.create_all(engine)`，并且需要真实 provider；只能在专用 DB 和明确付费授权后运行。
* `backend/app/agents/agent-server/run_real_trip_tools_trace.py:56-116` 用 transaction seed 数据并 rollback，但同样先 `create_all` 且要求真实 DeepSeek。历史 runbook 的“已跑过”描述是历史记录，不是本轮验证（`docs/backend/dixin/PLANNER_V1_PHASE1_HARNESS_RUNBOOK_2026-08-15.md:1-55`）。

## Cloud / DevOps 审计

构建验证工作流有临时 Postgres service、`MOCK_AI=1` backend tests 和 container health check（`.github/workflows/build-validation.yml:1-100`），这是一项积极基线。Docker 资源也采用非 root 运行用户（见各 Dockerfile）。

不过云端资料不能直接当成现状：

* AWS 文档描述 `app.cadensy.top` 的 ALB/ECS/RDS 架构（`AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md:1-110`），但其他文档仍指向 `shnnzdx/cap_stone`；本仓库 origin 是 `shnnzdx/Cadensy`。
* `backend-ai-runtime-config.yml` 确实包含 main-only guard 和 ECS stable wait（:81-83、:330-345），但也把 `DEV_ALLOW_MEMBERSHIP_HEADER` 硬设为 `1`（:172-191,263-264）。
* AWS README 将该状态称为当前 guest invite 的必要条件（`AWS/README.md:40-42,177-179`）。这既是安全 gate，也表明 guest identity 设计尚未完成；没有 AWS read-only verification 时不能称它为“已安全部署”。

## 本地 / GitHub 差异

本地 origin 为 `https://github.com/shnnzdx/Cadensy.git`。在开始写入本目录前，`git status --short --branch` 只有 `## main...origin/main`，`git diff --check` 无输出，`git rev-list --left-right --count origin/main...HEAD` 为 `0 0`。GitHub commit 页面同样显示 `6a55854` 为 “Document Cadensy replacement suggestions”。

本次文档会使该新审计目录成为未提交变更；不会 commit/push。

## 外部资料核验

本审计的框架判断只使用官方资料：Pydantic AI 的 [DeepSeek provider](https://pydantic.dev/docs/ai/models/deepseek/)、[dependencies](https://pydantic.dev/docs/ai/core-concepts/dependencies/)、[structured output](https://pydantic.dev/docs/ai/core-concepts/output/) 与 [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/)；以及 [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)、[LangChain](https://docs.langchain.com/oss/python/langchain/overview)、[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) 和 [LlamaIndex concepts](https://developers.llamaindex.ai/python/framework/getting_started/concepts/)。框架结论与整合边界见后续文件。
