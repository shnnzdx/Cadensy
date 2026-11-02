# 多人协作式 AI 旅行规划平台
## 首页 Hero + Scroll Storytelling 最终设计稿

**文档状态：** 当前目标设计 / 后续实现主设计稿  
**替代文档：** `ai-travel-hero-animation-design-v3.md`  
**设计范围：** 首页 Hero 第一幕、左右信息进入、中间 3D Idea Sphere、滚动吸入、球体放大、破碎、点状世界地图重组，以及与下一章节的连续衔接  
**当前技术基线：** React / Next.js + 原生 Three.js  
**设计语言：** Pale Blue / Soft Spatial Data / Collaborative Intelligence / Editorial Travel  
**参考边界：** 可以参考 Auros 的 volumetric particle presence，以及用户提供参考图的“多人输入 → 中心协调 → Shared Plan”信息结构，但不复制其具体素材、布局、颜色、shader 或源码。

---

# 0. 最终核心叙事

首页开场不是静态三栏，也不是一个独立粒子 Demo。

它是一段连续滚动故事：

```text
很多人的独立旅行输入
        ↓
围绕一个持续活着的 3D Idea Sphere 共存
        ↓
左侧 Inputs + 右侧 Shared Plan 轻微 stagger 进入
        ↓
用户继续下拉
        ↓
CTA 先淡出
        ↓
左右 UI 开始被中间球体具象吸入
        ↓
UI 被压缩、碎裂、转化为粒子
        ↓
球体不断吸收信息并逐渐放大
        ↓
球体成为视野唯一视觉核心
        ↓
球体失稳、破碎、散开
        ↓
粒子重新聚合
        ↓
形成纯视觉点状世界地图
        ↓
自然进入第二章节
```

核心产品语义：

> Many voices become one shared direction, and that shared direction opens into a world of possible travel.

中文语义：

> 很多人的独立偏好被理解、协调并归拢，最终形成一个共同方向，并自然打开下一段关于“世界与旅行”的产品叙事。

---

# 1. 设计目标

首页开场必须同时完成四件事：

1. **表达多人协作**
   - 左侧必须明显包含多个成员、多个偏好、多个限制。
   - 不应该看起来像单人填写表单。

2. **建立 TripSync 核心视觉符号**
   - 中间现有 3D Idea Sphere 是 Hero 的主要视觉资产。
   - 它不是背景装饰，而是“多人需求空间 / 协调核心”的产品隐喻。

3. **表达从分散到秩序**
   - 左侧更个人、更松散、更不规则。
   - 右侧更稳定、更结构化。
   - 球体位于二者之间，承担协调核心。

4. **让 Hero 与下一章节成为同一段故事**
   - 不做硬切页。
   - 不加入白色大框。
   - 世界地图必须由球体粒子语言演化而来。

---

# 2. Design Read

Reading this as:

> A premium collaborative travel homepage where many personal inputs orbit a living coordination core, then collapse into one shared direction before expanding into a world-scale travel narrative.

建议参数：

```text
DESIGN_VARIANCE: 8
MOTION_INTENSITY: 8
VISUAL_DENSITY: 5
```

解释：

- `DESIGN_VARIANCE 8`
  - 第一幕允许明显不对称。
  - 左右不做镜像。
  - 卡片可以轻微漂浮、错落。
  - 视觉重心必须始终由中间球体控制。

- `MOTION_INTENSITY 8`
  - 第一幕保持克制。
  - 强动效集中在吸入、放大、破碎、地图重组阶段。
  - 不允许全程高强度 motion。

- `VISUAL_DENSITY 5`
  - 比当前“只有一个球”的 Hero 信息量更丰富。
  - 但必须保留 pale-blue breathing room。
  - 不允许变成 dashboard。

---

# 3. 废弃旧结构

旧版：

```text
左侧球体
→ 中间 Placeholder / 处理模型
→ 右侧纵向计划流程
```

最终改为：

```text
左侧 Independent Traveler Inputs
→ 中间现有 Living 3D Idea Sphere
→ 右侧 Shared Plan
```

因此：

