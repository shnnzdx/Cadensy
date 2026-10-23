# Cadensy 项目交接文档

最后更新：2026-10-22（Release B 生产验收关闭）

当前工作目录：`C:\Users\zdxzh\Desktop\capstone\New`

权威生产工具基线：`origin/main` 的
`72eda0d919d75a7ed40561313203d52f39ecffd7`。本机 checkout 是操作员相关状态，
接手时必须重新运行 `git status --short --branch` 和 `git rev-parse origin/main`，
不要假设当前分支就是 `main`。

给其他 AI 的最新可复用交接 prompt 在：

- `docs/HANDOFF_PROMPT_CURRENT.md`
- `docs/HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md`（仅限 backend / repo refactor 任务）

> 项目历史文档中可能仍出现 `TripSync`。在当前产品语境中，它指的就是 Cadensy，不要因此复制或新建第二套项目结构。

## Release B 当前生产状态（2026-10-22）

**Release B — ACCEPTED AND CLOSED。** 当前生产部署是已经通过真实 DeepSeek
受限验收的稳定基线，而不是待继续试验的阶段性环境。

- 活跃 Backend Task Definition：`tripsync-backend:26`；紧急 Runtime-off
  回滚基线：`tripsync-backend:25`。
- Durable Conversation 两个生产 Gate 均为 `1`；运行时为 Legacy +
  DeepSeek `deepseek-flash`，Thinking disabled，`MOCK_AI=0`、Fake E2E Runtime=`0`。
- 生产验收已确认真实 Provider 回复、持久化 Conversation、重新认证后的服务端
  Context Reconstruction、Idempotency、脱敏 Telemetry 及 ECS/ALB/API 健康。
- 不要为了整理历史 Ollama 字段而修改 `:25`、回滚、创建新 ECS revision，或触发
  CD-02。历史 Ollama AWS 退役是 **DEFERRED — NON-BLOCKING**，必须另行批准。
- `AI_enhanced/PR-08_Controlled-DeepSeek-Production-Activation/02_RELEASE_B_FINAL_PRODUCTION_ACCEPTANCE_AND_CLOSEOUT.md`
  是本次生产验收和后续边界的正式记录。

这不是 Chromium UI E2E 的新验收记录：Context Recovery 是通过真实生产 API 的
重新认证会话验证。跨请求 token/cost 停止阈值是人工控制，不是已实现的硬性费用上限。

## 1. 接手后的第一步

开始修改前，按顺序阅读：

1. `AGENTS.md`
2. `README.md`
3. `INTEGRATION-ROADMAP.md`
4. 本文档 `HANDOFF.md`
5. `docs/navigation-known-wrong-behavior.md`
6. 涉及后端时再读 `backend/README.md`、`backend/LOCAL_DEV.md`

所有操作保持在：

```text
C:\Users\zdxzh\Desktop\capstone\New
```

不要修改这个目录之外的文件。

## 2. 当前工作区非常重要

工作树可能包含尚未提交的开发成果或生成后的 Trip preview assets。它们属于当前开发成果，不要执行：

```bash
git reset --hard
git checkout -- .
git clean -fd
```

也不要为了“清理工作区”覆盖或删除不属于当前任务的改动。开始工作前先运行：

```bash
cd /Users/jiayichen/Desktop/cap_stone
git status --short --branch
git diff --check
```

提交前应按功能审查实际 diff，不要把所有文件盲目打包成一个无法解释的提交。

## 3. Repository 结构

这是一个混合仓库，不是单一前端：

- `frontend/`：主站、登录/注册页以及 `/trip` host shell
- `trip/`：Trip workspace 的源代码
- `frontend/public/trip-app/`：由 `trip/` 构建并同步的嵌入产物，不是源代码
- `backend/`：FastAPI、SQLAlchemy、PostgreSQL、Planner、Place Service
- `shared/`：跨前端的 navigation、session、copy 和 compatibility contracts
- `docs/`：产品、架构和历史交接资料
- `AWS/`：AWS 部署上下文和规划资料

主要依赖方向：

```text
frontend host shell
        ↓ embeds
trip workspace
        ↓ HTTP API
FastAPI → domain services → PostgreSQL / AI providers / Geoapify
```

## 4. 不可随意破坏的架构边界

### Navigation

`shared/trip-navigation-policy/` 负责 workspace destination policy、route reachability、restoration fallback 和 invite/join destination decision。

