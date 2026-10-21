# PR-07 — Release Candidate Summary and Next Steps

> 本文记录 Stage 4A 结束时的判断。Stage 4B 已继续处理这里列出的
> CD-01 activation latch、code-only Gate M 兼容和证据卫生事项；最新状态以
> `07_STAGE4B_RC_ASSEMBLY_AND_CD01_RELEASE_SAFETY.md` 为准。

## 当前结论

Stage 2、Stage 3A 与 Stage 4A 的本地源代码和测试共同证明：Legacy Runtime 的真实 Provider 链路、Direct Candidate durable persistence、刷新/重新登录恢复、Historical Resolve 和安全边界已经达到可审查 Release Candidate 状态。

**可以开始建立 Draft/Review PR。** 这不等同于批准生产发布或生产 Runtime 激活。

## 建议分支与 Commit 划分

建议分支：`codex/cadensy-legacy-durable-runtime-rc`

建议按可审查边界拆分：

1. `feat(ai): add default-off legacy runtime activation controls`
   - Runtime/readiness/Provider limits/telemetry/CD-01 helper。
2. `fix(conversations): persist preview direct candidates as durable options`
   - Coordinator mapping、JSON normalization、dedupe 与后端回归。
3. `fix(trip): harden durable conversation recovery and option rendering`
   - 同步发送锁、idempotency、pending/failed/recovery、Plan Assistant UI、嵌入资源原子更新。
4. `test(e2e): secure disposable chromium durable-chat harness`
   - 随机 `_test` 数据库、拒绝覆盖、credential-safe fixture、Direct Candidate 浏览器验收。

暂存必须使用文件 allowlist；不得使用 `git add -A`，避免把 `backend/test-results-*` 加入提交。

## Release Candidate 文件边界

候选代码集中在：

- `backend/app/agents/`
- `backend/app/api/main.py`
- `backend/app/domain/chat/service.py`
- `backend/app/domain/conversations/`
- `backend/app/domain/preview_apply.py`
- `backend/app/observability/telemetry.py`
- `backend/scripts/pr06b4_e2e_fixture.py`
- `backend/scripts/run_pr06b4_local_e2e.ps1`
- 对应 `backend/tests/`
- `trip/src/final/` durable conversation 与 Plan Assistant 文件
- 对应 `frontend/tests/`
- 原子更新后的 `frontend/public/trip-app/index.html`、`embed-manifest.json` 与新 hashed asset
- `scripts/cd01/prepare_ecs_task_definition.py` 及其离线 contract tests。

本地测试结果、fixture 输出、stdout/stderr log、control 目录和 provider sanitized JSON 是运行证据，不属于发布源代码。

## 生产发布阻断项

### P1 — CD-01 workflow 未显式验证新 activation latch

`scripts/cd01/prepare_ecs_task_definition.py` 已强制：

```text
CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED=0
```

相关 helper tests 也验证该值。但 `.github/workflows/cd01-production-release.yml` 的 rollback/final runtime-off `jq` 检查仍只覆盖开发 header、基础 Durable Gate 和 Fake Runtime，尚未显式断言新的 production activation latch 为 `0`。

最小修复：在 workflow 的 runtime-off 契约检查与离线 contract test 中加入该 latch；不改变部署架构，不执行 workflow。

### P1 — RC 证据卫生与原子资源暂存

- 必须排除全部 `backend/test-results-*`。
- 必须将旧 hashed bundle 删除、新 bundle、manifest 与 HTML 引用作为一个原子变更暂存。
- 建议在提交前补充 `.gitignore` 或预提交证据检查，但不应为了这个问题重构仓库。

### P2 — 配置文档尚未完整描述双 Gate

`.env.example`、`backend/README.md`、`backend/LOCAL_DEV.md` 和交接文档尚未完整记录 base gate + production activation latch、Legacy/model/thinking 选择与 runtime-off rollback。发布前应补齐运维文档。

### 非阻断维护项

- `base.py` 与 `pydantic_runtime.py` 存在少量 model → telemetry 名称映射重复。
- `useDurableConversation.js` 的 terminal error cleanup 有重复分支。
- 这些不影响本轮验收，不应在 RC 中扩大为架构重写。

## Release A — Deploy Code, Runtime Off

目标：部署已验证代码，但不激活真实 Provider。

- 固定 Source SHA 与镜像 digest。
- `CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=0`。
- `CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED=0`。
- `CADENSY_E2E_FAKE_RUNTIME=0`，生产开发身份旁路关闭。
- 不注入 Provider credential；不执行新的 Schema Migration。
- 验证健康检查、现有受保护功能、Runtime-off 历史可读与 composer disabled。
- 回滚到已记录且 schema-compatible 的前一 Task Definition；不 downgrade 数据库，不删除 durable history。

Release A 之前必须关闭上面的 CD-01 latch contract 阻断。

## Release B — Separately Authorized Runtime Activation

目标：在独立批准后，以受控流程启用 Legacy + DeepSeek。

- 使用已批准的 DeepSeek Secret Reference，不显示或复制 secret value。
- Legacy Runtime、`deepseek-flash`、Thinking disabled、Mock/Fake off。
- base gate 与 production activation latch 均显式为 `1`。
- 固定 Source SHA / Image Digest；不直接执行历史 Ollama-era workflow。
- 先进行极小流量验证，核对 readiness、ECS Task Definition、安全 telemetry、请求预算和 durable completion。
- 生产 rollback 是回到 Release A：两个 Gate 关闭，Provider 不再可用，持久化历史保留。
- 禁止通过数据库 downgrade 或删除 Conversation history 回滚 Runtime。

## 下一步顺序

1. 补 CD-01 activation latch 的 workflow 离线契约检查。
2. 补配置/回滚文档。
3. 使用 allowlist 建立干净 feature branch 和四个逻辑 Commit。
4. 再次运行 targeted tests、Trip preview build、secret/evidence scan 与 `git diff --check`。
5. 创建 Draft/Review PR，先审代码与 Release A runtime-off 契约。
6. Release B 保持独立审批，不能随 PR 合并自动激活。

## 权限与状态声明

- 未 Commit、Push、Merge。
- 未调用 AWS、生产 RDS、ECS、SSM 或 KMS。
- 未部署。
- Stage 4A 没有新的 DeepSeek 请求。
- 当前结论仅授权进入代码审查准备，不授权生产激活。