- 不再存在中间 Placeholder。
- 不再需要额外 AI Brain / Crystal / Processor。
- 不再需要白色大容器。
- 现有球体本身就是协调核心。

---

# 4. 页面结构

整体分为三个连续阶段：

```text
A. Natural-flow Hero Scene
B. Scroll Transition Scene
C. World Map / Section 2
```

重要：

> Hero 本身不是固定 `100vh` 的盒子。

第一页允许自然超过首屏。

CTA 在较矮屏幕上稍微低于 fold 是允许的。

---

# 5. Scene 1：Natural-flow Hero

结构：

```text
Hero Heading

        ↓ generous spacing

Left Input Cluster
        ↘
          Living 3D Idea Sphere
        ↗
Right Shared Plan

        ↓ generous spacing

CTA
Fine print

        ↓

Scroll Transition begins
```

第一幕在没有开始强滚动转场时，就必须作为一个完整首页构图成立。

---

# 6. 第一幕版式选择

用户要求：

> 不严格复制参考图，需要更自由、更现代，并提供可选择的版式。

因此保留三个方向。

## 6.1 Layout A — Balanced Triptych

```text
Left Inputs     Sphere     Shared Plan
```

特点：

- 产品逻辑最清楚。
- 最接近 many voices → coordination → one plan。
- 左右基本平衡，但不做完全镜像。

适合：
- 强调产品解释。

风险：
- 容易像传统 explainer diagram。

---

## 6.2 Layout B — Asymmetric Editorial（推荐默认）

结构：

```text
左侧输入群偏上 / 偏左

                 大型球体略偏中

                                  右侧 Plan 偏右 / 偏下
```

特点：

- 更现代。
- 更像品牌首页。
- 左侧更散，右侧更稳。
- 中间球体成为真正视觉引力中心。
- 为吸入路径和球体放大留出更自然的空间。

**当前推荐默认：Layout B。**

如果视觉评审不通过，可以回退 Layout A。

---

## 6.3 Layout C — Orbital Narrative

特点：

- 左侧输入沿球体形成更明显弧线。
- 右侧结果区更像空间中的 resolved state。
- 动效潜力最大。

风险：
- 可读性最低。
- 更容易变成纯 WebGL demo。

当前不作为默认。

---

# 7. Hero Heading

保留：

```text
Plan a trip everyone
can agree on.
```

要求：

- 不缩成很小。
- 不超过 2 行 desktop。
- 不新增过多 eyebrow / badge / trusted by。
- Heading 到主场景必须有明显 breathing room。

桌面目标：

```text
Headline → Scene
约 56–80px
```

Hero 不需要为了让 CTA 塞进首屏而压缩此距离。

---

# 8. 左侧：Independent Traveler Inputs

## 8.1 语义

左侧代表：

> 多个旅行成员在计划形成前，各自提出的独立偏好、限制和顾虑。

它必须比右侧更分散、更个人化。

---

## 8.2 数量

桌面：

```text
5–7 个输入
```

平板：

```text
4–5 个
```

移动端：

```text
3–4 个核心输入
```

---

## 8.3 内容

推荐：

```text
Budget
Dates
Pace
Food
Accessibility
Activities
Stay preference
```

具体短句可使用：

```text
Keep it under $2,000
June 10–18 works best
Moderate pace
Vegetarian options
Step-free access
Culture + nature
Quiet hotel
```

---

## 8.4 卡片视觉

推荐：

- 透明 / 半透明 pale surface。
- 非常轻的 border。
- 极轻 shadow。
- 小头像可选。
- 小 icon 可选。
- 少量 note / member mark 可选。
- 不使用完全相同的 SaaS cards。

允许：

- 轻微 rotation。
- 不同宽度。
- 轻微错位。
- 不完全等距。
- 1–2 个元素稍微离开主 cluster。

原则：

```text
Individual
but
visually related
```

---

## 8.5 卡片层级

避免 6 张完全相同卡片。

建议：

```text
2 个 primary input
3–4 个 secondary input
1 个 micro note / privacy hint
```

---

# 9. 中间：Living 3D Idea Sphere

## 9.1 产品语义

球体继续代表：

> 当前旅行小组中，所有成员尚未完全协调的想法、偏好与限制所形成的动态需求空间。

