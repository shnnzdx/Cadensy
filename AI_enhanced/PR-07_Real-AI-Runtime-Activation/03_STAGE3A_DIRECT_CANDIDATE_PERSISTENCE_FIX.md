# Stage 3A — Durable Direct Candidate Persistence Fix

## 开始条件

- 只修复 Stage 3 已定位的 Direct Candidate → Durable Option 缺口。
- 禁止真实 DeepSeek、AWS、Schema Migration、生产部署、Commit 和 Push。
- 必须保留 PR-05/PR-06B 的 Authorization、HMAC、Idempotency、Lease/Fence、Recovery、Historical Resolve 与 Protected Apply。

## 实施决策

修复放在 `backend/app/domain/conversations/runtime.py` 的 Coordinator Preview → Persistence 边界，而不是 Runtime、Preview 或 Apply 内部。理由是候选已经通过 `RevisionBracketedPreview` 的能力、目标和 Revision 检查，缺失的是统一持久化映射。

## 代码行为

- 将有效 `preview.candidate` 转换成 `complete_submission(options=...)` 已接受的 Durable DTO。
- 只使用经过 Preview 检查的 `item_id`、`item_title`、safe `patch`、Plan Revision 与 Planning Facts Revision。
- 标题使用服务端确定的安全文案，受 300 字符上限约束；不传播原始 Tool Result、私有分类理由或未经审查的模型字段。
- 在持久化边界对允许的领域值（包括 Python `date`）执行受控 JSON 兼容转换；未知/不可安全序列化字段 fail closed。
- 合并 Direct Candidate 与既有 `candidate_options`，按“目标 Plan Item + 规范化 safe patch”去重，保持确定顺序并遵守 `MAX_OPTIONS`。
- Candidate 仍是 advisory suggestion，不是已批准写命令。

## 回归覆盖

扩展既有 PR-06B 测试体系，覆盖：

1. Direct Candidate 保存为一条 Assistant Message 和一条 `ConversationMessageOption`。
2. Direct Candidate 与 candidate_options 重复时只保存一个 Option。
3. `day_date` 等合法日期值可 JSON 持久化。
4. History API 返回 Option；Historical Resolve 重新授权与 Revision 校验，但不 Apply。
5. 同一 Idempotency Key 重放不重复调用 Runtime、不新增 Message/Option。
6. Stale Revision、Membership 撤销、过期 Lease、旧 Fence、wrong Trip、wrong owner、personal scope 继续 fail closed。
7. 原有多候选路径与普通 Reply-only/Clarification 行为不变。

## 数据映射证据

```text
PreviewResult.candidate
  item_id             -> option.target_plan_item_id
  item_title          -> server-safe option title input
  validated patch     -> option.safe_patch
  captured plan rev   -> option.plan_revision
  captured facts rev  -> option.planning_facts_revision
```

Option 只保存建议快照；Plan Item 与两个 Revision 在 completion 阶段均不发生修改。

## 结果

- Fake Runtime targeted tests：PASS。
- PostgreSQL durable/API/history/historical-resolve tests：PASS。
- Idempotency、Lease/Fence、revocation、scope 与 stale revision 安全回归：PASS。
- 既有 candidate_options 与普通消息路径：PASS。
- Schema Migration：无。
- 未发现与 PR-05/PR-06B 业务契约冲突。
- 下一步只在新 Conversation / 新 synthetic fixture 上，以相同预算进行 Stage 3B 真实复验。