不要在 UI component 中重新 hardcode role-based navigation。

### Technical Session

`shared/session-runtime/` 负责：

- `tripsync:*` storage keys
- bearer token mechanics
- `Authorization`、`X-Trip-Id`、`X-Membership-Id` identity headers
- invite adoption cache
- invalid-session clearing
- logout sequencing

不要在随机组件中重复实现这些逻辑。

### Plan Workspace

`trip/src/final/plan-feature/PlanFeature.jsx` 是 Plan feature 的唯一 public boundary。

- `usePlanInteractionRuntime`：selection、comments、map/list、menu、booking、drawer state
- `useAssistantChangeRequestFlow`：Cadensy drawer conversation、proposal apply、change-request flow
- `FinalApp.jsx`：只负责 route mount、workspace composition 和 command execution

不要把 Plan interaction state 搬回 `FinalApp.jsx`，也不要绕过这个边界另建第二套 PlanFeature。

## 5. 当前产品核心

Cadensy 不是普通 itinerary generator，而是 group-travel decision engine：

- 维护一个持续更新的 Current Plan
- 所有 change request 经过后端规则分类
- 保护成员的 private constraint wording
- 通过 ledger 保留 plan changes

当前后端 decision path：

- `notice`
- `round`
- `reopen_round`
- `confirm`

AI 可以解释或辅助生成，但不能选择 change path，不能读取其他成员的 private raw wording，也不能绕过后端规则直接修改 Current Plan。

## 6. Place Library / Geoapify 当前实现

主要文件：

- `backend/app/domain/places/service.py`
- `backend/app/domain/places/geoapify.py`
- `backend/app/db/models.py`
- `backend/app/db/init_schema.py`
- `backend/tests/test_places.py`

当前策略：

1. 所有 destination（包括 Chicago）都先查询 PostgreSQL `place` cache。
2. `backend/data/poi_chicago.py` 保留用于 legacy reference、tests、demo fixtures 和 compatibility，但不再作为正式 Planner candidate source。
3. cache 数量或类别覆盖不足时才调用 Geoapify。
4. Geoapify 先 geocode city，再调用官方 endpoint：

   ```text
   https://api.geoapify.com/v2/places
   ```

5. retrieval 按类别组分别请求，不再依赖单次 union query 的前 N 条：
   - major attractions
   - museums
   - historic places
   - parks
   - food
   - leisure
6. 请求使用 city `place:` filter，并通过城市范围内的多个 bias 点改善地理覆盖。
7. 返回结果 merge、deduplicate、normalize、upsert 后写入 PostgreSQL。
8. Planner candidate pool 再按类别 round-robin，并在每类内部进行 geographic spread。

重要数据规则：

- `price = unknown` 保持 `null`
- `duration = unknown` 保持 `null`
- `opening_hours = unknown` 保持 `null`
- `walking = unknown` 保持 `null`
- unknown 不等于 free、90 minutes、all-day 或 low walking
- 不要恢复任何假默认值

英文名称规则：

- 优先读取 Geoapify 的 English/international name
- 原始名称只含 Latin 字母时可作为英文主名
- 有可靠 local name 时，作为次级名称保留
- 没有可靠英文名且原名不是 Latin script 时，当前不会要求 Planner 猜测翻译
- display alias 只能是小范围展示层优化，不能变成复杂 planning tags

## 7. Planner 与 AI Provider 当前状态

Planner 入口：

```text
POST /api/trips/{trip_id}/plans/generate
→ backend/app/domain/plans/generator.py
→ backend/app/agents/planner.py
→ backend/app/agents/base.py
```

当前真实 AI Provider 路由为 **DeepSeek-only**，由 `DEEPSEEK_*` 配置控制；不使用
任何历史 Provider selector 作为当前运行时选择路径。Chat Runtime 默认是 `CHAT_AGENT_RUNTIME=legacy`；
`pydantic` 仍是显式 opt-in，且不会回退到 Legacy。

仓库本地配置示例的默认 `DEEPSEEK_MODEL` 是兼容别名
`deepseek-v4-flash`。Stage 3B 的受限本地真实 Provider 验证则显式使用
`deepseek-flash`、Thinking disabled；该验证配置不等于更改仓库或生产默认值。

Durable Chat 的运行权限是明确的：