新的首页结构中，它同时承担：

> The coordination core.

它不是 AI brain，也不是黑盒。

---

## 9.2 当前实现基线

保留原生 Three.js：

```text
THREE.Scene
THREE.PerspectiveCamera
THREE.WebGLRenderer
THREE.Points
THREE.BufferGeometry
THREE.ShaderMaterial
transparent canvas
```

当前不引入 React Three Fiber。

---

## 9.3 当前粒子数量

```text
mobile < 640
visual: 720
interactive: 44

tablet < 980
visual: 1600
interactive: 86

desktop
visual: 3800
interactive: 148
```

不要因为加入左右 UI 就先增加 particle count。

---

## 9.4 当前球体形态

继续保留：

```text
outer layer: 0.82–1.07
middle layer: 0.60–0.93
inner layer: 0.38–0.66

outer: 68%
middle: 25%
inner: 7%
```

保留：

- 中心空气感。
- 外围更强。
- 前后深度。
- local irregularity。
- 轻微椭球。
- localField / outerSpill / densityWave。

---

## 9.5 当前颜色

继续使用当前 pale-blue 基线：

```text
rear      #b5c7df
soft      #d0e2fb
mid       #afcdf7
active    #82b5f5
highlight #5f9ef1
aura      #d8e9fb
```

避免：

- neon。
- purple glow。
- cyberpunk teal。
- 多类别高饱和配色。

---

## 9.6 当前运动

保留：

- Slow Y rotation。
- Low-frequency speed variation。
- Per-particle shader drift。
- Subtle breathing。
- Pointer parallax。
- Hover boost。

第一幕中球仍然必须“活着”。

---

## 9.7 Hover

保留当前逻辑：

```text
nearest interactive particle
→ 70ms hover intent
→ valid screen projection
→ tooltip visible
```

Tooltip 继续：

```text
ref-driven ownership
```

不要重新让 React state 控制 frame-level tooltip position。

---

## 9.8 新场景中的 Hover 规则

加入左右 UI 后，Hover 更克制：

- 不暗整个 Hero。
- Tooltip 不遮左侧 cards。
- Tooltip 不遮右侧 Shared Plan。
- Tooltip 不遮 H1。
- 一次只显示一个主动 Hover label。
- 第一版不新增 auto label cycle。

---

# 10. 右侧：Shared Plan

## 10.1 定义

右侧代表：

> 多人输入被理解和协调后，正在形成或已经形成的共享旅行计划。

视觉反差：

```text
Left:
distributed / personal / irregular

Right:
structured / shared / resolved
```

---

## 10.2 推荐结构

主 Plan Panel 可以包含：

```text
Shared plan
Everyone's aligned / Everyone's happy

Overview
Itinerary
Budget
Travel & Stay
Activities
```

不要在 Hero 里复制完整产品界面。

---

## 10.3 状态

推荐：

```text
inactive
→ receiving
→ active
→ confirmed
```

右侧可以从轻骨架逐步进入完整状态，但不能抢球体主视觉。

---

## 10.4 视觉

要求：

- 更规整。
- 更稳定。
- 更少 rotation。
- 更低 motion amplitude。
- 更清楚 hierarchy。

可有：

- 1 张主要 Shared Plan。
- 1–2 个极轻辅助 token，例如日期或 destination。

不要：

- 重 dashboard。
- 6 张等宽结果卡。
- 多色状态系统。

---

# 11. 第一幕进入动画

用户已确定：

> 左右内容轻微 stagger 从两边进入。

建议节奏：

```text
0.0s  Heading 已存在 / 淡入
0.2s  Sphere 进入稳定状态
0.4s  左侧第一组 input card
0.55s 左侧第二组
0.7s  右侧 plan shell
0.85s 右侧 plan detail
1.0s  CTA 稳定
```

左侧建议：

```text
opacity 0 → 1
translateX(-18px ~ -36px) → 0
small Y variation → 0
scale .97 → 1
```

右侧建议：

```text
opacity 0 → 1
translateX(18px ~ 32px) → 0
scale .985 → 1
```

禁止：

