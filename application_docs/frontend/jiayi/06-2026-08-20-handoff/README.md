# TripSync / Cadensy 今日交接文档

日期：2026-08-20

本文件记录今天在 Trip workspace 中完成、验证和遗留的修改。当前工作区仍有未提交改动；本文档以当前代码为准，不代表可以直接覆盖其他人的本地修改。

## 1. 工作范围

今天的工作集中在两条主线上：

1. Trip workspace 的视觉、交互和页面状态修正。
2. Destination normalization 与 Generate Plan 真实运行链路排查。

核心原则保持不变：

- 不修改 auth、login、profile bootstrap、membership/session、localStorage、routing、iframe 和 API base URL。
- 不改变 Planner / Chat Agent 的业务边界，也不绕过 Apply / Vote / Confirm 流程。
- 优先复用既有 state、handler、API 和数据结构。
- UI 修改以 incremental refinement 为主，不重构整个 FinalApp。

## 2. 今日修改总览

### 2.1 Plan workspace

- 保留 warm paper、muted navy、Serif + Sans、低阴影和 subtle border 的视觉基线。
- 清理 Plan 顶部重复 Trip dashboard 信息，让 Shared Itinerary 和 itinerary timeline 成为主要内容。
- Shared Itinerary 内移除 Live plan、Edit preferences、Ask Cadensy 操作按钮。
- 移除 Shared Itinerary 内所有 line-art、route line、纹理和装饰背景，改为干净的 warm off-white surface。
- 将 Trip collaborators 从大面积独立卡片改成轻量 collaborator strip：
  - Collaborators
  - avatar / initials
  - Manage members →
- 普通 activity row 使用统一的极淡 secondary surface，避免整页一片白。
- Meal row 保留极轻的 warm sand 区分，避免黄色大色块。
- Day header、activity row、meal row、transport row 使用不同但克制的 surface hierarchy。
- 移除 Day header 下方冗余的 route summary / Show on map row，使 itinerary 直接从 Day header 进入第一条 activity。
- 保留时间、timeline、place category icon、collapse、overflow menu、map 和现有 item interactions。

### 2.2 Map day tabs 与 blank page 修复

- 继续使用原有 `railDay`、`showAllOnMap`、`showDayOnMap(day.id)` 和 `railDays`。
- 没有创建第二套 selected-day state。
- 移除按 `view.railDay` 强制 remount Map 的 key，避免切换 Day 时销毁并重新创建 Leaflet map。
- 提高 day tab row 的层级，明确设置 `pointer-events: auto`、`z-index`、`isolation` 和 pointer cursor，避免被地图图层覆盖。
- 保留 marker 的 pointer events。
- 修复选择单日后才触发的 `totalRouteMiles` 缺失问题，避免 Day 1 / Day 2 / Day 3 / Day 4 点击后整页空白。
- 保持 Map 数据、POI 数据、地图组件和地图 library 不变。
- 选择 map day 后继续同步左侧 itinerary 的 day 展开状态。

### 2.3 跨页面 workspace 统一

Plan、Chat、Updates、Preferences、Members、Invite 继续保留各自功能，但复用同一套视觉语言：

- page background
- primary / secondary surface
- subtle border
- restrained radius
- low shadow
- muted text
- Serif 页面标题和 Sans metadata / controls
- 一致的 workspace gutter、section spacing 和 button treatment

同时移除 Chat、Updates、Preferences、Members、Invite 顶部重复的完整 Trip dashboard 信息，让每个页面进入后直接展示自己的核心任务。

### 2.4 Preferences post-plan 状态

Preferences 的可编辑状态由是否已经生成有效 itinerary item 决定：

- 没有 plan，或 plan 没有实际 items：仍然可编辑、可保存。
- 当前 plan 已经有实际 itinerary items：进入只读状态。

生成后：

- 隐藏 `Save preferences`。
- Budget、visibility、pace、interests、non-negotiable 等控件只读。
- 保留 Preferences tab，允许查看生成 Plan 时使用的偏好。

不要把“没有 plan”误当成只读条件。正确判断不能简单写成 `plan === null` 或 `hasPlan === false` 就禁用表单。

### 2.5 Itinerary item actions 与 booked 状态

生成后的 itinerary overflow menu 移除了：

- Move to another day
- View details

保留现有合理操作，例如：

- Edit time
- Replace place
- Mark as booked / Remove booked status
- Remove from plan

