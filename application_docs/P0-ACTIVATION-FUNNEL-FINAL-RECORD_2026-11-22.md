# P0 Activation Funnel：任务最终记录（Final Record）

> **仓库副本说明 / Repo copy note：** 本文是 P0 Activation Funnel 任务最终记录的仓库副本，派生自 Cadensy_Docs 的本地版本（sha256 `027110301e1892ef29d6603f557b78d147ccd34fbef9bdb312b550571ff6e088`）。只改写了只在本地有意义的路径和引用措辞（本地文件路径、事实包行号、聊天消息 ID）；事实、数字、SHA、run ID、时间以及 VERIFIED / NOT VERIFIED 标注均未改动。文中 S1–S7 来源文件和 QA 报告均为本地工作文件，未入库；保留文件名和 sha256 仅供追溯。

- **日期：** 2026-11-22（CST，America/Chicago，UTC-6）
- **编写：** Cadensy_Docs
- **状态：** COMPLETED（本地记录）。生产上线经由 Owner 执行的 workflow 完成（见 §5），但仍有若干 **NOT VERIFIED** 项（见 §6、§9）。
- **入库说明：** 本地版编写时没有建分支、推送或开 PR，也没有改仓库。本仓库副本经 Owner 批准以分支 + PR 方式提交到 `application_docs/`，是否合并由 Owner 决定。
- **时间：** 全文时间均为 CST。来源中没有 UTC/Z 格式的时间戳，所以没有做时区换算。
- **标注规则：**
  - **VERIFIED**：后面会写明证据，包括 QA 报告、run ID 或文件。
  - **NOT VERIFIED**：没有独立证据。
  - **“由 X 报告，NOT VERIFIED”**：来源只给了结论，没有可读的原始证据。
- **凭据处理：** 本文不收录任何凭据、令牌或带 userinfo 的数据库 URL。来源中出现的 `TEST_DATABASE_URL` 本身已省略 userinfo，本文也不转录。

## 来源（只读；sha256 为 2026-11-22 编写时由 Cadensy_Docs 计算）

| # | 来源文件（本地工作文件，未入库） | sha256 | 角色 |
|---|---|---|---|
| S1 | Cadensy_DD 整理的事实包 `P0-final-record-factpack.md` | `192168910b60ff1eeeb82da5651e685f7102da26c80c20d903ed187bdcc91700` | 主事实来源（Cadensy_DD，2026-11-22）。sha256 与预期前缀 `192168910b60ff1e` **一致** |
| S2 | `QA-PR54-metrics-2f595b57.md` | `d7ff52076e96e8004d8e2c1e524ee867c750754317049a8ce6439bd7fedffe69` | Cadensy_QA：PR #54 范围审查。S1 给的前缀 `d7ff5207…` **一致** |
| S3 | `QA-PR55-pin-36b16d2a.md` | `5304e2b9dd151cc2737d6e65deb3f70cb7c89b72a8cbdb510ccdec9b596bd50b` | Cadensy_QA：PR #55 第一轮（36b16d2a）。S1 没有点名这个文件，但提到了这轮 QA 的结论，S4 也引用了它 |
| S4 | `QA-PR55-ac3ea85a.md` | `74d23cf0f48541c4605233aaec04c01490b8e82e4e6b67b3172d31023604b73e` | Cadensy_QA：PR #55 最终轮（ac3ea85a）。S1 给的前缀 `74d23cf0…` **一致** |
| S5 | `P0-owner-decisions-and-backlog.md` (v2) | `d786638e31e9cd8d1255339c3677513479e8e802c3c91f1d468cdb287137fe85` | 2026-11-17 决策记录（Cadensy_Docs） |
| S6 | `PHASE0-current-state.md` | `d9a3c3981f4b1ba33d677485b8361650192cae7bf58676f53c5bdde99900466f` | 立项前现状核查 |
| S7 | `P0-ABC-implementation-brief.md` | `b33811facd45fc5ebd2b0003b4772adfce071b2e00ae30663c2caacca226cd9a` | 实现简报 |

S1 引用的 QA 报告文件都存在，没有缺失。

---

## 1. 立项

- **2026-11-17 15:35**，Owner 启动 P0，范围如下（由 S1 报告；S1 没有附 Owner 原文，所以 **NOT VERIFIED**）：
  - **P0-A**：分析事件和漏斗查询
  - **P0-B**：邀请、加入、分享体验
  - **P0-C**：60 秒偏好填写体验
  - **P0-D**：邮件提醒，暂缓，只做计划
  - **P1**（重新定位、商业化、AI 计费）**没有启动**
