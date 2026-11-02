# Chat Agent / Planner 观察到的问题

最后更新：2026-08-16

## 背景

这份文档记录在 Plan 页面、Preferences 页面、Plan drawer Chat Agent 和
Planner 生成流程里，从真实用户视角观察到的问题。

最初测试场景是洛杉矶行程里的一个计划项：

```text
Can we replace this with something more relaxing?
```

当时选中的 item 是：

```text
Los Angeles Times Globe Lobby
Day 1, 9:00 AM
```

## 已观察到的问题

### 1. 非 Chicago replacement 曾受城市限制，现已修复

最初根因是 Chat Agent 的 replacement tool 仍停留在旧的 Chicago-only curated 路径：

```text
backend/app/agents/tools.py::find_replacement_place
-> data/poi_chicago.py
-> unsupported_destination for non-Chicago trips
```

而 Planner 侧实际已经走的是共享 Place Service：

```text
backend/app/domain/places/service.py
-> PostgreSQL place cache
-> Geoapify provider
```

因此问题不是 planner 没有外部 place source，而是 replacement 没复用现有 Place Service。

2026-08-15 已完成修复：

- 去掉了 Chicago-only replacement restriction。
- `find_replacement_place` 现在复用 backend Place Service / Geoapify path，而不是第二套 places architecture。
- replacement candidates 现在会：
  - 保持与当前 trip destination 一致
  - 排除当前 item 自己
  - 排除 Current Plan 已使用地点
  - 只返回真实 provider/cache 结果
  - 带稳定 `candidate_id`
  - 要求 coordinates 存在
  - opening hours 已知且不覆盖当前 time block 时会排除
  - 支持 `relaxing`、`park`、`cafe`、`viewpoint`、`museum` 这类简单 keyword/category intent
  - 优先返回距离当前 item 较近的候选
- `price_per_person` 现在允许 `null/unknown`，不再因为 provider 没价格就拒绝真实地点。
- deterministic safety 保持不变：AI 仍然只能从 tool 真正返回过的 candidates 里选，hallucinated venue 不能进入 `ProposedChatChange`。

真实 non-Chicago validation：

```text
Date: 2026-08-15
Trip destination: Los Angeles, USA
Current item: Los Angeles Times Globe Lobby
User: "Can we replace this with something more relaxing?"
```

验证结果：

- Agent tool sequence:

```text
get_current_plan
-> find_replacement_place
-> classify_change
```

- tool 通过真实 Geoapify-backed Place Service 返回了 Los Angeles candidates。
- 返回结果包含真实 `candidate_id`，并能在 `place` 表中找到对应 provider row。
- Agent 只从 tool 返回的 candidates 中选择。
- 成功形成合法 `ProposedChatChange`。
- `Apply` 前 Current Plan 保持不变。

当前状态：

- Observed Issue #1 已修复。
- replacement path 不再保留 Chicago-only special case。

### 2. 数字选项回复曾会映射到错误选项，现已修复

问题根因已经确认：follow-up option selection parser 原本只认这几类显式格式：

```text
Option 2
option b
the second one
```

但不认用户单独回复一个 bare number：

```text
2
```

因此当 assistant history 里已经有 candidate options 时，用户只回 `2` 不会进入稳定的 history-option selection 路径，而会漏回到后续 agent / semantic 路径。这就是为什么：

- `Option 2.` 能工作
- `I'll take the second one.` 能工作
- 粘贴完整 option 文本也能工作
- 但单独回复 `2` 会不稳定，甚至可能跑偏

2026-08-16 已完成修复：

- bare numeric reply 现在会被当成显式 option index 解析。
- `2` 现在稳定映射到第二个 candidate option。
- 这个修复保持在最小范围内，只扩展 pure-number follow-up，不改变已经工作的 `Option 2`、ordinal 和 full-text selection 行为。

用户影响：