- 非生产 `legacy` + `MOCK_AI=1` + Durable Base Gate=`1` + Fake E2E Runtime=`0`
  可走确定性的本地 Mock Reply；它不会调用 DeepSeek、不会伪造建议或 Preview。
- 真实 DeepSeek 需要 `MOCK_AI=0`、官方 HTTPS endpoint、有效 Provider 配置与
  Durable Base Gate=`1`。
- `APP_ENV=production` 还必须同时拥有独立 Production Activation Latch 与生产签名
  material；`MOCK_AI=1` 和 Fake E2E Runtime 都不能绕过这一限制。

发布边界保持不变：**Release A** 仅发布代码，两个 Durable Runtime gate 都固定为
`0`，不附加 Provider credential；**Release B** 必须经过单独授权，才可配置
DeepSeek secret reference、Legacy selector、Thinking-off/model 与双 gate。不要输出、
复制或提交任何真实 API Key；Provider 失败时，核心 deterministic rules 与 decision
flow 必须保持可运行。

### Release B 已完成的生产验收

Release B 已在 2026-10-22 经受保护的 CD-02 工作流和 `Main` Environment 审批激活，
随后完成有界真实 DeepSeek 验收。最终生产 Profile 与证据、Token/费用计算方法、
回滚边界及未完成的非阻断运营事项，均记录在
`AI_enhanced/PR-08_Controlled-DeepSeek-Production-Activation/02_RELEASE_B_FINAL_PRODUCTION_ACCEPTANCE_AND_CLOSEOUT.md`。

不要把此前“Release B 必须单独授权”的实现期说明当作当前仍未上线；后续任何
Activation、Runtime-off Rollback、Secret/IAM/KMS 变更仍需新的明确生产授权。

## 8. Planner 最新 scheduling 行为

主要文件：

- `backend/app/domain/plans/generator.py`
- `backend/app/agents/planner.py`
- `backend/tests/test_plan_generation.py`
- `backend/tests/test_planner.py`

当前日程生成把 sightseeing 与 meals 分开处理：

### Candidate identity / place names

- DeepSeek Planner 只返回 `candidate_id + start_hour`，不返回地点名称。
- 候选中的 `english_name`、`local_name`、category、坐标和营业信息都是只读事实。
- 后端用 `candidate_id` 查回本次 Place Library 候选，并把候选中的规范
  `english_name / local_name` 写入 `PlanItem.title / PlanItem.local_title`。
- Planner 不得创造、翻译、改写或拼接地点名称；无可靠英文名的非拉丁地点继续在
  Place Service 阶段过滤。

### Sightseeing

- 每天约 2–4 个 sightseeing activities
- 使用 `(3, 2, 4, 3, 2)` 的 soft variation pattern 循环，但不会为了凑数量而让 itinerary blocked
- morning / afternoon / late-afternoon 时间会按日期自然变化
- 尽量在 16:00 后安排至少一个 meaningful sightseeing item
- 避免固定使用每天同一组 `10:00 / 14:00 / 19:00`
- 正常完整日通常 09:00–10:30 开始，并在 Dinner anchor 计入后持续到约 18:00 或更晚
- 规划优先级依次为 hard constraints、fixed/locked events、dates/availability、
  opening hours、geographic feasibility、timing/transition、meals、soft interests、variety
- 已知营业时间和活动时长必须遵守；优先同一区域并为交通和余量留出空间
- 每天先从全城候选中选择一个 spatially-diverse anchor，再以 Haversine 直线距离形成
  当天的 neighborhood candidate pool；不同日期轮换 anchor，避免所有天集中到同一区域
- rules fallback 会按上一站坐标、interest relevance 和候选质量排序，并拒绝已知时长重叠
- 这里只使用坐标距离做 clustering，不把它描述成真实 walking route/time

### Meal anchors

- Lunch 约 11:30–13:30
- Dinner 约 17:30–20:00
- meal slots 不计入 2–4 个 sightseeing 数量
- Lunch 优先 restaurant/casual dining，并可在放宽阶段接受有实质食物的 café、bakery、
  food hall；Dinner 优先 restaurant/dining/regional cuisine，并排除 café-only 候选
- meal selection 以上午/下午路线交界选择 Lunch，以最后 sightseeing area（及可选 evening
  stop）选择 Dinner；依次尝试约 1.5km、3km、6km 的坐标半径，再放宽 meal-specific
  category，最后才使用 honest flexible meal break