- 高速从屏幕外飞入。
- 大 bounce。
- presentation-style animation。

---

# 12. CTA

保留：

```text
Create a trip
See how it works
Free to use · No credit card required
```

CTA：

- normal document flow。
- 不固定 viewport bottom。
- 不为了首屏可见缩球。

---

# 13. Hero Natural-flow

Hero 继续保持自然高度。

禁止把整个 Hero 强制成：

```text
height:100vh
height:100svh
max-height:viewport
overflow:hidden
```

作为压缩方案。

Hero 高度由内容决定。

---

# 14. Scroll Transition 总体机制

第一幕稳定展示之后，用户继续下拉进入 scroll storytelling。

建议结构：

```text
Natural-flow Hero Scene
        ↓
Transition Scroll Wrapper
        └── Sticky Transition Stage
              ├── continued input state
              ├── continued plan state
              ├── sphere
              ├── fragments
              └── future map particles
        ↓
World Map Section
```

重要：

> Sticky 只用于“转场阶段”，不是把整个 Hero 固定成一屏。

---

# 15. Scroll Progress

标准化：

```text
p = 0.00 → 1.00
```

推荐 mapping：

```text
0.00–0.10 resting / CTA exit
0.08–0.25 attraction
0.22–0.52 concrete absorption
0.38–0.68 sphere expansion
0.62–0.72 pre-shatter
0.70–0.84 shatter
0.80–1.00 map formation
1.00       map resting / release
```

阶段可以 overlap。

---

# 16. Phase A — CTA / Heading Exit

```text
p = 0.00–0.12
```

行为：

- CTA 先淡出。
- Fine print 淡出。
- Heading 轻微上移 + opacity 降低。
- 左右内容暂时保留。

用户已明确：

> CTA 不参与吸入。

Heading 也不需要被球吸入。

---

# 17. Phase B — Attraction

```text
p = 0.10–0.30
```

行为：

- 左侧 cards 开始受到球体“引力”。
- 右侧 Shared Plan 也轻微向球靠近。
- Sphere 仍保持活体运动。
- Sphere brightness / density 只轻微增强。

不要一上来就碎。

必须先让用户看到：

```text
everything is being pulled inward
```

---

# 18. Phase C — 具象吸入

用户已确定：

> 使用具象吸入，不只是 fade。

推荐三层：

## 18.1 UI Compression

```text
scale ↓
text opacity ↓
card shell 收缩
```

## 18.2 Fragmentation

卡片边缘产生受控碎片：

- 小点。
- 短片。
- 微型方块。
- 少量文字残影可选。

## 18.3 Particle Pull

```text
card origin
→ curved pull path
→ sphere boundary
→ sphere interior
```

---

# 19. 左侧卡片吸入顺序

不要一起吸。

建议：

```text
secondary / outer inputs
→ primary inputs
→ final private / constraint hint
```

单张卡完整状态：

```text
stable
→ selected
→ compressed
→ fragmented
→ pulled
→ absorbed
```

---

# 20. 右侧 Shared Plan 吸入顺序

右侧代表秩序，因此解构更有序。

建议：

```text
Plan detail rows
→ icons / chips
→ inner structure
→ outer shell
→ fragments
→ sphere
```

不要像左侧一样随机爆散。

---

# 21. 吸入路径

使用：

- curved bezier / spline。
- 路径默认不可见。
- 碎片经过时只出现很短 trail。
- trail 快速衰减。

禁止：

- 长 neon line。
- vortex spiral。
- black-hole accretion disk。
- laser beam。

---

# 22. 球体吸收反馈

随着吸收：

### Density
稍微增加。

### Brightness
foreground 轻微增强。

### Volume
内部粒子略增加，但中心仍有空气。

### Scale
整体逐渐放大。

### Motion
rotation 可以略微减速，增强“质量感”。

不要：

- 越来越亮直到白团。
- bloom 爆发。
- 越吸越快地旋转。

---

# 23. Sphere Expansion

推荐阶段：

```text
p = 0.38–0.68
```

参考 scale：

```text
Scene 1
1.0

Mid transition
1.35–1.65

Pre-shatter
1.8–2.4
```

实际按 viewport 调整。