- **立项前现状核查（S6）。** 基线 main 是 `ee16f2b49a56ff8d6ba24db81d4c2b531981829b`，在 2026-11-17 约 15:45 复核过。
  - 前端 `npm ci && npm test` 结果为 192/192 通过（S6，日志 `fe-test.log`，本地工作文件，未入库）。
  - 后端 pytest **没有运行**，因为当时没有 PostgreSQL，后端事实都来自静态阅读（S6）。
  - S6 还记录了 Business · Worker 在 2026-11-17 15:14 的策略评审。该评审的结论在 S6 中标为 REPORTED。
- **实现简报（S7）** 把 Owner 决策列为约束，并要求实现方自行核实现状，不能直接沿用 S6 给出的 file:line。

## 2. Owner 决策（2026-11-17）

**归属说明：**

- 下列决策来自 S1（Owner 在 Cadensy_DD 聊天中的决策消息，2026-11-17）和 S5。S5 写明这些内容 **“由 Cadensy_DD 转达”**，不是 Owner 原话。
- `first_decision_completed` 的细化在 S5 里记为 Rinan 在 Cadensy_DD 聊天中的细化消息（2026-11-17，经 Cadensy_DD 转达）。S1 则把相近的定义记在 “Owner PR #54 review（转发的 ChatGPT 审阅，2026-11-17 20:16）” 名下。
- Cadensy_Docs 手上没有 Owner 原文消息，因此原文措辞 **NOT VERIFIED**。

1. **可复用邀请链接。** 同一个有效邀请可以反复复制，每次访问不重新生成 token。仍然只存 token 的哈希，保留撤销和行程隔离（S1；S5 §1-1）。
   - **Cadensy_DD 默认方案**（S5、S7）：每个行程只有一个有效邀请；原始链接只保存在组织者的浏览器存储里；显式“重新生成”时撤销旧链接；新邀请 30 天后过期；旧的 NULL 过期链接继续有效。
   - 这套默认方案是否已在 PR #54 中完整实现：现有 QA 报告没有覆盖，**NOT VERIFIED**（见 §3 范围说明）。
2. **投票或确认进行中时加入返回 409，保持不变。** 列入待办，见 §6（S1；S5 §1-2、§2）。
3. **`first_decision_completed`**（以细化后的定义为准，取代简报 S7 中“settled/confirmed”的旧写法）：
   - **计入：** 小组决策轮次，通过决策机制得出确定的最终结果，并且有真实成员参与。包括通过或应用的结果、正式否决（行程不必改变）、以及已化解的僵局。
   - **不计入：**
     - 进行中的轮次、没有确定结果的轮次
     - 撤回、取消或作废的轮次
     - 提案和投票开始
     - 组织者直接编辑
     - 预订
     - 零票自动保持（keep / `round_rejected`）
   - 只改分析埋点，不改决策引擎（S1；S5 §1-3）。
   - “零票自动保持不计”和“需真实成员参与”这两条只出现在 S1 所记的 PR #54 审阅意见里，S5 没有。
4. **`notice_auto_applied`** 作为单独事件记录，不计入这个激活指标（S1；S5 §1-3）。
5. **其他指标口径**（S1，来自 PR #54 审阅）：
   - `first_plan_created` 只统计第一次真正生成可用计划，不统计普通的 plan_change（包括 Notice 和 Booking）。
   - 邀请指标不能叫 “Invitation Acceptance Rate”。它的含义是：带来至少 1 名成员的邀请链接占比，分母是至少被打开过一次的链接。
   - 不新增用户追踪。
6. **分享：** 只做微信分享模板，不做 WhatsApp，不接 SDK（S1；S5 §1-4）。
7. **分析与隐私：** 最少量采集；不设持久化匿名浏览器 ID；事件里不放 token、私密约束或 PII（S1；S5 §1-5）。
8. **授权范围：**
   - **P0-A、P0-B、P0-C 获授权。**
   - **P0-D 邮件暂缓**：目前没有可复用的发信服务。SES 一类方案需要 Owner 另行批准成本、凭据、访客邮箱 PII 和用户同意（S5 §1-6）。
   - 以下事项仍需 Owner 明确授权：合并和 ECR 推送、生产迁移、部署、付费服务、重大安全或隐私变更（S5 §1-7）。