- required dietary constraints 会继续通过 constraint engine 过滤真实餐厅；Preferences 的
  maximum budget 会对已知餐价做上限筛选，未知餐价保持 unknown，不会伪造
- meal start 会在当天的可用 lunch/dinner 选项中轮换，并避开已知活动时长重叠
- food-themed walk、museum 或 attraction 不能仅凭 `food` tag 被当作餐厅
- 没有可靠 restaurant 时使用：
  - `Flexible lunch break`
  - `Flexible dinner break`
- flexible break 不伪造 place、coordinates、price、duration、opening hours 或 walking data

这些都是 soft scheduling rules。Meal anchor 缺失或无法安全验证时，不应单独导致 complete-plan validation blocked。

Provider opening hours 仅在能可靠解析 `24/7` 或常见 weekday/time-range 格式时作为
known-open/known-closed 验证；缺失或无法解析时保持 unknown。Dinner 后只有显式带有
nightlife/evening/show/music/sunset/views 等信号、质量足够且仍在当天 cluster 内的地点，
才可能作为 optional evening activity。

当前模型没有 arrival/departure time，因此生成范围内的每一天暂时都被当作 full travel day。将来若增加抵达/离开时间，首尾日需要再单独适配。

## 9. Plan UI 最新展示规则

主要文件：

- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/final.css`

当前展示目标：

- UI 主语言为英文
- Day title 使用英文区域/主题，不直接拼接多个 POI 名称
- route summary 只显示简洁英文景点名称
- API 传递 `is_meal` 和派生的 `meal_type`；PlanFeature 将 meal stop 明确显示为
  `LUNCH` / `DINNER` 独立紧凑 schedule section，
  sightseeing 编号和 route summary 不再把 meal 算作普通 activity
- activity card 以英文名称为主标题
- 有必要且可靠的 local name 时，在英文标题下方以更小、更浅文字显示
- local name 已是相同英文/Latin 名称时不重复显示
- 地址会去掉重复的景点名、城市、国家等尾部信息

不要把英文和当地语言直接拼成类似：

```text
南京海底世界 & Arts & Culture
```

## 10. Trip Cover 与 Place Image

这两个概念必须保持分离：

- Trip Cover：用于 My Trips、Trip overview、Trip card
- Place Image：用于 Plan 内具体 activity

当前 resolver：

```text
trip/src/final/trip-cover.js
```

逻辑：

```text
trip.coverImageUrl / trip.cover_image_url
→ 安全 URL 校验
→ 有值则显示 trip cover
→ 没有则显示 neutral travel cover
```

Trip cover 绝对不能 fallback 到某个 activity/place image，否则 Paris 可能再次显示 Chicago Theatre。
当前已接入 backend-only Unsplash Trip Cover service：

- `backend/.env` 使用 `UNSPLASH_ACCESS_KEY`，前端不接触 key
- `Trip` 保存 cover URL、source、photographer attribution 和 fetch timestamp
- 成功结果永久复用；provider 失败七天后才重试；未配置 key 不写 negative cache
- My Trips 当前 workspace 优先请求并 eager/high-priority 加载，Other Trips 使用 lazy loading
- CDN rendition 分别约为 1000×560 和 360×220，不加载原始大图
- 无结果、API 不可用或 attribution 不完整时继续显示 neutral `Travel cover`
- Place `image_url` / PlanItem `photo_url` 与 Trip cover 保持完全分离

真实 provider smoke test：

```bash
cd /Users/jiayichen/Desktop/cap_stone-main/backend
.venv/bin/python scripts/validate_unsplash_covers.py
```

脚本依次验证 Saint Louis、Paris、Tokyo，且不会输出 access key。当前本机
`backend/.env` 尚未配置 `UNSPLASH_ACCESS_KEY`，因此本轮只完成 mock/provider-contract
测试，不能把 neutral fallback 冒充为真实 Unsplash 验收结果。

## 11. Create Trip 与 Preferences 最新行为

主要文件：

- `trip/src/final/FinalApp.jsx`
- `trip/src/final/TripAppState.jsx`
- `backend/app/api/main.py`
- `backend/app/db/models.py`

Create Trip 真正必填项：

- Trip name · Required
- Destination · Required
- Start date · Required
- End date · Required

日期选择规则：

- past dates disabled
- start date >= 当前本地日期
- end date >= start date
- calendar 默认从当前月份附近开始
- 不写死月份或年份

Preferences 概念分离：

```text
Trip Dates
= organizer 在 Create Trip 设置的公共旅行范围