Booked item 继续使用原有 backend settledness 语义，并在前端通过整条 activity row 的 `booked` 状态加强视觉确认感，而不仅仅显示一个弱 badge。

### 2.6 Add a stop

保留已有的 insertion flow：

1. 用户点击两个 itinerary item 之间的 `+ Add stop`。
2. runtime 记录当前 day、previous stop、next stop 和 insertion position。
3. 打开既有 Add a stop modal。
4. 确认后调用既有 backend 创建路径。
5. backend 成功后刷新当前 day itinerary。

Modal 文案已统一为：

- 标题：`Add a stop`
- helper：`Add a place or activity between these two stops.`
- label：`What would you like to add?`
- placeholder：`Search for a place or enter an activity`
- primary action：`Add to plan`

插入位置显示为：

`Between   9:00 AM → 10:30 AM    Day 2`

不要把新 item 直接 append 到 day 末尾。后续如继续增强 schedule validation，应继续由 backend 负责最终合法性校验，前端只做轻量、确定性的 warning。

### 2.7 Explain 输出

Explain 现在应只解释当前选中的 itinerary item，不解释它为什么安排在当前位置，也不总结前后 stop 或整天行程。

优先展示已有字段，按小区块组织：

- Schedule：日期、开始时间、结束时间
- Details：地点类型、地址、预计时长、description 等已有值
- Status：Booked、Not booked、Existing reservation 等

缺失字段不显示，不猜测，不补充虚构内容。前端 Plan drawer 使用结构化字段渲染，backend chat/explainer payload 保持当前数据边界。

### 2.8 Destination normalization 与 Generate Plan

已存在的 normalization 层继续作为统一入口，至少支持：

```text
la / LA            -> Los Angeles
los angeles        -> Los Angeles
nyc / NYC          -> New York City
dc / DC            -> Washington, D.C.
```

今天重点不是继续添加 alias，而是排查 LA 真实链路：

```text
raw destination
-> normalize
-> Geoapify geocode
-> Geoapify places
-> PostgreSQL/cache
-> category/quality filtering
-> planner candidates
-> Generate Plan
```

Generate Plan 时会再次 normalize legacy Trip 的 destination，避免数据库里已有 `la` 的旧 Trip 继续失败。

Geoapify 结果使用 canonical city 作为搜索中心和缓存归属，但不会要求每个 place 的 locality / district / suburb 必须与 `Los Angeles` 完全相等。

错误分类已区分：

- `DESTINATION_NOT_FOUND`
- `NO_PLACE_CANDIDATES`
- `CONSTRAINTS_BLOCKED`

前端不再把所有 Generate Plan 失败都显示成 requirements blocked。

## 3. LA 真实链路验证记录

使用已有 destination 为 `la` 的真实 Trip，调用当前运行中的 backend Generate Plan endpoint。

### 3.1 Geocoding

```text
raw destination:       LA
normalized destination: Los Angeles
geocoded city:          Los Angeles
country:                United States
latitude:               34.0536909
longitude:              -118.242766
HTTP status:            200
```

### 3.2 Provider、cache、filter 和 planner

```text
Geoapify places returned:       80
cached places after provider:  80
after category/quality filter: 77
final planner candidates:      77
```

### 3.3 真实 Generate Plan endpoint

```text
HTTP status: 200
plan status:  active
days:         3
items:        13
cached LA rows in PostgreSQL: 80
```

数据库已确认该 Trip 的 destination 被持久化为 `Los Angeles`，Plan status 为 `active`。

### 3.4 问题定位

原先数据库中存在旧的 blocked Plan 记录，原因是旧运行结果保存为 `NO_PLACES`。当前真实 backend 重启后，Geoapify、PostgreSQL/cache、过滤和 Planner 均能返回有效数据。

另外，脱离应用启动顺序直接 import Geoapify 模块时，dotenv 尚未加载，会误报 `GEOAPIFY_API_KEY is not configured`。真实 Uvicorn runtime 会通过应用启动路径加载 backend `.env`，本次已确认 key 正常加载，且真实请求返回 200。

## 4. 主要文件索引

### Backend

- `backend/app/domain/places/geoapify.py`
  - Geoapify geocode / places 请求
  - destination-found metadata
  - provider response 解析
- `backend/app/domain/places/service.py`
  - `normalize_destination`
  - cache 查询
  - provider places 读取
  - planner candidate filtering