- 用户现在可以直接回复 `2`，不用再补完整句子。
- follow-up selection 会优先走 stable option order / identity，而不是回退到自然语言重猜。

当前状态：

- Observed Issue #2 已修复。

### 3. 粘贴完整选项文本本来就能工作，这证明坏的不是 classify_change

当数字回复选错后，用户粘贴完整文本：

```text
2. Move it to 9:15 AM — shift the start slightly later on the same day, where
nothing else is booked.
```

助手随后能正确准备 time-change proposal：

```text
Current: 9:00 AM
Proposed: 9:15 AM
Impact: NOTICE
```

这个现象说明底层 change classification 一直是通的，真正不稳定的是 compact follow-up selection。

结论已经明确：

- Issue #2 和 Issue #3 不是两个独立故障。
- 它们本质上是同一个 seam：

```text
history candidate options
-> follow-up option selection parsing
-> stable option identity / order
```

- 不是 `classify_change` 本身坏了。

2026-08-16 的修复后，以下几类 follow-up 都有回归覆盖：

- `2`
- `Option 2.`
- `I'll take the second one.`
- 完整 option 文本

验证结果：

- `backend/tests/test_chat_agent_branch.py`: `43 passed`
- `backend/tests/test_chat.py`: `10 passed`

当前状态：

- Observed Issue #3 已通过和 Issue #2 同一修复一起解决。

### 4. Apply 显示 backend reachability error，但真实原因是后端异常

9:15 AM proposal 准备好后，UI 显示：

```text
I could not reach the backend. Try again in a moment.
```

本地检查显示 backend 当时可达：

```text
GET http://127.0.0.1:8000/api/health -> {"ok": true}
```

进一步复现 Rosalind's Ethiopian Restaurant 的 time-change card 时发现：

```text
POST /api/plans/items/9e1e8448614441a18437f0ab02993503/changes
HTTP/1.1 500 Internal Server Error

TypeError: propose_change() got an unexpected keyword argument 'alternatives'
```

失败路径：

```text
backend/app/api/main.py submit_change()
-> orch.propose_change(..., alternatives=alternatives)
```

但 `backend/app/domain/decisions/orchestrator.py` 里的 `propose_change()` 当前没有 `alternatives` 参数。

用户影响：

- 用户看到的是“连不上后端”，但真实情况可能是后端 500。
- UI 没区分网络失败、后端 500、已知 JSON error。
- 即使助手正确分类为 `NOTICE`，Apply 仍可能失败。

预期行为：

- Apply 失败时应尽量显示更具体的错误。
- 本地测试出现该文案时，应检查 uvicorn/backend traceback。
- backend API 和 decision orchestrator 的函数签名应保持一致。

Status update, 2026-08-15:

- Fixed in `backend/app/api/main.py` and `backend/app/domain/decisions/orchestrator.py`.
- `submit_change()` still validates frontend `options` into backend-safe `alternatives`.
- `orch.propose_change()` now accepts `alternatives`.
- Alternatives are only used for `ROUND` and `REOPEN_ROUND` decision paths.
- `NOTICE` and `CONFIRM` paths still ignore alternatives, so the existing mutation boundary remains unchanged.
- The related time-window constants bug in `_validated_change_options()` was also fixed by importing `DAY_START_HOUR` and `DAY_END_HOUR` from `backend/app/agents/tools.py`.
- Regression coverage was added in `backend/tests/test_trips.py`.
- Additional vote-card cleanup: if the submitted `requested` patch and an assistant alternative patch are identical, the duplicate alternative is filtered out so the vote UI does not show two identical choices.

### 5. 错误文案会掩盖服务端失败

前端会把 missing status 和 500-level failures 映射成宽泛的 backend reachability 文案。

相关前端位置：

```text
trip/src/final/plan-feature/useAssistantChangeRequestFlow.js
trip/src/final/TripAppState.jsx
trip/src/final/FinalApp.jsx
```