My Availability
= 当前成员在 Trip Dates 内的个人可用范围
```

Preferences 直接展示继承的 Trip Dates，不再让成员重新定义整个旅行日期。成员选择：

- `Yes, all dates`：availability 保存为完整 Trip Dates
- `No, I have limited availability`：只在 Trip Dates 内选择个人 start/end

当前保存字段为：

- `preferred_start_date`
- `preferred_end_date`
- `available_start_date`
- `available_end_date`

Planner/constraint conversion 已能读取成员 availability。不要另外创建第二套日期系统。

Plan 已存在时保存 Preferences 或增删改 constraint：

- 不修改任何现有 `PlanItem`
- 将 `Plan.needs_refresh` 标为 true
- Current Plan 显示轻量提示，说明 shared plan 仍保持原样
- 后续 generation/replan 继续从数据库读取最新 Preferences/constraints；change proposal 的
  deterministic classification 也继续读取最新 constraints

## 12. Database 与 schema

后端使用 FastAPI + SQLAlchemy + PostgreSQL。运行时数据库由 `backend/.env` 的 `DATABASE_URL` 决定；测试数据库由 `TEST_DATABASE_URL` 决定。

Place Library 使用真实 `place` 表，不要只检查 SQLAlchemy metadata。对现有 runtime database 应运行 additive schema setup：

```bash
cd /Users/jiayichen/Desktop/cap_stone/backend
.venv/bin/python -m app.db.init_schema
```

不要未经确认对共享或 production-like 数据库运行 destructive seed。

常见安全规则：

- `DATABASE_URL` 与 `TEST_DATABASE_URL` 必须不同
- pytest 数据库名必须明显是 test-only
- `backend/.env` 不可提交
- 修改 `.env` 后应重启 backend；已运行进程不会自动可靠地重新加载所有配置

## 13. macOS 本地运行命令

### Backend

```bash
cd /Users/jiayichen/Desktop/cap_stone/backend
.venv/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Health check：

```text
http://127.0.0.1:8000/api/health
```

### Main frontend

```bash
cd /Users/jiayichen/Desktop/cap_stone-main/frontend
npm run dev
```

默认地址通常为：

```text
http://localhost:3000
```

### Standalone Trip workspace

```bash
cd /Users/jiayichen/Desktop/cap_stone/trip
npm run dev
```

### 修改 trip 后同步 embedded preview

只修改 `trip/` 不会自动更新主站 `/trip` 中的静态嵌入内容。必须运行：

```bash
cd /Users/jiayichen/Desktop/cap_stone/frontend
npm run build:trip-preview
```

这会更新 `frontend/public/trip-app/` 中带 hash 的 assets、`index.html` 和 `embed-manifest.json`。旧 hash 文件删除、新 hash 文件新增属于正常 build output。

## 14. 验证命令

### 最近 Planner / Place 定向测试

2026-08-14 本轮最后一次相关回归结果：

```bash
cd /Users/jiayichen/Desktop/cap_stone-main
backend/.venv/bin/pytest -q -p no:cacheprovider \
  backend/tests/test_plan_generation.py \
  backend/tests/test_planner.py \
  backend/tests/test_places.py \
  backend/tests/test_preferences.py \
  backend/tests/test_trips.py
```

结果：

```text
119 passed in 1.95s
```

同一轮使用项目配置的真实 Geoapify key 对 `New York, USA` 做了 provider/runtime 验证：

```text
96+ provider candidates（restaurant neighborhood sampling 后 115）
day pool: 24 sightseeing + 18 food
09:30 Frieda and Roy Furman Gallery
12:00 LUNCH — Sushi Yasaka
14:45 Clark Studio Theater
16:45 Merkin Concert Hall
18:30 DINNER — Ashford & Simpson's Sugar Bar
transition coordinate distances: 0.61 / 0.61 / 0.16 / 0.47 km
flexible meals: 0
```

这只是 Haversine 坐标距离验证，不代表真实 walking time。已知午间关闭的 restaurant 被
排除；营业时间缺失的候选仍保持 unknown。

### 完整 backend tests

```bash
cd /Users/jiayichen/Desktop/cap_stone/backend
DISABLE_SCHEDULER=1 MOCK_AI=1 .venv/bin/python -m pytest -q
```