## 3. PR #54 实现与 QA

| 事项 | 内容 | 状态 |
|---|---|---|
| PR | https://github.com/shnnzdx/Cadensy/pull/54（cloud agent `bc-f3a49058`），共 46 个变更文件 | 46 个文件这一事实 VERIFIED（S2，API） |
| 最终 head | `2f595b57da2bb954f166c577be64415e588ab48f`，parent `59751d08b7bb5a8b6cec06a012454fe1d17f386a`，base `ee16f2b49a56ff8d6ba24db81d4c2b531981829b` | VERIFIED（S2） |
| QA 结论 | Cadensy_QA **范围内 PASS**。审查范围只有 `59751d08..2f595b57` 这 10 个文件：3 项指标修正，加一处只改文案的 UI 变更。**不是 46 个文件的全量审查** | VERIFIED（S2） |
| 本地测试 | 后端完整 Legacy 测试 1275 passed / 0 failed / 0 skipped（host `localhost`）；前端 `npm test` 199/0 | VERIFIED（S2） |
| 测试说明 | 第一次用 `127.0.0.1` 跑，1274 过、1 失败（`test_evaluation_foundation.py` 写死了 `localhost`，属于环境问题，不在本次提交内）；换成 `localhost` 后全过 | VERIFIED（S2） |
| CI | run `44222888415`（pull_request）和 `44222883863`（push）均 success，14/14 check-runs 成功 | 运行结果 VERIFIED（S2，元数据）。QA 读日志时返回 403 |
| CI 日志计数 | 后端 1275 passed，前端 199/199 | 由 Cadensy_DD 报告（S1）。QA 无法读取日志，但本地独立复现了相同计数 |
| 新迁移 | Alembic `c8e1d4a90b27`（新增 `activation_event` 表和 preference 的 `first_submitted_at` 列），parent `b7d3a1e9c642` | 迁移链 VERIFIED（S3 §3：单一 head，`down_revision` 为 `b7d3a1e9c642`） |
| 合并 | Owner 于 **2026-11-17 21:58** squash 合并，merge commit `1b9e3ff2d35f13d416b33c952138583bf67ac31c` | VERIFIED（S3：main 在 21:58 为 `1b9e3ff2…`，即 #54 的 squash） |

**`first_decision_completed` 的实现映射**（QA 在代码层面验证，VERIFIED，S2 §2）：

- 在 `orchestrator.py` `settle_round` 的 `:847` 处加了 `if votes and (...)` 判断。零票轮次仍然以 `keep` 关闭，但不再发出事件，状态机本身没有改。
- 有投票、结果为 keep 时记为 `round_rejected`；其他胜出选项记为 `round_applied`（`:852-857`）。
- 僵局记为 `deadlock_resolved`（`organizer.py:232`）。Confirm 仍在 `orchestrator.py:1052` 发出事件。`notice_auto_applied` 是单独事件（`orchestrator.py:679`）。
- 去重键为 `first_decision_completed:{trip_id}`，并且 `ON CONFLICT (dedup_key) DO NOTHING`。
- 相关测试全部通过。
- **仍待确认：** 数据库层面完整的列映射（例如上面这些结果标签写进哪个 metadata 字段），以及 Confirm 路径具体发出什么结果标签，S2 都没有写明。

**其他已验证的指标改动（S2）：**

- `invite_links_with_at_least_one_join_rate` 取代了 `invitation_acceptance_rate`。
- `first_plan_created` 只由 `_write_items` 的生成路径发出。
- `FinalApp.jsx` 只改了文案，重新构建出的 bundle 与提交的文件逐字节一致。
- 两个 LOW 级别的小问题：SQL 注释过时；有一个无关测试写死了主机名。

**NOT VERIFIED：**

- PR #54 中另外 36 个文件（P0-B、P0-C 的大部分 UI 和 API 实现、邀请复用与过期逻辑、微信模板等）没有可用的 QA 报告。
- `ai_generate` 路径只做了静态检查，没有实际执行（S2）。

## 4. 事故：后端镜像发布失败，以及 PR #55 修复

**事故经过：**

