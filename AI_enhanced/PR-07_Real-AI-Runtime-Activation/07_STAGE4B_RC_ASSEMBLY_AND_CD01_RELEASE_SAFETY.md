# Stage 4B — Release Candidate Assembly and CD-01 Release Safety

## 开始状态与权限边界

- 执行日期：2026-10-21。
- 旧本地 HEAD：`00256e1802a4c617c3240c04ebd15b6936a5add8`。
- 非破坏性 `git fetch origin main --prune` 后，`origin/main` 仍为
  `f03d7c2c316414f6df3b48dedc7fe076f7062e75`。
- 旧 HEAD 是 `origin/main` 的直接祖先，且 merge commit 的树没有覆盖本地改动；因此安全创建并切换到
  `codex/cadensy-legacy-durable-runtime-rc`，保留全部 Stage 2/3A/4A worktree。
- 本轮未 Commit、Push、创建 PR、运行 GitHub Actions、访问 AWS/生产数据库或调用真实 Provider。

## 可审计的决策记录

1. **Runtime-off 校验必须可离线执行。** 不继续复制难以单元测试的 `jq` 片段，而是在现有
   `prepare_ecs_task_definition.py` 边界增加 release/rollback 两种 validator；Workflow 在发布前和发布后调用同一契约。
2. **旧 rollback 只兼容一个缺失字段。** 历史 Task Definition 可以缺少后加入的 production activation latch，因为旧代码缺省为 off；基础 Runtime Gate、Mock/Fake、Provider credential、HMAC/DB secret reference 和独立 cold-start/schema evidence 不得放宽。
3. **Code-only 不能伪造 Gate M。** 新 Source SHA 继续绑定 runtime 和 schema-verifier digest；历史 migration 使用独立的精确 GitHub Actions run URL，验证该 Gate M run/job 的成功状态但不把它重新绑定到新 SHA。真正的 schema 放行证据仍包括针对当前生产数据库的只读 head + catalog verifier。
4. **保留 migration-bound 路径。** 原 Gate M workflow 和同 SHA durable evidence 校验不删除，仅在明确选择 `migration_bound` 时执行。
5. **不实际 stage。** 本轮以显式 RC allowlist 作为 staging inventory，避免改变用户 index；本地证据通过 `.gitignore` 的窄规则排除，禁止 `git add -A`。

## TDD 过程

测试 seam 在实施前冻结为：

- Task Definition helper 的公开生成/验证接口；
- CD-01 Workflow 的静态 YAML/脚本发布契约；
- 真实 PostgreSQL durable/migration contract；
- Durable frontend client 的公开行为。

RED 首次执行得到 12 个预期失败：缺少 runtime-off validator、release mode、独立 schema evidence 和 workflow 双 Gate 验证。随后按垂直切片完成 helper validator 与 workflow code-only 分支；CD-01 targeted suite 转为全绿。

最终 Spec 复审随后复现了三项 fail-closed 缺口：environment/secrets 跨通道同名覆盖、`DEEPSEEK_TOKEN` 别名漏检，以及 code-only 仅有 workflow 文本断言。先补回归测试得到 RED，再完成修复。新增 strip 测试还发现 Provider environment 项从 map 删除后仍残留在顺序列表中的 `KeyError`；过滤已删除名称后转绿。最终 CD-01 两文件合约测试为 **46 passed**。

## 代码与发布契约变化

### Dual-gate Runtime-off

`scripts/cd01/prepare_ecs_task_definition.py` 现在：

- 新 Release A 显式要求：
  - `CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=0`
  - `CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED=0`
  - `CADENSY_E2E_FAKE_RUNTIME=0`
  - `MOCK_AI=0`
- 拒绝 environment/secrets 中的模型 Provider credential，以及未分类的 `*_API_KEY`。
- 拒绝已知 Provider 的 `*_TOKEN` 等凭据别名。
- 拒绝 environment 内、secrets 内以及 environment ↔ secrets 的重复名称，避免 ECS secret 覆盖显式 `0`。
- `release` profile 要求全部默认关闭 Gate 显式为 `0`。
- `rollback` profile 仅允许 production latch 缺失；如果存在必须唯一且为 `0`。
- CLI 支持 `--validate-runtime-off-profile release|rollback`，供 Workflow 复用。

`.github/workflows/cd01-production-release.yml` 现在：

- rollback baseline 使用 `rollback` validator；
- 新注册 Task Definition 在部署后使用 `release` validator；
- Release Summary 明确记录 base gate、production latch、Fake、Mock 和 Provider-off 状态。

### Code-only Release

新增输入：

- `release_mode`: `code_only`（默认）或 `migration_bound`；
- `schema_migration_evidence_ref`: 独立历史 migration evidence reference。

`scripts/cd01/validate_release_evidence.py` 将该输入限定为本仓库精确的 Gate M
GitHub Actions run URL，并可执行校验 code-only 的 Gate M 字段必须全空、
migration-bound 的 run/SHA/task 必须完整且 run URL 与 run ID 一致。

`code_only`：

- 要求 Gate M run/SHA/task 输入为空，避免把历史运行误标成当前 SHA；
- 通过 GitHub API 验证独立 URL 指向 `main` 上成功的 Gate M workflow 和成功的 bounded migration/catalog-verification job；
- 仍验证当前 Source SHA 是 `main` 祖先；
- runtime digest 和 schema-verifier digest 必须带当前 Source SHA 的不可变 ECR tag；
- 在 ECS 私有网络中运行 `verify_pr06b_schema.py`，只读验证
  `b7d3a1e9c642`、五张 conversation 表和 active-account index；
