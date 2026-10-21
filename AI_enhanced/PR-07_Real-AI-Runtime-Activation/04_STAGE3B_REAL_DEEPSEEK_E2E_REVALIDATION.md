# Stage 3B — Real DeepSeek End-to-End Revalidation

## 开始条件与硬限制

- 用户已单独明确授权有限真实 Provider 调用。
- 全新本地 disposable PostgreSQL `_test` 数据库，只使用 synthetic Trip/Plan/Plan Item/Membership。
- `APP_ENV=test`、Legacy Runtime、`MOCK_AI=0`、Fake Runtime off、Durable base gate on。
- `deepseek-flash`、Thinking disabled、SDK retries=0。
- 最多两条 User Messages、总计最多五次真实 HTTP 请求；Turn 1 最多三次、Turn 2 最多两次。
- completion 512 tokens、序列化输入最多 60,000 chars；Provider-reported total tokens 20,000 是后续请求停止阈值。

## 实际 Provider 使用

| 项目 | 结果 |
| --- | --- |
| 真实 DeepSeek HTTP 请求 | **5 / 5** |
| Provider-reported total tokens | **13,895** |
| 自动重试 | 0 |
| 模型 | `deepseek-flash` |
| Thinking | disabled |
| Runtime | Legacy |
| 预算超限 | 否 |

## Turn 1 — Direct Candidate

实际观察到：

- DeepSeek 正常返回 usage。
- Legacy Runtime 执行 `get_current_plan` 和 `classify_change`。
- `RevisionBracketedPreview` 形成有效 Direct Candidate。
- Submission 最终为 `completed`。
- PostgreSQL 恰好保存一条对应 User Message、Assistant Message 和 Direct Candidate Option。
- Option 包含正确 target、safe patch、Plan Revision 与 Planning Facts Revision。
- History API 返回该 Option。
- Historical Resolve 在当前身份和 Revision 下重新验证；未调用 Protected Apply。
- Plan Item 与两个 Revision 没有意外修改。
- 使用同一 Idempotency Key 重放没有增加 Provider 请求、Message 或 Option。

## Turn 2 — Server-owned Persistent Context

- 第一轮客户端 Session 关闭后，创建独立 Session 并重新认证 synthetic user。
- 恢复同一个服务端 Conversation。
- Runtime history 由 PostgreSQL `build_context()` 重建；新客户端没有提交浏览器历史。
- 记录到的 history 仅包含角色、turn count 与服务端来源证据，不在报告中保存实际消息内容。
- 模型对上一轮讨论给出连贯响应。
- 最终总计两条 User Messages、两条 Assistant Messages、两个 completed Submissions。
- Turn 1 Option 仍可读取，Trip、Membership、Conversation scope 保持正确。

## 安全证据

- Fence/Lease/Idempotency 继续控制唯一 durable effect。
- Historical Option 不能绕过当前授权与 Revision revalidation。
- Safe telemetry 只记录允许的分类、关联 ID、时延与 usage；不记录 Prompt、Secret 或原始 Tool Result。
- 没有未经授权的 Plan mutation。
- 故障注入继续使用 Stage 3A Fake/Stub 证据，不用真实调用制造 429/500/timeout。

## 结果

完整后端业务路径 **PASS**。当时刷新/重新登录仅证明 HTTP TestClient 新 Session 重新认证，不能等同真实浏览器验收；真实 Chromium 被明确留给 Stage 4A。

脱敏聚合证据位于本地 `backend/test-results-stage3b-real-provider-sanitized.json`，不建议进入 Release Candidate。未执行 AWS、生产数据库、部署、Commit 或 Push。
