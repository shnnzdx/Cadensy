# Stage 4A — Browser Acceptance and Release Candidate Preparation

## 开始条件

- Stage 3B 已用五次真实请求通过；本阶段禁止任何新的付费 Provider 调用。
- 目标是完成真实 Chromium UI 验收，最小加固本地 E2E harness，并整理 Stage 2/3A 为可审查 Release Candidate。
- 禁止 AWS、生产数据库、生产部署、Schema Migration、Commit、Push 和 Merge。

## Safe Browser Harness 加固

修改范围：

- `backend/scripts/run_pr06b4_local_e2e.ps1`
- `backend/scripts/pr06b4_e2e_fixture.py`
- `frontend/tests/pr06b4-local-browser-e2e.mjs`
- `backend/app/agents/e2e_runtime.py`
- 针对性 harness regression test。

已实现：

- 每次生成唯一、随机、本机且以 `_test` 结尾的 disposable PostgreSQL 数据库。
- 若目标数据库已存在，runner 立即拒绝；不自动 DROP、覆盖或复用。
- 只清理本次创建的数据库和临时目录，并在结束后恢复进程环境。
- Fixture stdout 不再输出 synthetic 登录凭据；计划项选择改为确定顺序。
- 增加测试专用 `[E2E-DIRECT-CANDIDATE]` 路径，实际生成 `PreviewResult.candidate`，不复用原 `[E2E-OPTION]` 的 `candidate_options` 路径。
- Fake E2E Runtime 的 readiness 例外只在 `APP_ENV=test`、`MOCK_AI=1`、Fake flag 和基础 Durable Gate 同时满足时成立；production/prod 禁令不变。
- 邀请 token 等浏览器证据输出经过脱敏。

## Chromium Test Matrix

| Journey | 结果 | 证据 |
| --- | --- | --- |
| J01 | PASS | 真实 Login UI、Durable send、完成、刷新恢复 |
| J02 | PASS | Sign out 后重新登录恢复同一 Conversation |
| J03 | PASS | 新 Chromium Context 重新认证并恢复授权历史 |
| J04 | PASS | Membership 移除/重新加入隔离，旧私有历史不可见 |
| J05 | PASS | 撤销期间迟到 completion 被 Fence 阻断 |
| J06 | PASS | 浏览器重复提交只产生一个 Submission/User Message |
| J07 | PASS | personal/global/item scope 隔离 |
| J08-CURRENT | PASS | Direct Candidate Option 在 UI 展示并 Historical Resolve 为当前有效 |
| J08-STALE | PASS | Revision 改变后旧 Option 被拒绝为 stale |
| J09 | PASS | 删除后的 Conversation 不可恢复访问 |
| J10 | PASS | 旧 `/chat` 兼容路径不写 durable Conversation |
| Runtime-off | PASS | 历史可读、composer disabled、无 Legacy fallback |

## Direct Candidate UI 证据

真实浏览器路径经过：

```text
LocalE2ERuntime Direct AgentSuggestedChange
  -> RevisionBracketedPreview
  -> ConversationRuntimeCoordinator
  -> complete_submission
  -> PostgreSQL ConversationMessageOption
  -> History API
  -> Plan Assistant UI
  -> Historical Resolve
```

观测到的 safe patch 为单项时间调整（`start_hour: 15`），Option 捕获 Plan/Facts Revision `2/2`。刷新、Sign out/re-login 后仍显示。点击时调用 Historical Resolve，不自动 Apply；在测试通过现有受保护业务路径推进 revision 后，旧 Option 正确变为 stale。该验证没有让 LLM 或 Option 直接修改 Plan。

## 回归结果

| 验证集 | 结果 |
| --- | --- |
| Chromium Browser Acceptance | PASS，J01–J10 + Runtime-off |
| Backend durable/API/PostgreSQL/CD-01/harness | **79 passed** |
| Agent/telemetry/runtime/Pydantic regressions | **72 passed** |
| Frontend durable/Plan characterization | **21 passed** |
| 早期 targeted regression | **19 passed** |
| `npm run build:trip-preview` | PASS；最新 Trip source 已重新构建到嵌入资源 |
| `git diff --check` | PASS |

## Disposable 数据与凭据安全

- 本轮随机浏览器数据库清理后数量：0。
- 回归数据库清理后数量：0。
- 临时目录清理后数量：0。
- 本地测试服务端口 5432/8000/4499 在结束时无遗留监听；本地 PostgreSQL 已停止。
- 两个此前已存在的固定名测试库没有被 runner 删除或覆盖。
- 已验证“目标已存在”路径：执行前存在 1 个，runner 拒绝，执行后仍为 1 个。
- 未读取真实 Provider API Key，未执行 Provider 请求。

## Release Candidate 审查结果

- Git HEAD：`00256e1802a4c617c3240c04ebd15b6936a5add8`。
- `origin/main`：`f03d7c2c316414f6df3b48dedc7fe076f7062e75`。
- HEAD 是 `origin/main` 的祖先；当前分支 upstream 已不存在。
- 相对 `origin/main`：32 个 tracked changes；另有 2 个应纳入 RC 的 untracked 文件，共 34 个候选路径。
- 应纳入的 untracked 文件：
  - `backend/tests/test_pr06b4_local_e2e_harness.py`
  - `frontend/public/trip-app/assets/index-6cidjQCS.js`
- 旧 bundle `frontend/public/trip-app/assets/index-CUIU91Ld.js` 应随 manifest/index 的新引用一并删除，保证嵌入资源原子更新。
- 未发现 Provider key、AWS key、私钥或 JWT 泄漏；runner 中动态本地连接串构造属于扫描 false positive。
- 未发现 Alembic migration、生产 workflow 修改或无关 RDS cleanup diff。

## 不应进入 Git 的本地证据

所有 `backend/test-results-*` 日志、JSON、exit 文件和 control 目录均应排除。历史 fixture JSON 含密码字段，历史完整 stdout log 也含 credentialed PostgreSQL URL 形态；即使属于本机测试数据，也不能加入 PR。应使用显式 allowlist 暂存，禁止 `git add -A`。

## Stage 4A 结果

本地浏览器与 Release Candidate 技术验证 **PASS**。未发现新的业务契约冲突；PR-05 Protected Apply、PR-06B authorization/idempotency/lease/fence 均保持。可以开始建立 review/draft RC PR，但生产发布仍受下一份总结中的 CD-01 latch contract 与证据卫生阻断。
