# PR-07 — Real AI Runtime Activation Evidence

**记录日期：** 2026-10-21
**工作区：** `C:\Users\zdxzh\Desktop\capstone\New`
**状态：** 本地 Release Candidate 准备中；未 Commit、未 Push、未部署
**安全边界：** 本目录不保存 API Key、账号密码、数据库连接串、私有 Prompt、原始 Tool Result 或未经脱敏的运行日志。

## 记录说明

本目录保存 PR-06B 完成之后，Cadensy 从“持久化会话基础设施”走向“真实 AI Provider 可控激活”的连续工程记录。文档中的“决策依据”是可复核的工程推理摘要，包括观察、约束、选择和结果；不包含模型私有思维链。

后续阶段继续沿用同一记录格式：开始状态与授权边界、观察证据、关键选择及其理由、文件/行为变化、执行过的验证、最终结果、未决风险和下一步。涉及真实 Provider 或本地凭据时只记录脱敏聚合结论，不复制敏感输入或原始日志。

| 阶段 | 文档 | 终态 |
| --- | --- | --- |
| Stage 1 | [只读审计与最小实施计划](00_STAGE1_READONLY_AUDIT_AND_PLAN.md) | 完成；识别生产 Gate、Provider readiness、前端可靠性与发布契约缺口 |
| Stage 2 | [Legacy Runtime 激活准备](01_STAGE2_LEGACY_RUNTIME_ACTIVATION_PREPARATION.md) | 完成；默认关闭、Legacy 显式选择、请求预算与前端恢复能力就绪 |
| Stage 3 | [首次真实 DeepSeek 验证](02_STAGE3_REAL_DEEPSEEK_INTEGRATION_VALIDATION.md) | FAIL；Provider/Tools 成功，Direct Candidate 未持久化为 Option |
| Stage 3A | [Direct Candidate 持久化修复](03_STAGE3A_DIRECT_CANDIDATE_PERSISTENCE_FIX.md) | PASS；Fake/PostgreSQL/API/安全回归通过 |
| Stage 3B | [真实 DeepSeek 两轮复验](04_STAGE3B_REAL_DEEPSEEK_E2E_REVALIDATION.md) | PASS；5 次请求，完整后端链路通过 |
| Stage 4A | [Chromium 验收与 RC 准备](05_STAGE4A_BROWSER_ACCEPTANCE_AND_RC_PREPARATION.md) | PASS（本地）；真实 Chromium、Direct Candidate UI、刷新/重新登录通过 |
| RC 总结 | [Release Candidate 与下一步](06_RELEASE_CANDIDATE_SUMMARY_AND_NEXT_STEPS.md) | 可建立 Draft/Review PR；生产发布前仍有两个明确阻断项 |
| Stage 4B | [RC 组装与 CD-01 发布安全](07_STAGE4B_RC_ASSEMBLY_AND_CD01_RELEASE_SAFETY.md) | PASS；dual-gate、code-only、双轴 review 与 RC allowlist 已完成，等待 Commit/PR 授权 |

## 总体结论

已在隔离本地环境证明：

```text
Send
  -> Legacy Runtime
  -> DeepSeek
  -> scoped read-only tools
  -> RevisionBracketedPreview
  -> Direct Candidate Durable Option
  -> fenced completion
  -> PostgreSQL history
  -> refresh / re-login
  -> server-owned context reconstruction
  -> continued conversation
```

这不是生产激活批准。当前建议是先建立代码审查用 Release Candidate PR；Release A 保持 Runtime-off，Release B 需要单独授权和受控激活流程。