- `backend/app/domain/plans/generator.py`
  - Generate Plan orchestration
  - destination re-normalization
  - blocked reason / code mapping
- `backend/app/domain/trips/service.py`
  - Create Trip 时保存 canonical destination
- `backend/app/api/main.py`
  - Generate Plan API 返回 `blocked_code`
  - existing itinerary and item endpoints
- `backend/app/domain/chat/service.py`
  - structured Explain / item detail payload 相关逻辑

### Trip frontend

- `trip/src/final/FinalApp.jsx`
  - workspace shell、Preferences read-only 状态、跨页面 content shell
- `trip/src/final/TripAppState.jsx`
  - trip / plan / preferences state、API handlers、booked 状态更新
- `trip/src/final/final.css`
  - shared visual tokens、页面 surface、Plan row、map tab、modal 和 state styling
- `trip/src/final/plan-feature/PlanFeature.jsx`
  - Plan timeline、Add a stop、Explain、blocked state、map rail、item menu
- `trip/src/final/plan-feature/usePlanInteractionRuntime.js`
  - Plan item interaction、booked、menu、drawer、day selection 等运行时逻辑

### Embedded preview

`/trip` 实际使用的嵌入资源位于：

- `frontend/public/trip-app/index.html`
- `frontend/public/trip-app/embed-manifest.json`
- `frontend/public/trip-app/assets/`

修改 `trip/src` 后必须重新执行 preview build，否则 `/trip` 可能继续显示旧 bundle。

## 5. 启动方式

### Backend

```bash
cd /Users/jiayichen/Desktop/cap_stone/backend
./.venv/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

当前本轮验证时 backend 使用：

```text
http://127.0.0.1:8000
```

### Trip frontend

```bash
cd /Users/jiayichen/Desktop/cap_stone/trip
npm run dev
```

默认地址：

```text
http://localhost:3000
```

### 同步 `/trip` 嵌入预览

```bash
cd /Users/jiayichen/Desktop/cap_stone/frontend
npm run build:trip-preview
```

## 6. 今日验证结果

已通过：

- frontend `npm run build`
- `git diff --check`
- 相关 backend 文件 `py_compile`
- focused Geoapify regression test：`1 passed`
- 真实 Geoapify geocode 请求：HTTP 200
- 真实 backend Generate Plan endpoint：HTTP 200 / active
- 真实 PostgreSQL cache 写入和读取：Los Angeles 80 条

全量 backend pytest 在受限环境中无法完整运行，错误是 PostgreSQL 连接受到环境权限限制：

```text
connection to server at 127.0.0.1, port 5432 failed: Operation not permitted
```

这不是 Geoapify 空结果，也不是候选过滤结果。真实 backend 进程和真实 endpoint 在允许访问 PostgreSQL 的运行环境中已完成验证。

## 7. 继续开发时的注意事项

1. 不要把 `plan === null` 直接当作 Preferences read-only 条件；首次生成前必须可编辑和保存。
2. 不要给 Map 创建第二套 selected-day state，也不要恢复按 day 强制 remount 的 map key。
3. 不要只修改 `trip/src` 后直接判断 `/trip` 没有变化；先同步 `frontend/public/trip-app`。
4. 不要将 provider error、destination not found、无候选地点和 constraints blocked 合并成同一个 UI 错误。
5. 不要要求 Geoapify place 的 locality 与 canonical city 完全字符串相等。
6. 不要把 Add a stop 新 item append 到 day 末尾；必须沿用 previous / next stop 和 insertion position。
7. Explain 不应回到长段自然语言，也不应解释前后 stop 或整天安排原因。
8. 不要删除或覆盖现有 session/localStorage 数据来解决 Trip 空白页。
9. 继续遵守 `docs/AGENTS.md`、`trip/BACKEND.md` 和 `docs/PRODUCT.md` 中关于 agent、隐私、决策路径和 API 边界的规则。

## 8. 待后续确认

- 在用户当前 Chrome session 中手动确认 `/trip`、Map Day tabs、Preferences 生成前编辑和生成后只读状态。
- 若继续改 Add a stop，补充确定性的 schedule warning 测试，但不要新增复杂 AI rescheduling 流程。
- 若继续扩展 Destination，优先从已有 normalization 和 canonical geocode 结果复用，不要分散出第二套 alias mapping。