- PR #54 合并后，后端制品 run `44234319262`（Artifacts — Immutable Backend Images，2026-11-17 22:00:37 创建，head `1b9e3ff2`）在 step 9 “Verify migration runner offline without a database” 失败。
  - 失败这一事实 VERIFIED（S3，元数据）。
  - 原因：workflow 固定的 `BUILD_ALEMBIC_HEAD` 是 `b7d3a1e9c642`，而仓库的 head 已经是 `c8e1d4a90b27`。这个原因由 Cadensy_DD 报告，日志返回 403。QA 在本地复现了：旧固定值退出码 1，新值退出码 0。因此 **VERIFIED（通过复现）**（S3 §5）。
- **影响：** 后端镜像和迁移镜像都没有推送，没有执行任何迁移或部署，生产不受影响（S1）。这一点由 Cadensy_DD 报告，**NOT VERIFIED**，没有独立的 ECR 或生产读取。
- 前端镜像 run `44234319486` 成功，镜像被扣住没有部署（S1）。由 Cadensy_DD 报告，**NOT VERIFIED**。

**修复 PR #55**（https://github.com/shnnzdx/Cadensy/pull/55，cloud agent `bc-6c7c7072`）：

- S1 记录：因为 Terra 额度用完，改用 Grok 4.7 high；Owner 指示“全部改直到成功”。由 Cadensy_DD 报告，**NOT VERIFIED**。
- **第一轮 `36b16d2a286e541a99cdcc52359c04853ecd036e`**（S3）：
  - 7 个文件，+10/−10，只改固定值和文档措辞。
  - Cadensy_QA 结论 **BOUNDED PASS**：
    - **F-1 MEDIUM**：生产迁移和发布路径不支持从 `b7d3` 开始，会安全地失败（fail closed）。
    - **F-2 LOW**：校验器不检查 `activation_event`。
  - 测试：后端 1275 过；CI 14 项检查，13 成功、1 跳过。以上 VERIFIED。
- **最终轮 `ac3ea85a3aebab387e45d8653fe1c8f72bf865db`**（S4，提交时间 2026-11-17 22:52）：
  - 只在 recovery 模式下允许从 `b7d3` 开始。
  - 发布证据要求出现 `Running upgrade b7d3a1e9c642 -> c8e1d4a90b27`。
  - 校验器要求存在 `activation_event` 和 `uq_activation_event_dedup_key`。
  - 新增测试 `test_cd01_activation_gate_postgres.py`。
  - 相对 main 共 8 个文件，+362/−23。没有改动触发器、权限、environment、secret、OIDC 或应用代码。
  - Cadensy_QA 结论 **PASS**，F-1、F-2 和过时注释都已关闭。
  - 测试 1278/0/0。CI 方面：PR run `44240321252`、push run `44240316558` 均 success，14/14。以上 VERIFIED（S4）。
  - 遗留问题 **R-1 LOW**：校验器不检查 `first_submitted_at`、PK、`ix_activation_event_*` 索引、FK 和 check 约束，后续补不补可选。另有 R-2、R-3、R-4，均为 INFO 级。
- **合并（由 Cadensy_DD 报告，NOT VERIFIED）：**
  - 2026-11-18 合并到 main，提交 `bb4791fc`（S1；没有给出具体时刻，也没有完整 SHA）。
  - 后端制品 run `44358160583`、前端制品 run `44358160632` 均成功。

## 5. 2026-11-22 生产上线

以下三步都由 Owner 通过 GitHub Actions `workflow_dispatch` 执行，使用受保护的 `Main` environment。全部内容**由 Cadensy_DD 报告（S1）**；没有 QA 报告，Cadensy_Docs 也没有独立读取这些 run。

