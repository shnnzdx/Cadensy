# Stage 2 — Legacy Runtime Activation Preparation

## 开始条件

- 目标是为真实 DeepSeek 验证准备现有 Legacy Runtime，不重做 PR-05/PR-06B，不切换 Pydantic。
- 生产默认必须继续关闭；只允许本地测试配置启用。
- 所有修改保留在未提交工作区，禁止 AWS、部署、Commit 和 Push。

## 关键实施

### Runtime 与 Provider

- 显式支持 `CHAT_AGENT_RUNTIME=legacy`。
- 保持 `MOCK_AI=0`、`CADENSY_E2E_FAKE_RUNTIME=0` 才能进入真实 Provider 路径。
- DeepSeek 使用官方 HTTPS endpoint，验证模型使用 `deepseek-flash`，Thinking 显式关闭。
- OpenAI SDK 自动重试设为 `max_retries=0`，避免预算外隐式重试。
- 保留请求、Provider、Tool Deadline；Durable completion 上限为 512 tokens。
- 对序列化输入建立 60,000 字符预算；Provider-reported token 总量只作为后续请求停止阈值，不宣称能限制已发出请求的实际输入 token。

### Production Gate 与 Readiness

- 保留基础 `CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED` Gate。
- 生产环境另需独立 activation latch，形成双许可，默认关闭。
- Fake Runtime 在 production/prod 中继续禁止。
- Readiness 与 Runtime 选择、Mock/Fake 状态、endpoint、model、API Key 是否就绪绑定，缺项时 fail closed。

### Durable 前端可靠性

- 在同步发送边界增加锁，缩小快速重复点击竞态窗口。
- 让 Idempotency Key 与原始提交身份一起保留，未知网络结果可安全查询/恢复。
- Pending、Recovery、Failed 状态明确呈现。
- Runtime 不可用时拒绝新发送，保留已持久化历史，不回退到 Legacy `/chat` 伪造回复。

### 发布契约

- CD-01 Runtime-off helper 强制注入 activation latch 为 `0`。
- 增加/调整离线测试，确保 Release A 不能意外开启 Durable Runtime、Fake Runtime 或开发身份旁路。

## 主要修改区域

- Backend Runtime/Provider：`backend/app/agents/base.py`、`pydantic_runtime.py`、`runtime_factory.py`
- API/readiness/chat：`backend/app/api/main.py`、`backend/app/domain/chat/service.py`
- Durable execution/preview/telemetry：`backend/app/domain/conversations/{runtime,service}.py`、`backend/app/domain/preview_apply.py`、`backend/app/observability/telemetry.py`
- Frontend durable flow：`trip/src/final/durableConversationClient.js`、`useDurableConversation.js`、`plan-feature/useAssistantChangeRequestFlow.js`、`FinalApp.jsx`
- CD-01 helper/tests：`scripts/cd01/prepare_ecs_task_definition.py` 及相关测试。

## 结果

- Stage 2 本地准备与既有测试通过，具备受限真实 Provider Preflight 条件。
- 未新增 Schema Migration。
- 未读取或提交 Provider Secret。
- 未调用真实 Provider、AWS 或生产数据库。
- Stage 3 被限定为最多两条用户消息、最多五次真实 HTTP 请求，且必须在发出网络请求前强制计数。
