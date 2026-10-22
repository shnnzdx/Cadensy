# Stage 1 — Read-Only Audit and Minimal Implementation Plan

## 开始条件

- 已知 GitHub `main` 基线：`f03d7c2c316414f6df3b48dedc7fe076f7062e75`，包含 PR #23 与 PR #24。
- PR-06B 已完成；本阶段只读，不改代码、不提交、不部署、不操作 AWS。
- 以本地实际工作区为实施依据，不假定本地等于远端 `main`。
- 数据库开发说明来自 `backend/LOCAL_DEV.md` 与本机开发环境文档；任何测试数据库必须是本机、隔离且显式以 `_test` 结尾。

## 审计范围

审计覆盖以下真实链路：

```text
Frontend Send
  -> HTTP Admission
  -> Authorization
  -> Idempotent Submission
  -> Commit
  -> Runtime Gate
  -> Context Reconstruction
  -> Selected Runtime
  -> DeepSeek / Read-only Tools
  -> Protected Preview
  -> Fenced Completion
  -> Durable History
  -> Frontend Rendering
```

重点核对了：

- `backend/app/api/main.py`
- `backend/app/domain/conversations/{service,context,runtime}.py`
- `backend/app/domain/chat/service.py`
- `backend/app/agents/{base,runtime_factory,legacy_runtime,pydantic_runtime,pydantic_composition,execution,tools}.py`
- `backend/app/domain/preview_apply.py`
- `backend/app/observability/telemetry.py`
- `trip/src/final/{durableConversationClient,useDurableConversation}.js`
- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`
- `trip/src/final/FinalApp.jsx`
- CD-01 生产发布工作流及历史 AI Runtime/Secret 工作流。

## 可审计的决策依据

1. **不删除 Production Guard。** 原有 `_durable_conversation_runtime_enabled()` 在生产环境直接禁止 Durable Runtime。安全激活应采用默认关闭的双许可 Gate，而不是移除保护。
2. **先选 Legacy。** Legacy 已经具备 DeepSeek、`get_current_plan`、`classify_change` 与现有 Preview 契约，改动面最小；Pydantic 路径工具面更窄，且不是完成真实 Chat 阻断项所必需。
3. **复用 PR-05/PR-06B。** 身份、授权、HMAC、幂等、Lease/Fence、Recovery、History、Protected Apply 已构成安全边界，不应重写。
4. **Provider readiness 必须真实。** 缺少 API Key、错误 endpoint、Mock/Fake 状态或未授权生产 Gate 时，readiness 必须 fail closed，不能只因 Runtime 名称可构造就显示可用。
5. **发布分两步。** 先发布 Runtime-off 代码，再在独立批准后激活 Provider；历史 Ollama-era 工作流不应直接执行。

## 主要发现

| 分类 | 发现 | 最小方向 |
| --- | --- | --- |
| Must Have | Production 环境没有受控启用路径，只有绝对禁止 | 引入独立 production activation latch，并保留基础 Gate |
| Must Have | Provider readiness 可能与真实凭据/endpoint/Mock 状态不一致 | 将 readiness 与实际选择和必要配置绑定 |
| Must Have | 前端存在发送未返回期间的快速重复提交风险 | 同步发送锁 + 持久化 Idempotency Key |
| Must Have | Pending/Recovery、Failed 状态需要稳定的用户反馈与原请求身份 | 复用 durable submission 状态与 recovery key |
| Must Have | Provider 请求缺少用于本轮验证的严格费用边界 | SDK retries=0、completion 512、输入字符预算、总请求硬上限 |
| Should Have | `deepseek-v4-flash` 与 `deepseek-flash` 名称兼容可能影响 telemetry/thinking 判定 | 规范化 provider/model 元数据，不改变生产默认值 |
| Should Have | CD-01 的 runtime-off 契约需要覆盖新 production activation latch | 增加离线 Contract Test 与 workflow 检查 |
| Future | Pydantic 工具面扩展、RAG、MCP、Multi-Agent、Redis Queue | 明确不在本轮阻断路径中 |

## Stage 1 结果

- 审计结论：现有 PR-05/PR-06B 基础可复用，真实 AI Chat 的主要阻断在 Runtime 可控激活、Provider readiness/预算和前端可靠性，而非数据模型重写。
- 推荐实施：最小修改现有 Runtime/配置/前端 durable client，并通过 Fake、隔离 PostgreSQL 和真实 Provider 的逐级证据关闭风险。
- 未执行：代码修改、Schema Migration、真实 Provider、AWS、部署、Commit、Push。