用户影响：

- 服务端异常看起来像网络断了。
- 只看 UI 很容易误判问题方向。

预期行为：

- 面向用户的文案可以简洁，但本地日志/开发诊断应保留细节。
- 出现该文案时应检查 backend terminal。

Status update, 2026-08-15:

- Fixed in:

```text
trip/src/final/plan-feature/useAssistantChangeRequestFlow.js
trip/src/final/TripAppState.jsx
trip/src/final/FinalApp.jsx
```

- Missing HTTP status / network failures still use reachability copy.
- HTTP 500-level backend failures now use backend-error copy instead of reachability copy.
- This keeps user-facing text short, but gives developers the correct debugging direction: check backend logs rather than assuming the browser could not connect.

### 6. 偏好更新后没有明确的整计划更新入口

计划已经生成后，用户修改 preferences，会看到类似提示：

```text
Preferences updated
Your current plan was generated using earlier preferences and has not been
changed. Future replans and change proposals will use the latest planning inputs.
Review ->
```

但 Plan 页面没有明确动作让用户 regenerate 或 update 当前 itinerary。

用户影响：

- 用户知道 preferences 改了，但不知道如何让当前 itinerary 应用这些变化。
- 产品说 future replans 会用新输入，但没有把 replan 路径显性展示出来。
- 用户可能期待一个 whole-plan update confirmation flow，却只看到被动提示。

预期行为：

- stale-plan banner 应提供明确动作，比如 `Regenerate plan`、`Update itinerary`、`Review and replan`。
- 应说明当前 plan 在用户确认前不会改变。
- 如果 whole-plan regeneration 还不支持，UI 应直接说明，并引导用户使用已支持的 change proposal 流程。

### 7. Required planning inputs 的真实问题已确认，并已在 supported 路径上修复

这条问题后来确认不是 planner 完全不执行 required constraints。

backend planner 本来就会对**结构化的 required constraints**做 enforcement：

```text
backend/app/domain/plans/generator.py
-> _load_required_constraints(...)
-> _build_day(...)
-> _validate_items(...)

backend/app/domain/constraints/engine.py
-> violates(...)
```

真正的问题有两个：

1. 用户看到的是 `Required`，但系统真正执行的是 `kind + params` 这类结构化约束。
2. 在 2026-08-16 之前，constraint save path 几乎只校验 `kind`，没有校验 `params`
   是否真的可执行。

这会导致一种很危险的假象：

```text
original_text: "Required: avoid museums"
kind: avoid_tag
params: {}
```

这种输入在旧代码里也能保存，但 planner 实际拿到的是一个空约束，因此不会生效。

另外，前端 `avoid_tag` picker 当时也没有 `museum` 入口，所以用户想表达：

```text
Required: avoid museums
```

没有一个明确、可执行的结构化路径。

2026-08-16 已完成修复：

- backend 现在会校验六种 constraint 的 `params` 是否真正可执行。
- 空的或无效的 required constraint 不再允许保存。
- `original_text` 仍然只作为私有表述，不再被误当成 planner 可执行语义。
- 前端提示改成更准确的：

```text
Only these structured selections are checked against the plan.
```

- 前端 `avoid_tag` 现在提供 `museum` 入口。
- 前端在提交前也会阻止明显不可执行的 draft，例如：
  - 空 `avoid_tag`
  - 空 `dietary`
  - 没有起止的 `date_range`
  - 反向 `time_window`

当前正确心智：

- 被 planner 检查的是**结构化 required selections**，不是任意 free-text 原话。
- 如果用户只写一句自然语言，但没有落成结构化参数，系统现在会阻止保存，而不是假装它会被执行。
- 对已经支持的 required 类型，planner 继续执行并在必要时 block generation。

回归覆盖：

- `backend/tests/test_preferences.py`
  - 空 / 不可执行 constraint 会被拒绝
  - 反向 time window 会被拒绝
