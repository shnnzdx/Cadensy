# Cadensy 系统地图 · AI 部分交接文档

接手前先读这份。它讲清楚两套互不相干的系统各自怎么跑、AI 在哪里、边界画在哪，以及现在还有哪些已知没修的东西。

所有内容都在代码中逐条核实过，不是凭记忆写的。

---

## 目录

1. [两套系统，互不相干](#1-两套系统互不相干)
2. [排行程](#2-排行程)
3. [聊天 Agent](#3-聊天-agent)
4. [决策与投票](#4-决策与投票)
5. [要调教 agent，改哪个文件](#5-要调教-agent改哪个文件)
6. [怎么验证](#6-怎么验证)
7. [架构判断：不要加什么](#65-架构判断为什么是这样以及不要加什么)
8. [已知未修](#7-已知未修)
9. [关键文件索引](#8-关键文件索引)

---

## 1. 两套系统，互不相干

最容易混淆的一点：**排行程**和**聊天**是两套独立系统，用模型的方式完全不同。改一边不会影响另一边。

| | 排行程 | 聊天 |
|---|---|---|
| 入口 | `domain/plans/generator.py` | `domain/chat/service.py` |
| 调用方式 | `base.call_model` | `base.call_agent` |
| 轮数 | 一次，问完结束 | 最多 8 轮 |
| 工具 | 没有 | 五个 |
| 是 agent 吗 | **不是** | **是** |

> **底层是同一套。** 两边都走 `agents/base.py`，同一个 DeepSeek key、同一个 `deepseek-v4-flash`，由 `backend/.env` 的 `PLANNER_AI_PROVIDER` / `CHAT_AI_PROVIDER` 决定。`MOCK_AI=1` 时两边都不发真实请求。

---

## 2. 排行程

注意：**没有 `plan.py` 这个文件**。干活的是 `generator.py`，`agents/planner.py` 只是它中间叫模型的那一步。

```
点「生成行程」
  POST /api/trips/{id}/plans/generate
        ↓
取候选景点库                         domain/places/service.py
  芝加哥用手工整理的 50 个点（data/poi_chicago.py，有时长/价格）
  其它城市先查 PostgreSQL place 缓存，不够才调 Geoapify
  ⚠️ 城市名是精确匹配 —— 填 "LA" 查不到 "Los Angeles" 的缓存
        ↓
逐天筛出「合法」候选                  generator.py · _day_candidates
  Python 规则先过一遍：预算、营业时间、步行量、忌口、无障碍
  过不了的根本不给模型看
        ↓
【模型】从候选里挑今天去哪几个        agents/planner.py · plan_day
  单次调用，无工具，输出受 JSON 结构约束
  挑了名单外的东西会被直接丢弃
  格式不合格会自动重试一次修复
        ↓
【兜底】模型挑不出来 → 纯规则排        generator.py · _rules_day
  不叫模型。断网、没额度、模型抽风，行程照样生成得出来
        ↓
补三餐 → 全量校验 → 写数据库
```

返回里带 `generated_by`（`planner` / `rules`）和 `used_ai`，**看这两个字段就知道这次到底有没有用上 AI**。

> **这里没有 agent。** 模型只做一件事：从一份已经筛干净的名单里挑几个。所有硬规则都在它外面由 Python 把关，所以它不能编地点，也不能违反成员约束。

---

## 3. 聊天 Agent

曾经有三条岔路，靠关键词表决定走哪条。**现在只有一条：每一句话都走 agent。** 岔路是被删掉的，不是被绕过的。

### 一次对话怎么跑

```
你打的字 + 完整对话历史 + 当前选中的条目
  POST /api/trips/{id}/chat
  历史由前端每次带上来
  选中的条目会被写进这一轮消息的开头，让 agent 知道「这个」指的是哪个
        ↓
┌─────────── Agent 循环 · base.py call_agent ───────────┐
│ 1. 把当前所有消息发给 DeepSeek，问它下一步要不要调工具    │
│ 2. 它没要工具 → 这就是最终回答，循环结束                 │
│ 3. 它要了工具 → 后端真的去执行（一轮可以要好几个，并行）   │
│ 4. 工具结果追加进消息列表，回到第 1 步                   │
│ 5. 上限：8 轮 · 120000 token · 30 秒                   │
│    同一工具被守卫拦 2 次就停                            │
└───────────────────────────────────────────────────────┘
        ↓
【安全闸】检查回复有没有宣称「已经改好了」
  agents/chat.py · _claims_change_completed
  命中（已生效 / 已提交 / took effect / all set …）
  → 整段换成「还没有改变，要点 Apply」
        ↓
提取 proposed_change 和 candidate_options
  前端靠这两样渲染「将要提交什么」和备选方案
```

> **失败会降级，不会卡死。** 超时 / 报错 / 回复为空，都会退回一次不带工具的普通问答（`answer_question`），至少答得出东西。

### 五个工具，全部只读

| 工具 | 干什么 | 守卫 |
|---|---|---|
| `get_current_plan` | 读当前行程。按天、按日期、按星期几，或 `all` | — |
| `get_trip_facts` | 目的地、日期范围、人数、币种、预估人均总价。不返回任何成员身份 | — |
| `classify_change` | 判定一个改动走哪条路。也能表达「换场地」（`new_title` / `new_place` / `new_price_per_person` / `new_lat` / `new_lng`） | 必须先调过 `get_current_plan` |
| `find_replacement_place` | 从景点库找替代场地。排除当前项目、已用过的地方、营业时间盖不住该时段的 | 必须先调过 `get_current_plan` |
| `propose_options` | 给折中方案。见下 | 必须先调过 `get_current_plan` |

`propose_options` 有两个关键参数：

- `conflict_item_ids` —— 指明冲突涉及哪些条目。**不传的话只能猜当天最长的那个**，那只对「这天太满了」正确，对具体冲突全错。
- `suggestions` —— 让 AI 自己写方案。**每条都会被后端验证**：条目真实存在、patch 可执行、能通过判定。没通过的进 `rejected_suggestions` 返回，不会静默吞掉。

> **没有任何一个工具能写数据库。** agent 只能查和判定，真正的改动必须由用户点 Apply。这是产品前提，也是刻意画的边界。

---

## 4. 决策与投票

这是 Cadensy 真正的立意所在，而且**全部是确定性 Python 规则，AI 无权参与**。

- 判定入口：`domain/constraints/engine.py · classify()`
- 执行入口：`domain/decisions/orchestrator.py`

| 路径 | 触发条件 | 发生什么 |
|---|---|---|
| `confirm` | 动到已确认的预订，或违反某成员的硬约束 | 受影响的人必须逐个确认。预订涉及公摊钱 → 全组；硬约束 → 只有相关成员 + 发起人 |
| `reopen_round` | 这个条目已被一次投票定过（`settled`） | 需要写理由。推翻旧决定要**超过全体成员半数**支持，沉默算维持原状 |
| `round` | 有别人动过这个时段（`touched`），或时间撞车 | 全组投票。票多者胜，**平票维持原状**，没投票的人不影响结果 |
| `notice` | 以上都不命中 | 直接生效，全组收到通知。通知上有「我有不同意见」按钮，点了会升级成 `round` |

**判据按上表顺序检查，第一个命中就返回。**

投票截止时间：

| | 规划期 | 出行中 |
|---|---|---|
| 投票（round） | 24 小时 | **2 小时** |
| 确认（confirm） | 7 天 | 2 天 |

已经在路上了就不该等一天。

### 单人行程的特殊处理

- `settled` 和 `contested` 都不触发表决（一个人没有「小组」）
- 自己改自己动过的条目也不算争抢
- **成员数查不到时按有别人处理**，宁可多要一次投票

### AI 写的方案怎么进到投票卡片

这是 AI 唯一参与投票环节的方式：**它在聊天里写一次，验证通过后固化下来，投票时只展示，不再生成。**

```
① 聊天里 agent 调 propose_options，自己写折中方案
   后端逐条验证 → 通过的进 candidate_options 返回前端
        ↓
② 用户点某个方案的 Apply
   前端把【其余方案】一起放进 options 字段提交
   trip/src/final/plan-feature/useAssistantChangeRequestFlow.js
        ↓
③ 后端重新验证一遍（前端来的一律不可信）
   api/main.py · _validated_change_options
        ↓
④ 判定要投票 → 验证过的方案作为 alternative-N 进入 round
        ↓
⑤ 投票卡片：Keep current / Suggested change / AI 的方案们
```

**第 ③ 步的五道关卡，一关不过就丢弃：**

1. `item_id` 必须**就是被改动的那个条目**（不是「属于同一个 trip」就行——一个 round 只结算一个条目，指向别处的选项会被写到错的对象上）
2. 只允许 `start_hour` / `day_date` / `duration_min` 三个字段（`title`、`place`、价格、坐标进不来）
3. 至少要有一个可执行字段
4. `start_hour` 必须在 9:00–21:00 之间，`duration_min` 必须为正
5. 能通过 `classify_change`（用 savepoint 隔离，失败不污染事务）

最多接收 5 条，和已选方案 patch 相同的会去重。

**为什么不在投票环节实时叫 AI：**

- 提交改动是事务性请求，塞进 AI 调用会变成 8–10 秒，超时就整个提交失败
- 那时候上下文更少（只有一个条目和一个 patch），方案质量反而更差
- **一场投票开 24 小时，全组必须看到同一组选项**——实时生成会让每个人看到的不一样

### ⚠️ 选项没有 patch 就等于不改

**任何没带 patch 的选项赢了，都一律不改动行程。** 曾经有个「拿选项标题当改动内容」的兜底（为 Split up 保留的），会把 UI 文案写成行程条目的名字——比如把景点改名成 "Suggested change"。Split up 删掉后这个兜底也一并关闭了。

### 关于 Split up

投票选项里的 Split up **已经删除**。`domain/decisions/organizer.py` 里那个「组织者在僵局时拆分街区」是**另一条路**，不是投票选项，仍然保留。

---

## 5. 要调教 agent，改哪个文件

先判断「笨」在哪一层，不同的笨改不同的地方。

| 症状 | 改哪里 | 杠杆 |
|---|---|---|
| 该调的工具没调 / 调错 / 参数传错 | `agents/tools.py` 工具的 `description` | **最高** |
| 方案本身蠢、答非所问 | `agents/tools.py` 工具的 **handler 实现** | **最高** |
| 跨工具的行为规矩 | `domain/chat/service.py · _agent_system_prompt()` | 中 |
| 它缺信息只能猜 | `agents/tools.py` 工具**返回的字段** | 中 |
| 想不完被截断 | `CHAT_AGENT_MAX_ROUNDS`（现在 8） | 低 |
| 经常超时 | `CHAT_AGENT_TIMEOUT_SECONDS`（现在 30 秒） | 低 |

**关键区分：** 描述管「什么时候调它」，handler 管「调了之后给什么」。

**系统提示词只有一份**，定义在 `_agent_system_prompt()`，验证脚本从这里导入。以前脚本自带一份副本，两边各自漂移，导致脚本跑出来的结论不代表线上行为——**不要再复制它**。

---

## 6. 怎么验证

> ⚠️ **单元测试全绿不代表 agent 调对了。** 工具描述和提示词改错不会报错，只会让模型悄悄调错工具。改完必须跑真实验证。

### ① 真实验证（改了描述或提示词就必须跑）

```bash
cd backend
MOCK_AI=0 DISABLE_SCHEDULER=1 .venv/bin/python \
  app/agents/agent-server/run_real_trip_tools_trace.py
```

6 个场景，只看结尾的 `=== Summary ===`，记**轮数**和**工具顺序**。脚本在测试库的一个事务里造临时行程，跑完回滚，不落盘。

**每种配置跑 3 次看中位数。** 同样的代码两次跑出不同轮数是常态，单次数据不作数——这是实测过的。

判断标准：

- 轮数变少通常是好的
- **轮数明显变多，或者该调的工具不调了 → 改坏了，退回去**
- 特别盯简单问答那两个场景，它们应该稳定在 2 轮

### ② 单元测试（兜底，不能替代 ①）

```bash
cd backend
DISABLE_SCHEDULER=1 MOCK_AI=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

### ③ 判断某句话有没有走到 agent

```bash
cd backend
tail -f logs/agent-trace-$(date +%Y%m%d).jsonl
```

另一个日志 `logs/trace-{日期}.jsonl` 记每次模型调用：走的哪条路、哪个供应商、耗时、token。排查「到底有没有真的调 AI」看这个。

### ④ 本地起服务

```bash
cd backend && .venv/bin/python -m uvicorn app.api.main:app \
  --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend && npm run dev        # localhost:3000
```

改了 `trip/` 之后，主站 `/trip` 里的内容**不会自动更新**，必须跑：

```bash
cd frontend && npm run build:trip-preview
```

---

## 6.5 架构判断：为什么是这样，以及不要加什么

### 现在的配置

**一个 agent（聊天）· 一个 workflow（排行程）· 五个工具。**

这个配置是按任务性质选的，不是随便定的：

| 任务 | 用法 | 为什么 |
|---|---|---|
| 排行程 | Workflow | 一次性判断，不需要调查。模型只做「哪几个放同一天、各自几点」 |
| 对话 | Agent | 开放式需求。中文表达改动的方式无穷，关键词表穷举不完（这是实测踩出来的） |

### 撑住整个设计的边界

**模型出错的最坏情况是「答得笨」，不是「把行程改坏」。** 靠四条代码级约束：

1. 五个工具全部只读，AI 碰不到数据库
2. 决策路径由 Python 规则定，AI 只能调 `classify_change` 然后服从
3. 回复过一道安全闸，宣称「已改好」会被整段替换
4. 任何改动都要用户点 Apply，投票通过才写进行程

**改动这四条中的任何一条之前，先想清楚代价。** 它们不是防御性编程，是这个产品能成立的前提。

### 不要加的东西

- **不要加第二个 agent。** 现有问题没有一个是「agent 不够」造成的。多一个 agent = 多一套提示词、多一处漂移、多一倍验证成本。光是「验证脚本自带一份提示词副本」就已经坑过一次。
- **不要把排行程改成 agent。** 现在一天一次调用、四天约 8.5 秒。改成 agent 每天多轮会慢四倍，而它要做的只是一次判断——工具对它没用，候选名单已经递到面前了。
- **不要随意加工具。** 五个已经让简单问答出现过一次 2 轮变 3 轮。工具越多模型越犹豫，加之前要有具体需求驱动，加之后要跑真实验证。

---

## 7. 已知未修

交接文档最容易出问题的地方是只写「做了什么」不写「还欠什么」。以下每条都注明了位置，按「会不会在演示时咬人」排序。

### 7.1 冲突检测在四个城市里有三个是哑的 ⚠️ 演示前必读

芝加哥用手工整理的库（有时长），其它城市走 Geoapify，**时长未知就留空**——这个「unknown 不等于假默认值」的规则本身是对的。但冲突检测遇到时长为空会**直接跳过**。

实测数据库里的情况：

```
chicago        时长为空   0 / 60     ✅
shanghai       时长为空  24 / 24     ❌
los angeles    时长为空  24 / 24     ❌
new york       时长为空  17 / 17     ❌
```

**后果：拿洛杉矶／纽约／上海的行程演示「改时间会触发投票」，永远不会触发。** 整套决策机制在四分之三的城市里是隐形的。实测有个洛杉矶行程同一天两个条目都在 10:00，系统一声不吭。

**演示务必用芝加哥的行程。**

**修法：** 给未知时长一个**仅用于冲突检测**的默认值（比如 60 分钟），不写进数据库，保持「unknown 不等于假默认值」。

**位置：** `orchestrator.py · _schedule_conflict_item`（`if peer.duration_min is None: continue`）

### 7.2 聊天记忆只活在当前抽屉里，刷新就没

对话历史**只存在前端 React state**，后端没有 `chat_message` 表。

- 刷新页面 → 对话全没了，「我是说…」这类纠正立刻失效
- 关掉抽屉再打开，大概率也没了
- **别的成员看不到你和 AI 的对话**——对一个多人协作产品来说这不太对

后端已经完全支持接收历史（`call_agent(history=...)`），缺的只是持久化。

**演示时别刷新页面。**

**位置：** `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`（历史在 `messages` state 里）

### 7.3 单人行程还是能对自己的通知提异议、然后跟自己开会

`object_to_notice` 不看行程有几个人，也不经过判定引擎——它直接写死了一个 `ROUND`。所以「单人不表决」那条规则在这条路上不生效。

正确做法是单人行程的通知上就不该出现「我有不同意见」按钮。

**位置：** `orchestrator.py · object_to_notice`

### 7.4 投票卡片从不说明「为什么要投票」

AI 在聊天里已经写出了很具体的冲突分析，并传给了 `propose_options`。真实跑出来的一句：

```
"Moving Millennium Park walk to 11:00 AM would make it run 11:00-12:30,
 overlapping with Lunch near the Loop (12:00-1:15 PM)."
```

**然后这句话被丢掉了。** 投票卡片上显示的是一句固定文案——"Vote on the option, not the person"——每张卡片都一样。

**后果**：没参与聊天的成员打开 Updates 页，只知道"这个街区有争议"，**不知道争议是什么**。AI 干的事只有跟它聊过天的那个人看得到。

**修法和第 4 节的选项完全一样：AI 写一次 → 验证 → 冻结 → 投票时只展示，不再生成。** 不新增模型调用，不新增表。

| 层 | 改什么 |
|---|---|
| 前端 | 提交改动时把 `conflict_description` 跟 `options` 一起发 |
| 后端 | 存进 `DecisionRound.reason`（这个字段已经存在，推翻投票在用，普通投票是空的），做长度截断 |
| 前端 | 卡片上 `{isReopen && round.reason && ...}` 去掉 `isReopen &&` |

**约束**：这段文字是模型写的，会被全组在投票期间看到。按选项文案同样处理——**纯文本、长度截断、绝不参与任何判断**。判定仍然由 Python 规则做。

### 7.5 一个选项只能改一个条目

`ProposedChatChange` 和每个选项都只有一个 `item_id` + 一个 `patch`。所以「把每天都简化」「把散步和午餐互换」这类需求**在数据结构层面表达不出来**。

已经在工具描述里禁止 AI 写「顺便把另一个也挪走」这种文案，但根本限制还在。

**位置：** `domain/chat/service.py · ProposedChatChange`

### 7.6 generator 的兜底路径可能让每天行程一模一样

候选排序只按重试次数轮转，**不看是第几天**；唯一让各天不同的是「已用过」去重表，而兜底路径会把它清空。单人行程（无预算上限）永远走得进这条路。

目前没有稳定复现，已有回归测试盯着（`tests/test_plan_day_variety.py`）。

**位置：** `generator.py · _build_day` / `_candidate_order`

### 7.7 planner 提示词要求了模型做不到的事

提示词写着「不要每天都重复 10:00 / 14:00 / 19:00」，但模型是**按天单独调用**的，它看不到别的天排了什么时间。实际生成的行程确实每天都是这三个整点。

要修得把已用时间也传进去，或者干脆由 Python 分配时间。

**位置：** `agents/planner.py · _day_prompt`

### 7.8 死代码：两处已经没有生产调用方的 AI 模块

| 文件 | 状态 |
|---|---|
| `agents/explainer.py` | **整个文件**生产已无调用方，只有 `tests/test_explainer.py` 在用 |
| `agents/chat.py · explain` | 同样，只剩 `tests/test_chat_safety_and_time.py` 在用 |

两个都是历史遗留的「解释改动」模块，功能已被 agent 取代。`explain` 里的安全短语检测 `_claims_change_completed` 已经单独接到 agent 回复上了，**那条保障没丢**。清理时连同各自的测试一起删。

不影响运行，但接手的人会以为它们还在跑。

### 7.9 排行程没有任何验证手段

聊天那边有 `run_real_trip_tools_trace.py`：改一次跑一次，看轮数和工具顺序，好坏一目了然。

**排行程什么都没有。** 改了提示词只能重新建行程、点生成、肉眼看——慢、不可重复、看不出细微退步。

这是「改 AI 提示词最危险」原则的一个缺口：那边有尺子，这边没有。

**建议：** 写一个对比脚本放在 `backend/app/agents/agent-server/`，同一份种子数据跑两遍（`MOCK_AI=1` 规则组 vs `MOCK_AI=0` AI 组），输出景点、时间、去重数、各天是否雷同。**先有尺子，再动刀。**

---

## 8. 关键文件索引

| 路径 | 是什么 |
|---|---|
| `agents/base.py` | 模型调用底座。`call_model` 单次，`call_agent` 循环 + 工具 |
| `agents/tools.py` | 五个只读工具的定义和实现。**调教 agent 主要改这里** |
| `agents/planner.py` | 排行程时叫模型的那一步 |
| `agents/chat.py` | 降级用的普通问答 + 安全短语检测 |
| `agents/trace.py` | 写 `logs/*.jsonl` 的地方 |
| `domain/chat/service.py` | 聊天入口、系统提示词、降级逻辑 |
| `domain/plans/generator.py` | 排行程主流程 |
| `domain/places/service.py` | 景点库：芝加哥策展 / 缓存 / Geoapify |
| `domain/constraints/engine.py` | **判定引擎**，四条路径在这里决定 |
| `domain/decisions/orchestrator.py` | **执行**：通知、投票、确认、结算 |
| `data/poi_chicago.py` | 芝加哥 50 个策展景点 |
| `api/main.py` | 所有 HTTP 接口 |

---

## 三条不要越过的线

1. **工具永远只读。** agent 不能写数据库，改动必须经过用户点 Apply。
2. **决策路径由 Python 定，AI 只能服从。** 模型不能选 `notice` / `round` / `confirm`，只能调 `classify_change` 然后按结果说话。
3. **`unknown` 永远不等于假默认值。** 价格、时长、营业时间未知就留空，不要填 0 或 90 分钟。