“变大”必须来自整体空间尺度：

- group scale。
- camera composition。
- scene framing。

不要只增加 point size。

---

# 24. Pre-shatter

```text
p = 0.62–0.72
```

此时：

- 左右 UI 基本消失。
- CTA 已消失。
- Heading 不再是主视觉。
- Sphere 主导整个画面。
- Sphere 开始轻微失稳。

失稳方式：

- outer noise amplitude 轻微增强。
- 少量点脱离原半径。
- rotation 稍减速。
- silhouette 局部失去稳定。

不要直接爆炸。

---

# 25. Shatter

用户已确定：

> 球先爆散 / 破碎，再聚成世界地图。

推荐：

```text
p = 0.70–0.84
```

这里的“破碎”不是玻璃爆炸。

定义：

```text
a volumetric coordination field losing spherical constraint
```

表现：

- 粒子逐步失去球形约束。
- 不同区域先后解构。
- 粒子带方向性散开。
- 整体由球形过渡成更宽的水平粒子场。

---

# 26. 禁止的 Shatter

不要：

- fireworks。
- 同步 radial explosion。
- 所有点等速向外。
- 强 shockwave。
- portal。
- black hole。
- 红黄爆炸。
- 强 motion blur。

---

# 27. Shatter Motion Field

推荐逻辑：

```text
sphere base position
+
radial release
+
directional low-frequency noise
+
horizontal redistribution bias
```

因为下一状态是世界地图，所以散开必须开始向：

```text
sphere
→ wider horizontal particle field
```

过渡。

---

# 28. World Map Reassembly

```text
p = 0.80–1.00
```

地图是：

```text
pure visual point map
```

不是：

- Google Maps。
- GIS。
- dashboard。
- route planner。
- clickable map。

---

# 29. Map Visual

继续使用 sphere 同一套粒子语言：

- Pale blue points。
- Point-size variation。
- Density variation。
- Airy silhouette。
- Very subtle depth 可选。

不显示：

- 国家边界。
- 国家名称。
- 经纬线。
- 复杂 route。
- city labels。

地图必须：

```text
recognizable
but
airy
```

---

# 30. Map Formation

建议：

```text
1. Shattered cloud
2. First target attraction
3. Continental masses become visible
4. Excess particles fade / become atmosphere
5. Point world map settles
```

禁止：

```text
sphere disappears
CUT
map appears
```

---

# 31. Map Target 生成

推荐从本地 silhouette asset 采样：

```text
local SVG / PNG world silhouette
→ sample visible area
→ target point positions
```

如果项目已有适合的 world-map asset，可复用。

不要依赖 remote map API。

---

# 32. Particle Reuse

优先复用现有球体 point system。

后续每个粒子可拥有：

```text
sphereBasePosition
shatterVector
mapTargetPosition
transitionSeed
```

可增加：

```text
uStoryProgress
uSphereExpansion
uShatterProgress
uMapProgress
```

尽量不要：

```text
destroy sphere canvas
→ create unrelated new map canvas
```

因为会破坏连续性。

---

# 33. Map Point Allocation

不要求所有 3800 点都成为地图。

建议：

```text
60–85%
→ map targets

15–40%
→ atmosphere / residual / fade
```

避免地图太实。

---

# 34. Hero → Section 2

地图形成后：

```text
Transition sticky stage releases
↓
World Map becomes Section 2 visual
↓
Section 2 HTML content enters naturally
```

必须感觉：

```text
same particles
same space
same story
```

而不是：

```text
animation ended
new page starts
```

---

# 35. Motion Hierarchy

建议强度：

```text
Scene 1           3/10
Entrance          4/10
Absorption        7/10
Sphere Expansion  7/10
Shatter           8/10
Map Reassembly    6/10
Map Resting       2/10
```

不能全程都是 8/10。

---

# 36. Scroll 技术方向

推荐：

- scroll progress 驱动 transition。
- 不使用 `window.addEventListener("scroll") + React setState` 每帧更新。
- 使用 GSAP ScrollTrigger 或等效高性能 scroll timeline。
- Three.js 值写 refs / uniforms。
- DOM UI 使用 transform / opacity。
- React 不做 frame-level rerender。