- `backend/tests/test_trips.py`
  - constraints API 会对空 required payload 返回 `422`
- `backend/tests/test_plan_generation.py`
  - `avoid_tag = museum` 的 required constraint 会被真实执行
  - generated items 仍通过全部 required constraints

当前状态：

- Observed Issue #7 的 supported path 已修复。
- planner 不是把 hard requirement 当 soft preference。
- 产品现在会更诚实地区分：
  - 可执行的 structured required
  - 只是用户原话、但还没变成 planner 语义的内容

### 8. Limited availability 之前只被当成 soft signal，现已在 planner generation 路径修复

这条问题后来确认不是 persistence 丢失。

在 2026-08-16 修复前，limited availability 的真实状态是：

- 前端能保存 `available_start_date` / `available_end_date`
- 重新打开 Preferences 也能读回并恢复 `limited` 选择状态
- 但 planner generation 并没有把它当成 hard date gate

真正的根因在 backend planner：

```text
backend/app/domain/plans/generator.py
-> _load_availability_by_date(...)
```

旧逻辑只是把 partial availability 当成：

```text
"this day should be lighter"
```

也就是：

- 某一天如果不是所有已提交成员都 available
- planner 只把当天 sightseeing count 降到 `MIN_DAY_ACTIVITIES`
- 但不会把这一天从可生成日期里移除

所以用户在 Preferences 里选了：

```text
No, I have limited availability
```

在旧代码里仍可能看到自己不可用日期上的 itinerary items。

2026-08-16 已完成修复：

- saved limited availability 现在会真正收窄 planner generation 的可生成日期。
- planner 现在只会在**所有已提交 availability 的成员都 available** 的 trip dates 上生成 items。
- day filtering 保留真实 trip `day_index` / `day_date`，不会把剩余日期重新编号成错误的 Day 1/Day 2。
- 如果所有已提交 availability window 没有任何交集，generation 现在会直接 blocked，而不是生成一个违规 itinerary。
- Preferences save / reload path 保持不变：
  - `available_start_date`
  - `available_end_date`
  仍然会保存并回读。

实现后行为：

- 2026-08-16 之前：
  - limited availability 只是 soft lighter-day signal
  - planner 仍可能在 unavailable dates 生成 items
- 2026-08-16 之后：
  - limited availability 成为 generation-time hard date filter
  - unavailable dates 不再出现在生成 items 里

额外修正：

- 在把可生成日期缩窄后，planner 的 completeness check 还残留一个旧假设：

```text
allowed day indexes must be 1..N
```

这会把只剩 Day 2 / Day 3 的合法 itinerary 误判成 incomplete。

该问题也已一起修复：

- completeness 现在按实际允许生成的 trip day slots 检查
- 不再要求可生成 day index 必须连续从 1 开始

回归覆盖：

- 已有 persistence/readback coverage 继续证明 availability 会保存：
  - `backend/tests/test_trips.py`
- 新 generation coverage：
  - limited availability 只在 shared available dates 生成
  - disjoint availability 直接 blocked
  - existing required-constraint generation checks 保持通过

验证结果：

- `pytest backend/tests/test_trips.py -q -k "preference_dates_are_saved_and_read_back or preference_dates_outside_trip_window_are_rejected_for_any_role"`
  - `2 passed`
- `pytest backend/tests/test_plan_generation.py -q -k "generated_items_pass_every_required_constraint or saved_limited_availability_limits_generation_to_shared_dates or disjoint_limited_availability_blocks_generation"`
  - `3 passed`

当前状态：

- Observed Issue #8 已在 planner generation 路径修复。
- limited availability 不再只是 UI 可见项。
- save / reload / generation 现在已经形成端到端链路。

### 9. 初次生成后再修改 preferences，没有 replan / preview 动作

