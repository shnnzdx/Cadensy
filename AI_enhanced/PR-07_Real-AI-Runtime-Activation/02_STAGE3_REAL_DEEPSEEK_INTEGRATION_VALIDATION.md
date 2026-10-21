# Stage 3 — Initial Real DeepSeek Integration Validation

## 开始条件与授权边界

- 仅本地 disposable PostgreSQL `_test` 数据库与 synthetic fixture。
- Legacy Runtime、`deepseek-flash`、Thinking disabled、SDK `max_retries=0`。
- 最多两条用户消息、最多五次真实 DeepSeek 请求；异常或协议失败立即停止。
- 每次 completion 上限 512 tokens，输入上下文受 60,000 字符预算约束。
- 禁止 AWS、生产数据库、生产 Secret、部署、Commit 和 Push。

## 实际执行

首轮真实链路进入：

```text
User Message
  -> LegacyRuntime
  -> DeepSeek
  -> get_current_plan
  -> classify_change
  -> RevisionBracketedPreview
  -> ConversationRuntimeCoordinator
  -> complete_submission
```

真实 Provider 共发出 **3 次请求**，均成功返回。`get_current_plan` 与 `classify_change` 均实际执行，说明 Provider、Legacy tool loop 和 Preview 前半段兼容。

## 失败结果

最终验收为 **FAIL**，并按预算规则停止，没有为了获得理想输出继续重试。

已定位根因：

```text
LegacyRuntime._suggested_change()
  -> RevisionBracketedPreview._classify_candidate()
  -> PreviewResult.candidate
  -> ConversationRuntimeCoordinator.execute()
  -> complete_submission(options=...)
```

Coordinator 当时只转换 `preview.candidate_options`，遗漏单项调整常走的 `preview.candidate`。因此：

- Assistant Message 可以持久化；
- 经过 Preview 验证的 Direct `AgentSuggestedChange` 没有变成 `ConversationMessageOption`；
- 刷新后无法恢复这个建议，也无法通过 Historical Resolve 重新验证。

## 安全判断

- 这不是 Provider、Tool Calling、Authorization、Fence 或 Protected Apply 失败。
- 没有证据表明 Plan 被 LLM 直接修改。
- 修复应位于 Preview → Persistence 转换边界，不应改变 PR-05 Apply、Schema 或 Runtime 框架。
- 脱敏结果保存在本地 `backend/test-results-stage3-real-provider-sanitized.json`；该文件属于本地证据，不建议加入 Release Candidate。

## 结果与下一步

- Provider/Legacy/tools：PASS。
- Direct Candidate durable persistence：FAIL。
- Turn 2：未继续，符合“Turn 1 任一验收失败立即停止”。
- 下一步：Stage 3A 使用 Fake/Stub 和隔离 PostgreSQL修复并回归，不重新调用真实 Provider。