- 成功状态记录为 `at-approved-head-no-schema-mutation`。

`migration_bound` 保留原同 Source SHA 的 Gate M GitHub、CloudTrail、Task Definition、CloudWatch 和 ECS task evidence 校验。两个模式都不在 production release workflow 中执行 upgrade、stamp 或 downgrade。

## 文档与配置

已更新：

- `backend/.env.example`
- `backend/README.md`
- `backend/LOCAL_DEV.md`
- `docs/CD01_PRODUCTION_SAFE_DELIVERY.md`

说明了 Legacy 默认、DeepSeek 当前 Provider、Thinking disabled、非生产基础 Gate、生产双许可 latch、Release A/Release B、Fake Runtime 禁令，以及 Stage 3B 本地真实 Provider PASS 不等于 AWS Production Activation。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| Runtime Gate / readiness、Agent、Durable API/Coordinator/Context、Preview、PostgreSQL concurrency、CD-01 contracts、local harness contracts | **156 passed, 1 skipped**；skip 为可选 PR-04B Pydantic PoC lock 未安装 |
| CD-01 helper/workflow targeted contracts（包含于上项） | **46 passed** |
| PR-06B membership + conversation PostgreSQL migration contracts | **2 passed** |
| Frontend durable client、Plan characterization、embedded preview integration | **30 passed** |
| Workflow YAML parse | PASS |
| Python helper compile | PASS |
| `git diff --check` | PASS；只有既有 Windows LF→CRLF 提示 |
| Added-line + untracked RC secret scan | PASS |
| Embedded asset linkage | PASS；新 bundle 存在且被 index/manifest 引用，旧 bundle 不存在且不再被引用 |

本机没有可用的 `actionlint`；Windows 的 `bash.exe` 指向未安装 `/bin/bash` 的 WSL，因此 shell `bash -n` 未运行。YAML parser、离线 workflow contract tests 和精确脚本文本断言均已通过；真实 GitHub Actions 仍需后续授权。

## 本地数据库与进程

- 本轮只创建了全新的本地 disposable 数据库
  `cadensy_stage4b_rc_20261021_1352_test`。
- 创建前确认目标不存在，主机为 localhost，名称以 `_test` 结尾。
- 测试使用该数据库，不访问生产或 cloud RDS。
- 已仅删除上述精确数据库并确认不存在；本轮启动的 `D:\PostgreSQL\18\data` 本机 PostgreSQL 已使用 fast stop 正常停止，`pg_ctl status` 确认无 server running。

## RC 文件与证据卫生

- `.gitignore` 新增窄规则 `backend/test-results-*`；现有本地日志、fixture、control 文件和脱敏 Provider JSON 均保留在磁盘，但不再出现在默认 Git candidate set。
- 高置信 secret scan 未发现 Provider key、AWS access key、JWT、私钥或新增 credentialed database URL。
- 历史文档中的本机示例数据库凭据和 runner 的变量化本地 URL 构造被识别为既有/动态 false positive，不是本轮新增 secret。
- 不包含 RDS cleanup、Alembic migration、PR-05 Apply 改写或实验性 Agent 架构。

RC allowlist 共 **51 个路径**：39 个相对 `origin/main` 的 tracked change，加 12 个 untracked candidate；其中包含 1 个旧 embedded bundle 删除和 1 个新 bundle 新增。分类如下：

- Release contract：`.github/workflows/cd01-production-release.yml`、`scripts/cd01/prepare_ecs_task_definition.py`、`scripts/cd01/validate_release_evidence.py`、两份 CD-01 test 文件及 `docs/CD01_PRODUCTION_SAFE_DELIVERY.md`。
- Runtime/backend：`backend/app` 下 10 个受控 Runtime、API、conversation、preview 与 telemetry 文件；3 个 backend 配置/开发文档。
- Durable/Browser tests 与 harness：2 个 backend harness 脚本、6 个既有 backend regression 文件及新增 `test_pr06b4_local_e2e_harness.py`。
- Frontend/Trip：5 个 `trip/src/final` 文件、3 个 frontend tests，以及 embedded preview 的旧 bundle 删除、新 bundle、index、manifest。
- Evidence/docs：根 `.gitignore`、`AI_enhanced/README.md` 及 PR-07 的 README + Stage 1–4B 八份记录。

最终 added-line + untracked candidate 高置信扫描结果：AWS key、GitHub token、Provider secret、JWT、private key、credentialed database URL 均为 **0**。旧的本机日志/fixture/evidence 仍保留在磁盘并由窄 `.gitignore` 规则排除，没有删除或进入 RC candidate set。

## 双轴最终复审

- **Spec：PASS。** 上述三项缺口全部关闭；未发现新的需求偏差或范围扩张。
- **Standards：PASS。** 0 项书面规范违规、0 个阻断项。保留 3 个不阻断本 RC 的重复代码异味：durable terminal-error cleanup、DeepSeek model→telemetry mapping、durable 状态 UI 展示条件；本轮不扩大范围重构。

## 状态区分

- **RC Ready：PASS。** 可按显式 allowlist Commit/Push 并建立审查 PR；本轮未执行这些操作。
- **Release A Design Ready：PASS。** 本地静态发布流程和离线契约完整，但尚未执行。
- **Release A Execution Ready：** 否。仍需要 Commit/PR review、Main required reviewer、构建后的固定 digests、当前生产 schema 只读 evidence、rollback baseline evidence 和单独人工执行授权。