---

# 37. Sticky 使用边界

允许：

```text
Transition Scene
→ sticky / pinned
```

不允许：

```text
整个 Natural Hero
→ 固定一屏
```

最终结构：

```text
Hero: natural flow
Transition: scroll storytelling
Section 2: natural flow
```

---

# 38. Transition Scroll Length

不要将它理解为“第一页固定长度”。

transition 只是 scroll driver。

初始测试：

```text
200–280vh
```

最终根据视觉 review 调整。

---

# 39. Tooltip 在 Transition 中

Scene 1：

```text
enabled
```

Transition 开始后：

```text
disabled
```

建议：

```text
p > 0.08
→ hide tooltip
→ disable new hover
```

避免 cinematic transition 中 tooltip 漂移。

---

# 40. Entrance 与 Scroll Story 分离

必须区分：

```text
Entrance
→ time-driven

Scroll Story
→ scroll-driven
```

不要让两套 timeline 同时抢同一个 transform。

---

# 41. 背景

全过程维持 pale-blue atmospheric background。

允许随 progress：

```text
Scene 1
soft / bright

Absorption
slightly focused around sphere

Shatter
slightly cleaner

Map
airy again
```

禁止：

- 深色切换。
- black-hole backdrop。
- purple glow。
- strong vignette。

---

# 42. 明确禁止白色大框

整个 Hero 不再加入：

- large white visualization panel。
- giant rounded container。
- boxed dashboard surface。

左、中、右直接存在页面空间里。

---

# 43. Visual Layering

建议：

```text
z0 atmosphere
z1 micro background texture
z2 sphere / map WebGL
z3 inputs / shared plan DOM
z4 tooltip / annotation
z5 heading / CTA
```

Transition：

```text
z3 UI
→ fragments
→ absorbed

z2 sphere
→ dominant
→ shattered
→ map
```

---

# 44. Desktop

完整体验：

- 5–7 inputs。
- Living sphere。
- Shared Plan。
- Hover。
- Pointer parallax。
- Entrance stagger。
- Absorption。
- Expansion。
- Shatter。
- Map reassembly。

---

# 45. Tablet

简化：

- 4–5 inputs。
- Shared Plan 减少 detail。
- Sphere 保持完整。
- Shatter point count 降低。
- Map point count 降低。
- 仍保留 scroll story。

---

# 46. Mobile

不缩成桌面三栏。

第一幕改为：

```text
Heading
↓
Input cluster
↓
Sphere
↓
Shared Plan
↓
CTA
```

Transition 仍可：

```text
CTA fade
→ inputs / plan absorbed
→ sphere grows
→ shatter
→ map
```

Mobile：

- 关闭 pointer parallax。
- Hover 改 tap 或降低重要度。
- particle / fragment 数量降级。

---

# 47. Reduced Motion

`prefers-reduced-motion`：

第一幕：

- 静态 sphere。
- 左右 UI 直接进入。
- focus / hover 可保留。

Transition：

不要强吸入 / shatter。

替代：

```text
Left / Right fade
→ Sphere slight scale / crossfade
→ Point Map crossfade
```

仍保持语义可理解。

---

# 48. Performance

必须：

- Canvas 不可见时暂停。
- DPR cap。
- 低性能降低 particle count。
- Map point count 分级。
- Shatter 尽量 GPU / typed arrays / shader。
- 不为大量 fragments 创建 React components。
- DOM fragments 使用有限 pool。

---

# 49. Card Fragmentation 技术边界

“具象吸入”不等于真的把每张卡拆成几百个 DOM。

推荐：

### Card shell

```text
scale / opacity / clip-path
```

### Fragment overlay

每张主要卡：

```text
8–24 fragments
```

或使用 canvas / pooled fragments。

最终 fragments 转换成 sphere point language。

---

# 50. Shared Plan Fragmentation

不要逐字拆。

建议拆：

```text
Rows
Icons
Small chips
Card shell
```

足够表达“解构”。

---

# 51. Story State

建议：