如果用户第一次生成 itinerary 前填写 preferences，生成结果可以反映这些 preferences。但如果 itinerary 已经存在，用户后续再改 preferences，当前 plan 不会更新。UI 只显示 preferences changed 的提示，却没有明确按钮让用户 regenerate、preview differences 或 apply new preferences。

观察到的行为：

```text
1. User creates or opens a trip.
2. User fills preferences.
3. User generates an itinerary.
4. User later changes preferences.
5. The app shows a banner / notice that preferences were updated.
6. The itinerary stays the same.
7. There is no obvious action such as "Regenerate with new preferences" or
   "Preview updated plan".
```

用户影响：

- 用户可能以为 preference edits 坏了，因为 itinerary 没有变化。
- 产品告诉用户 preferences changed，但不给下一步。
- 用户不知道新 preferences 只影响未来小改动、未来 full replan，还是完全不影响。
- 生成前 preferences 很“有用”，生成后却变成被动提示，体验不一致。

预期行为：

- post-generation preferences 改变后，Plan 页面应显示明确动作，例如 `Regenerate with new preferences`、`Preview updated plan`、`Review replan`。
- 应解释当前 itinerary 在用户确认前保持不变。
- 如果 full replanning 未支持，UI 应明确说明 changed preferences 会影响什么。
- 用户应能在替换 itinerary 前比较 old vs new plan impact。

用户视角测试方法：

1. 创建 fresh trip。
2. 填写一组明确偏好，例如：

```text
Top interests: Culture
Preferred pace: Packed
```

3. Generate itinerary，并截图或记录生成内容。
4. 回到 `Preferences`。
5. 改成明显不同的偏好，例如：

```text
Top interests: Nature / Relaxed
Preferred pace: Slow
Required: start after 11 AM
```

6. 保存 preferences。
7. 回到 `Plan`。
8. 检查页面是否提供显性动作，让用户用新 preferences 更新当前 plan。
9. 检查 itinerary 是变化、带清楚解释地保持不变，还是无下一步地保持不变。

通过标准：

- App 明确说明现有 plan 已 stale。
- 用户有明显动作可以 regenerate 或 preview 新 plan。
- 保存新 preferences 后，用户不会被卡在只有提示没有操作的状态。

失败标准：

- App 只显示被动 notice，没有 replan / preview action。
- 用户不知道如何让新 preferences 影响 itinerary。
- 现有 plan 不变且没有明确下一步。

### 10. Item-scoped Ask Cadensy 曾会丢失 selected item context，现已修复

问题根因已经确认，而且根因在 backend reference-resolution，不是前端没有传 `item_id`。

前端实际一直都有把 item-scoped drawer 的 selected item 传给 chat API：

```text
trip/src/final/plan-feature/useAssistantChangeRequestFlow.js
-> app.chatWithTrip({ message, itemId, history })

trip/src/final/TripAppState.jsx
-> POST /api/trips/{tripId}/chat
-> { message, item_id, history }
```

真正出错的是 backend 对 selected item 的使用条件太窄：

- 它会把 selected item 自动用于 `this` / `it` / `this one` 这类 pronoun request
- 但 `move to 4pm` 这种没有代词、却明显是 item-relative change request 的短句
  不会自动绑定到当前 selected item
- 结果会被误判成缺少 item reference，然后回复：

```text
I don't see that item in the Current Plan yet. Which item do you mean?
```

2026-08-16 已完成修复：

- backend 现在在已经有 `item_id` 的前提下，会把 selected item 也用于这类 item-relative short request：
  - `move to 4pm`
  - `move to tomorrow`
  - `make this shorter`
  - `replace ...`
  - `remove ...`
- 修复保持在很小范围内：
  - 只在已有 selected item 且消息看起来像 item-relative change request 时触发
  - 不会覆盖显式提到另一个 item 的情况
  - stale selected item 仍然安全失败
  - 模糊 generic reference 仍然会要求澄清

用户影响：