| # | 时间 | 内容 | 状态 |
|---|---|---|---|
| 1 | 10:52 | **Release — Production Schema Migration**，run `45046230145`。source `bb4791fc`，mode recovery，start `b7d3a1e9c642`，target `c8e1d4a90b27`。镜像 `tripsync-backend@sha256:2d55a69208593f15354146c09bbb9fc93828c7d0c5b1b25988474e2aa454802d`。所有步骤成功，包括只读 catalog 校验 | 由 DD 报告。**迁移日志原文 NOT VERIFIED**：GitHub job 日志不打印 alembic 输出，`Running upgrade` 那一行在 step summary 和 CloudWatch 里，DD 没有独立读取 |
| 2 | 11:32 | **CD-04 — Manually Approved Pydantic Code Release**，run `45050004725`。source `5d3f44eb`（PR #57）。runtime `tripsync-backend@sha256:0de389ecebd85290c58bd61b9fbfa8d5e66b1020662c8ba45ab57dfdf7997fac`，回滚基线 task def `tripsync-backend:31`。c8 schema 预检通过，promotion 成功，没有触发回滚 | 由 DD 报告。**PR #57 没有经过团队 QA**，NOT VERIFIED |
| 3 | 11:57 | **Release — Production Frontend**，run `45052327129`。workflow 版本为 `3c3ce0f6`，source `1b9e3ff2`（PR #54 的合并提交）。镜像 `tripsync-frontend@sha256:34ff3bfb1afbfc1f7eb4adb87194b3f234a107fa21caf32fad05e62f8b2eb9b2`。ALB 健康检查和公网路由检查成功 | 由 DD 报告。**提交 `3c3ce0f6` 没有经过团队 QA**，NOT VERIFIED |
| — | 12:09 | DD 外部检查：`https://app.cadensy.top` 返回 200；`/api/health` 返回 `{"ok":true}` | 由 DD 报告，NOT VERIFIED。这只能说明服务存活，不能说明新功能正常 |

**Owner 主导、未经团队 QA 的发布相关变更（只有 CI 通过，S1）：**

- docs PR #56（`05d92ca7`），刷新 P0 handoff，2026-11-22 合并。
- PR #57（`5d3f44eb`），“ci: add pydantic-preserving backend code release”，新增 CD-04 workflow。
- 提交 `3c3ce0f6`，“ci: archive superseded releases and normalize workflow names”，分支 `codex/workflow-lifecycle-cleanup`。

**需要注意：**

- 生产后端运行的代码由 `5d3f44eb`（PR #57）构建，不是 QA 过的 `ac3ea85a` 或 `bb4791fc`。
- 生产前端来自 `1b9e3ff2`。PR #54 的 QA 只覆盖了其中 10 个文件。

## 6. 遗留事项

| 事项 | 状态 |
|---|---|
| 真实账号手动冒烟测试（重复复制邀请、加入、60 秒偏好），以及漏斗数据是否真的写入 | **未做，NOT VERIFIED**（S1） |
| 团队 QA 审查 PR #57 和 `3c3ce0f6` | **未做**（S1） |
| 迁移日志原文（`Running upgrade b7d3a1e9c642 -> c8e1d4a90b27`，在 step summary 和 CloudWatch 中） | **没有独立读取** |
| R-1：扩展校验器检查范围 | 可选（S1；S4 §7） |
| `first_decision_completed` 的完整数据库列映射，以及 Confirm 路径的结果标签 | 待确认（见 §3） |
| PR #54 其余 36 个文件（包括邀请复用和 30 天过期的默认方案） | 没有 QA 报告，NOT VERIFIED |
| P0-D 邮件提醒 | 暂缓 |
| 待办 **BACKLOG-P0-JOIN-409**（编号为提议）：投票或确认进行中时加入返回 409，影响激活，P0 不改 | open / backlog（S5 §2） |
| 不要启动 | R1-B、PILOT-FIX-02、PILOT-DOC-06，以及计划中的 ENG-01、SEC-02、SUPPLY-01、BUILD-01（S1） |
| 流程风险 | workflow 仍使用 IAM 用户凭据（“Proceeding with IAM user credentials”）；OIDC（SEC-02）还在计划中（S1），由 DD 报告 |

---

## 7. 时间线（CST）