### Frontend / embedded Trip

```bash
cd /Users/jiayichen/Desktop/cap_stone/frontend
npm run build:trip-preview
npm test
```

### Diff hygiene

```bash
cd /Users/jiayichen/Desktop/cap_stone
git diff --check
```

注意：unit tests 通过不等于真实 provider/runtime 流程通过。涉及 Geoapify、PostgreSQL 或 DeepSeek 的任务，完成标准应包含真实 runtime database、真实 endpoint response 和 backend traceback 检查，但绝不输出 secret。

## 15. 常见问题

### `/trip` 没显示刚修改的 Trip UI

通常是 embedded preview 过期。运行 `frontend/npm run build:trip-preview` 对应的命令，不要直接编辑生成的 bundle。

### 前端显示 `Could not reach the backend`

这个文案有时也会掩盖 backend `500`。先检查 uvicorn terminal traceback 和实际 response body，不要直接假设是网络错误。

### Plan 页面显示通用 blocked 文案

检查 `POST /api/trips/{id}/plans/generate` 返回的真实 `blocked_reason`。UI 中的 date-range 建议可能只是 fallback，不一定是真实根因。

### Geoapify 返回数据但 Planner 没 candidates

依次检查：

```text
Geoapify response
→ normalization
→ place upsert / transaction commit
→ cached city query
→ English-name eligibility
→ PlannerPlace conversion
→ deterministic constraint validation
```

unknown metadata 不能仅因为未知就被判 invalid；只有 hard constraint 无法安全验证时才可以拒绝该 candidate。

### Backend 修改后行为仍旧

确认 uvicorn 使用 `--reload`，并在 `.env` 修改后主动重启 backend。

## 16. 尚未完成或仍有风险

- 当前工作区尚未整理成清晰 commits
- 需要在提交前跑一次更完整的 backend/frontend test suite
- 最新 meal-anchor scheduling 已通过定向测试，但尚未在本次交接中重新生成一个真实 Geoapify trip 做浏览器端验收
- Planner AI quality 仍依赖 provider response；deterministic validation/fallback 必须继续保留
- Geoapify 的 English/local name 覆盖不完整，不能靠硬编码翻译任意地点
- Trip cover 目前只有显式 URL 或 neutral fallback，尚未接 city cover provider
- Preferences 当前表达的是一个连续 availability range，不支持多个离散可用日期区间
- arrival/departure time 尚未建模，首尾日仍按 full day 处理
- Alembic migrations 尚未建立，当前依赖 additive `init_schema`
- Durable Chat history 已持久化并完成生产 Context Recovery 验收；其浏览器 UI
  体验、用量监控、rate limiting 和硬性 token/cost budget 仍是后续产品/运营工作
- magic-link 和 guest-to-account binding 仍未完全完成
- automatic trip state transitions 尚未完全完成
- deeper `frontend` / `trip` runtime merge 仍暂停

## 17. 推荐下一步

优先级建议：

Release B 已关闭后，后续工作不应重新打开 AI 基础设施发布范围。建议独立排期：

1. 产品：优化 AI Chat UI、Conversation History、Suggestion 展示与 Plan Assistant
   协作流程。
2. 运营安全：增加 Provider Usage Monitoring、rate limiting、严格 token/cost
   budget 与真实用户上线前的滥用防护。
3. Planner 产品验证：使用真实 runtime 配置重新生成至少一个非 Chicago trip，记录：
   - Geoapify candidate count
   - PostgreSQL cached count
   - Planner final candidate count
   - 每日 sightseeing / meal anchors
   - generate HTTP status 与 blocked reason
   - GET plan activities
4. 浏览器检查 My Trips、Create Trip、Preferences 和 Plan 页面。
5. 历史 Ollama AWS 清理仅在独立批准、建立 DeepSeek-only Runtime-off 回滚基线后进行；
   它不是 Release B 阻断项。

## 18. 最后原则

- 视觉问题改 UI
- destination policy 从 `shared/trip-navigation-policy/` 开始
- session/header/storage 从 `shared/session-runtime/` 开始
- Plan interaction 保持在 `plan-feature/` 边界内
- Planner 只做最小、可验证的 scheduling 修改
- unknown 永远不等于 fake default
- 修改 `trip/` 后同步 embedded preview
- 真实 runtime 问题必须看真实 response、database 和 traceback，不能只依赖 mocks