- item-scoped drawer 里不再需要为了 `move to 4pm` 这类请求重复 item 名称。
- Drawer header 和 backend-selected item 现在能保持一致。

验证结果：

- 新增 regression coverage：
  - `backend/tests/test_chat_agent_branch.py`
  - `backend/tests/test_chat.py`
- 相关通过结果：
  - `backend/tests/test_chat_agent_branch.py`: `44 passed`
  - `backend/tests/test_chat.py`: `11 passed`

当前状态：

- Observed Issue #10 已修复。

## 当前判断

Chat Agent V1 的高层架构方向仍然是对的：

```text
AI proposes.
Backend rules decide.
Humans apply.
```

现在观察到的问题更具体：

- non-Chicago replacement 已修复，真实 Place Service / Geoapify path 已接通。
- bare-number option follow-up 已修复，history option selection 现在能稳定处理 `2` / `Option 2` / ordinal / full text。
- Apply failure 文案过于笼统。
- Apply endpoint 把 `alternatives` 传给了当前不接受该参数的 orchestrator 函数。
- backend/server logs 是定位 Apply 失败的必要信息。
- Vote option cards now render the concrete after-state of a proposed patch, and duplicate assistant alternatives are filtered when they are identical to the submitted proposal.
- preferences 更新后会让 plan stale，但缺少 whole-plan update action。
- required structured constraints 已确认会被执行；空或不可执行的 required 输入现在会被拒绝保存。
- limited availability 已在 planner generation 路径接通为真实日期过滤。
- 初次生成后再改 preferences，会 stale 但没有 regenerate / preview action。
- item-scoped `Ask Cadensy` selected-item context 已修复，短的相对修改请求现在会绑定到当前 selected item。

## 建议修复优先级

1. Preferences 改变后添加明确 stale-plan action，或说明只有 future replans/change proposals 会使用新 preferences。
2. 增加 post-preference-edit replan flow：existing itinerary 上 preferences 改变后显示 regenerate / preview action。
3. 继续保持已修复项的 regression coverage：
   - non-Chicago replacement
   - compact option selection
   - Apply alternatives / diagnostics
   - structured required constraints
   - limited availability generation filtering
   - item-scoped selected-item context

## 2026-08-16 补充：`change time` 澄清回复暴露内部推理，且 Markdown 未渲染

观察场景：

```text
Cadensy:
Ask me about this item, or tell me a change in your own words. I will check it first and show exactly what would be submitted.

User:
change time
```

当时是在 item-scoped `Ask Cadensy` 中操作，选中的 item 是：

```text
Gloria Molina Grand Park
item id: c6618460949a425ea061b38f07dedc91
current start: 10:00 AM
date: 2026-09-29
```

实际看到的回复：

```text
The traveler wants to change the time of "Gloria Molina Grand Park" (item id c6618460949a425ea061b38f07dedc91), which currently starts at 10:00 AM on 2026-09-29. The user hasn't specified a new time. I need to ask what time they'd like to move it to. Let me classify the change once I know the target time. But since no specific time was given, I should ask for the desired time. Let me ask the traveler what time they'd like to move it to. The item "Gloria Molina Grand Park" is currently scheduled at **10:00 AM** on **September 29**. What time would you like to move it to?
```

这次观察里同时有两个用户可见问题。

### A. Agent 内部推理泄露到 chat bubble

问题表现：

- 用户只说 `change time`，没有给目标时间。
- 系统已经知道 selected item 是 `Gloria Molina Grand Park`，所以 item context 没丢。
- 但回复里出现了 agent 内部执行过程：
  - `The traveler wants to change the time...`
  - `The user hasn't specified a new time.`
  - `I need to ask...`
  - `Let me classify...`
  - `Let me ask...`
- 这些内容不是面向用户的最终回复，应该只存在于内部推理 / scratchpad / routing 层。

正确行为：