| 时间 | 事件 | 证据 |
|---|---|---|
| 2026-11-13 18:33 | main 为 `ee16f2b4…`（Merge PR #51），即 P0 基线 | S6 |
| 2026-11-17 15:14 | Business · Worker 策略评审（REPORTED） | S6 |
| 2026-11-17 15:35 | Owner 启动 P0 | S1（由 DD 报告） |
| 2026-11-17 ~15:45 | Phase 0 现状核查，main 复核为 `ee16f2b4…` | S6 |
| 2026-11-17 | Owner 决策消息；`first_decision_completed` 细化消息（均为 Owner 在 Cadensy_DD 聊天中的消息） | S1、S5（经 DD 转达） |
| 2026-11-17 20:16 | Owner 转发 PR #54 的审阅意见（ChatGPT review） | S1 |
| 2026-11-17 20:23 | PR #54 head `2f595b57` 提交；CI `44222888415` / `44222883863` 成功 | S2 |
| 2026-11-17 ~20:55 | QA PR #54 范围内 PASS | S2 |
| 2026-11-17 21:58 | PR #54 合并，提交 `1b9e3ff2` | S1、S3 |
| 2026-11-17 22:00:37 | 后端制品 run `44234319262` 失败（step 9） | S3 |
| 2026-11-17 22:38:48 | PR #55 提交 `36b16d2a`；CI `44238797115` / `44238773735` 成功 | S3 |
| 2026-11-17 ~22:55 | QA PR #55 第一轮 BOUNDED PASS（F-1 / F-2） | S3 |
| 2026-11-17 22:52 / 22:54 | PR #55 提交 `ac3ea85a`；CI `44240321252` / `44240316558` 成功 | S4 |
| 2026-11-17 22:54 之后 | QA PR #55 最终轮 PASS（报告抬头日期写成 2026-11-06，见 §10） | S4 |
| 2026-11-18 | PR #55 合并为 `bb4791fc`；制品 run `44358160583` / `44358160632` 成功 | S1（由 DD 报告） |
| 2026-11-22 | PR #56 合并；PR #57（`5d3f44eb`）；提交 `3c3ce0f6`；均未经团队 QA | S1 |
| 2026-11-22 10:52 | 生产 schema 迁移 run `45046230145` | S1（由 DD 报告） |
| 2026-11-22 11:32 | CD-04 后端发布 run `45050004725` | S1（由 DD 报告） |
| 2026-11-22 11:57 | 生产前端发布 run `45052327129` | S1（由 DD 报告） |
| 2026-11-22 12:09 | DD 外部检查：200，`{"ok":true}` | S1（由 DD 报告） |

## 8. 状态分级

三种状态分开记录：合并到 main、部署到生产、经过独立验证。

| 变更 | merged-main | deployed-production | independently-verified（团队 QA） |
|---|---|---|---|
| PR #54（`1b9e3ff2`） | 是，2026-11-17 21:58（VERIFIED，S3） | 前端以 `1b9e3ff2` 部署，run `45052327129`；后端改由 CD-04 从 `5d3f44eb` 部署（由 DD 报告） | 部分：只有 10 个文件范围内 PASS（S2）；其余 36 个文件没有 |
| 迁移 `c8e1d4a90b27` | 是（随 #54） | 是，run `45046230145`（由 DD 报告；日志原文 NOT VERIFIED） | 仅离线和合成库验证（S3、S4）；生产迁移本身没有独立验证 |
| PR #55（`ac3ea85a` → `bb4791fc`） | 是，2026-11-18（由 DD 报告） | 迁移镜像 source 为 `bb4791fc`（由 DD 报告） | PASS at `ac3ea85a`（S4）；对 `bb4791fc` 本身没有 QA |
| PR #56（`05d92ca7`，文档） | 是，2026-11-22（由 DD 报告） | 不适用 | 否 |
| PR #57（`5d3f44eb`） | 由 DD 报告，合并时间未注明 | 是，生产后端 runtime（由 DD 报告） | **否** |
| `3c3ce0f6` | 由 DD 报告，在分支 `codex/workflow-lifecycle-cleanup` 上；是否已在 main 上，S1 没有写明 | 作为前端发布的 workflow 版本被使用（由 DD 报告） | **否** |
| 新功能端到端（真实账号） | 不适用 | 不适用 | **否，没有做冒烟测试** |

## 9. NOT VERIFIED 汇总

1. 迁移日志原文（`Running upgrade b7d3a1e9c642 -> c8e1d4a90b27`）没有被独立读取。
2. PR #57（`5d3f44eb`）和提交 `3c3ce0f6` 没有经过团队 QA，但已用于生产后端和前端发布。
3. 真实账号冒烟测试没有做；漏斗数据是否真的写入，没有验证。
4. 2026-11-22 三次生产 run 的结果和 12:09 的外部检查，全部来自 Cadensy_DD 报告，没有 QA 或 Docs 的独立读取。
5. PR #55 于 2026-11-18 合并为 `bb4791fc`，以及 run `44358160583` / `44358160632` 成功：由 DD 报告。
6. 前端 run `44234319486` 成功并被扣住；事故没有影响生产：由 DD 报告。
7. CI 日志中的计数（后端 1275 / 1278、前端 199）：由 DD 报告，QA 读日志时返回 403。QA 在本地独立复现了相同计数。
8. PR #54 其余 36 个文件，包括邀请复用、30 天过期、微信模板和 P0-C UI，没有 QA 报告。
9. `first_decision_completed` 的完整数据库列映射，以及 Confirm 路径的结果标签，待确认。
10. Owner 原话（2026-11-17 的决策消息、细化消息，以及 20:16 的审阅转发，均在 Cadensy_DD 聊天中）：Cadensy_Docs 没有原文，只有 DD 的转述。
11. 换用 Grok 4.7 high 的原因，以及 Owner 指示“全部改直到成功”：由 DD 报告。
12. IAM 用户凭据风险：由 DD 报告。
13. 生产数据库在迁移前处于 `b7d3`：S3 标为 REPORTED（来自仓库历史执行记录）；迁移 run 成功（由 DD 报告）间接支持这一点。