```ts
type HeroStoryState =
  | "resting"
  | "cta_exit"
  | "attracting"
  | "absorbing"
  | "sphere_expanding"
  | "pre_shatter"
  | "shattering"
  | "map_forming"
  | "map_resting"
```

State 只用于：

- major lifecycle。
- debug。
- accessibility hooks。

不要每帧 React state。

---

# 52. Current Sphere Boundary

Scene 1 阶段不重写现有球。

保留：

- native Three.js。
- current BufferGeometry。
- current ShaderMaterial。
- current particle count。
- current hover subset。
- current tooltip refs。
- current depth。
- current slow motion。
- current natural Hero layout。

只有实现 absorption / shatter / map 时，才增加 transition attributes / uniforms。

---

# 53. 后续 Three.js Transition Data

允许新增：

```text
aSphereBase
aShatterVector
aMapTarget
aTransitionSeed
```

uniform：

```text
uStoryProgress
uSphereExpansion
uShatterProgress
uMapProgress
```

第一轮 Scene 1 不要求全部实现。

---

# 54. Accessibility

必须保留：

- 真正 HTML H1。
- 真正 CTA。
- DOM input labels。
- Shared Plan 可用辅助文本说明。
- Canvas 作为辅助视觉。
- Reduced motion。
- Keyboard interaction 不依赖 WebGL hover。

---

# 55. Anti-Slop

禁止：

- AI purple glow。
- 三张完全相同 feature cards。
- 左右完全镜像。
- 巨大白色 bento panel。
- network lines everywhere。
- futuristic HUD。
- fake analytics。
- “AI processing…” generic loader。
- AI brain / chip。
- orbit rings。
- planet latitude / longitude。
- Matrix glyphs。
- neon portal。
- glassmorphism everywhere。
- 所有元素一起吸入。
- 所有粒子同步爆炸。
- Map 突然 cut in。
- CTA 被吸入。
- Heading 被碎裂。

---

# 56. Scene 1 验收

- [ ] H1 清楚。
- [ ] 左侧明显表达多个独立旅行输入。
- [ ] 左侧不是 6 张完全相同卡片。
- [ ] Sphere 保持当前活体感。
- [ ] Sphere 是主视觉。
- [ ] 右侧 Shared Plan 明显比左侧更有秩序。
- [ ] 没有大白框。
- [ ] Layout 自由、现代、editorial。
- [ ] 左右有轻微 stagger。
- [ ] Hero 仍然 natural flow。
- [ ] CTA 不被硬塞进 first viewport。
- [ ] Mobile 不强行缩桌面三栏。

---

# 57. Absorption 验收

- [ ] CTA 最先淡出。
- [ ] Heading 只 exit，不被吸入。
- [ ] 左侧 cards 有 selected → compressed → fragmented → pulled → absorbed。
- [ ] 右侧 Shared Plan 有更有秩序的解构。
- [ ] Fragment 数量受控。
- [ ] Pull path 有曲线。
- [ ] 没有 black-hole vortex。
- [ ] 没有长 neon trail。
- [ ] Sphere 会对吸收产生反馈。
- [ ] Transition 开始后 Tooltip 关闭。

---

# 58. Sphere Expansion 验收

- [ ] 球是真的整体变大。
- [ ] 不只增加 point size。
- [ ] Sphere 越来越成为唯一视觉中心。
- [ ] Motion 不突然加速。
- [ ] 不出现强 bloom。
- [ ] 中心仍保留空间感。

---

# 59. Shatter 验收

- [ ] 是有方向的解构，不是 fireworks。
- [ ] 不是所有点同步 radial explosion。
- [ ] 球形约束逐渐失去。
- [ ] 粒子开始覆盖更宽横向空间。
- [ ] Palette 保持 Pale Blue。
- [ ] 无 shockwave / portal / black hole。

---

# 60. World Map 验收

- [ ] Map 延续 sphere point language。
- [ ] Map 是 point-based。
- [ ] 大陆可识别。
- [ ] 无国家边界。
- [ ] 无国家名称。
- [ ] 无 GIS dashboard。
- [ ] 不需要点击。
- [ ] Map 是渐进形成。
- [ ] Map resting 足够安静，可以承接第二章节。

---

# 61. Hero → Section 2 验收