```text
Gloria Molina Grand Park is currently scheduled for 10:00 AM on September 29.
What time would you like to move it to?
```

或更短：

```text
What time would you like to move Gloria Molina Grand Park to?
```

问题分类：

- 不是 selected item context 问题：item 已识别正确。
- 不是 `classify_change` 能力问题：当前缺少目标时间，本来就不该进入最终 change proposal。
- 更像是 missing-slot clarification path 的 final response assembly 问题：内部 reasoning 没有和用户可见 answer 分离。

通过标准：

- 用户只输入 `change time` 且没有目标时间时，只追问目标时间。
- 用户可见回复不包含 `I need to...`、`Let me...`、`traveler wants...`、`classify_change`、tool planning 或 classification planning。
- 不生成 `ProposedChatChange`，直到用户给出具体新时间。
- item-scoped drawer 继续使用 selected item context，不要求用户重复 item 名称。

### B. Markdown 没有渲染

问题表现：

- 回复末尾包含 Markdown bold syntax：

```text
**10:00 AM**
**September 29**
```

- 但 UI 中没有渲染成加粗，而是原样显示星号。

用户影响：

- Chat bubble 看起来像 raw model output，而不是产品化的 assistant reply。
- 这和内部推理泄露叠加后，会让用户感觉 AI response 没有经过 final formatting / rendering 层处理。

预期行为：

- 如果 chat bubble 支持 Markdown，则 `**10:00 AM**` 和 `**September 29**` 应渲染为加粗。
- 如果该 UI 不打算支持 Markdown，则 agent 输出层不应生成 Markdown syntax，应输出 plain text：

```text
The item "Gloria Molina Grand Park" is currently scheduled at 10:00 AM on September 29.
What time would you like to move it to?
```

排查方向：

- 检查 Plan drawer / Ask Cadensy message renderer 是否对 assistant message 启用了 Markdown rendering。
- 检查是否某些 message type 走了 plain text renderer，而 proposal / card message 走了不同 renderer。
- 检查 agent final response format 是否应该避免 Markdown，统一输出 plain text。

当前状态：

- 待修复。
- 建议把这条作为 chat agent UX / response rendering bug，而不是 planner 或 decision orchestrator bug。

2026-08-16 已完成修复：

- 没有给 Plan drawer / Ask Cadensy 增加 Markdown renderer，也没有引入 `react-markdown`、
  `remark-gfm` 或其他 Markdown dependency。
- 普通 assistant chat bubble 继续走现有 plain text renderer。
- 后端把普通用户可见 reply contract 固定为 plain text：
  - 不输出 `**bold**`
  - 不输出 `_italic_`
  - 不依赖 Markdown heading/list syntax
- `backend/app/domain/chat/service.py` 的 agent system prompt 现在明确要求最终用户可见回复使用
  plain text。
- `change time` 这类缺少目标时间的 item-scoped clarification 现在走 deterministic plain-text
  reply path，不再把 Markdown emphasis 或后续渲染能力当作前提。
- 这次修复没有影响：
  - `ProposedChatChange` cards
  - candidate option cards
  - Apply flow
  - Planner
  - decision orchestrator

验证结果：

- 新增 regression coverage：
  - `backend/tests/test_chat_agent_branch.py`
  - `backend/tests/test_chat.py`
- 通过的定向测试：
  - `backend/tests/test_chat_agent_branch.py -k "plain_text or selected_item_is_in_agent_user_message"`
  - `backend/tests/test_chat.py -k "change_time_clarification_response_is_plain_text or selected_item_relative_time_request_uses_selected_item"`
- `selected item = Gloria Molina Grand Park` 且 `user = change time` 的最终 reply 现在是：

```text
Gloria Molina Grand Park is currently scheduled for 10:00 AM on September 29. What time would you like to move it to?
```

- 用户可见普通文本中不再出现 raw `**`、`_` 等 Markdown syntax。