## 10. 来源之间的矛盾和差异

1. **S4 抬头日期写的是 “2026-11-06”**，但报告内容中的提交时间是 2026-11-17 22:52，CI 更新于 2026-11-17 22:54。抬头日期应为笔误，本文按 2026-11-17 记录，没有修改 S4。
2. **`first_decision_completed` 的来源和措辞不一致：**
   - S5 记为 Rinan 在 Cadensy_DD 聊天中的细化消息（经 DD 转达）。
   - S1 记为 20:16 的 PR #54 审阅，并多了“真实成员参与”和“零票自动保持（`round_rejected`）不计”两条。
   - 两处内容可以兼容，S1 是进一步细化，但措辞和出处不完全一致。S7 的“settled/confirmed”旧写法已被取代。
3. **S1 开头说“只收录经 DD/QA 验证的事实”**，但事实包后半部分本身就标出了未审和未验证的项目。本文按逐条标注处理。
4. **生产代码与 QA 过的代码不是同一个提交：**
   - 后端 runtime 是 `5d3f44eb`（PR #57，未 QA），不是 `ac3ea85a` / `bb4791fc`。
   - 前端发布使用的 workflow 版本是 `3c3ce0f6`（未 QA）。
   - 这不是来源之间的矛盾，但说明“已部署”不等于“已独立验证”。
5. **S1 只给了短 SHA**：`bb4791fc`、`05d92ca7`、`5d3f44eb`、`3c3ce0f6`；本文照录。

## 11. 证据索引

- **文件：** 见文首来源表 S1–S7。S6 引用的 `fe-test.log`（本地日志，未入库）的 sha256 为 `dc69bfffbd7da537f13e20a734f07907d90f8df48ae31917ea3b26023e28db3a`。
- **PR：** #54、#55、#56、#57（https://github.com/shnnzdx/Cadensy/pull/54、/55；#56、#57 只见于 S1）。
- **Cloud agent：** `bc-f3a49058`（#54）、`bc-6c7c7072`（#55）。
- **提交：**
  - 基线 `ee16f2b49a56ff8d6ba24db81d4c2b531981829b`
  - `59751d08b7bb5a8b6cec06a012454fe1d17f386a`
  - `2f595b57da2bb954f166c577be64415e588ab48f`
  - `1b9e3ff2d35f13d416b33c952138583bf67ac31c`
  - `36b16d2a286e541a99cdcc52359c04853ecd036e`
  - `ac3ea85a3aebab387e45d8653fe1c8f72bf865db`
  - `bb4791fc`、`05d92ca7`、`5d3f44eb`、`3c3ce0f6`
- **Alembic：** `b7d3a1e9c642` → `c8e1d4a90b27`。
- **CI / Actions run：**
  - PR #54：`44222888415`、`44222883863`
  - 事故：`44234319262`（job `134566710076`）、`44234319486`
  - PR #55 第一轮：`44238797115`、`44238773735`
  - PR #55 最终轮：`44240321252`、`44240316558`
  - 合并后制品：`44358160583`、`44358160632`
  - 生产上线：`45046230145`、`45050004725`、`45052327129`
- **镜像 digest：**
  - 迁移镜像 `tripsync-backend@sha256:2d55a69208593f15354146c09bbb9fc93828c7d0c5b1b25988474e2aa454802d`
  - 后端 runtime `tripsync-backend@sha256:0de389ecebd85290c58bd61b9fbfa8d5e66b1020662c8ba45ab57dfdf7997fac`
  - 前端 `tripsync-frontend@sha256:34ff3bfb1afbfc1f7eb4adb87194b3f234a107fa21caf32fad05e62f8b2eb9b2`
  - 回滚基线 task def `tripsync-backend:31`