- [ ] Hero 没有明显“盒子结束”。
- [ ] 背景连续。
- [ ] Sticky transition 最终自然 release。
- [ ] Map 成为下一 section 视觉。
- [ ] Section 2 HTML 独立可读。
- [ ] 用户感觉仍在同一个故事中。

---

# 62. 实现阶段

## Phase 1 — Scene 1 Composition

只做：

- Heading。
- Left Inputs。
- Existing Sphere。
- Shared Plan。
- CTA。
- Natural-flow。
- No white frame。

先确认：

```text
composition
hierarchy
scale
spacing
balance
```

---

## Phase 2 — Entrance

增加：

- Left stagger。
- Right stagger。
- Sphere scene reveal。

---

## Phase 3 — Scroll Absorption

增加：

- CTA fade。
- Heading exit。
- attraction。
- compression。
- fragmentation。
- pull。
- sphere feedback。
- sphere expansion。

---

## Phase 4 — Shatter

增加：

- pre-shatter instability。
- constraint release。
- directional dispersion。

---

## Phase 5 — Map Reassembly

增加：

- map targets。
- particle reassembly。
- map resting。
- next-section release。

---

# 63. 建议组件结构

当前球体：

```text
frontend/app/IdeaSphereCanvas.tsx
frontend/app/ui.tsx
frontend/app/globals.css
```

后续可逐步拆分：

```text
frontend/app/hero/
├── HeroStory.tsx
├── HeroInputCluster.tsx
├── HeroSharedPlan.tsx
├── HeroTransitionStage.tsx
├── IdeaSphereCanvas.tsx
├── heroStoryProgress.ts
└── heroStoryTypes.ts
```

不要求立即迁移文件。

---

# 64. 职责划分

```text
HeroStory
→ page composition

HeroInputCluster
→ independent input DOM UI

HeroSharedPlan
→ shared plan DOM UI

IdeaSphereCanvas
→ sphere / shatter / map particles

HeroTransitionStage
→ sticky transition + orchestration

heroStoryProgress
→ progress mapping / GSAP / scroll values
```

---

# 65. GSAP 使用边界

GSAP 只负责需要 scroll storytelling 的部分：

- pin / sticky coordination。
- scroll progress。
- DOM fragments。
- CTA / Heading exit。
- scene timing。

Three.js 负责：

- sphere particle motion。
- sphere expansion。
- shatter point field。
- map reassembly。

不要用 GSAP tween 数千 DOM 粒子。

---

# 66. 暂时不做

不加入：

- destination routes。
- city markers。
- map labels。
- functional map interaction。
- AI chat。
- itinerary editor。
- voting UI。
- drag-and-drop。
- live backend data。
- map zoom controls。

当前目标是首页视觉叙事。

---

# 67. 最终视觉判断

实现后依次检查：

```text
1. 第一眼是否知道这是多人一起规划旅行？
2. 左边是否真的像“很多人的不同声音”？
3. 球体是否仍然是整个 Hero 的视觉核心？
4. 右边是否明显比左边更有秩序？
5. 整体是否不像普通 SaaS 三栏？
6. 页面是否保持空气感？
7. 滚动后是否真的感觉元素被“吸收”？
8. 球放大是否像一个有质量的空间体？
9. Shatter 是否优雅而非爆炸？
10. Map 是否像同一批粒子重新组织而来？
11. Hero 到第二章节是否没有明显断点？
12. 整个体验是否属于 TripSync，而不是通用 WebGL demo？
```

如果第 12 点回答是否定：

```text
设计仍未完成。
```

---

# 68. 最终结论

首页不再采用：

```text
Standalone Sphere Demo
```

最终采用：

```text
Many Voices
    ↓
Living Coordination Sphere
    ↓
Shared Plan
    ↓
Concrete Absorption
    ↓
Sphere Expansion
    ↓
Controlled Shatter
    ↓
Point-based World Map
    ↓
Next Product Chapter
```

这是首页开场的最终叙事骨架。

**第一阶段只先实现并评审 Scene 1。**

任何 absorption、shatter、world-map reassembly，都必须建立在 Scene 1 构图通过视觉评审之后。
