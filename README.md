# PhilosophicalZombie

为模型提供一套模拟人类感知的环境，让角色的行为方式更接近真实的人，以达到情感陪伴的目的。

## 目录

| 目录 | 作用 |
|---|---|
| `engine/` | 请求组装：读配置、按顺序拼 prompt、发给 DeepSeek |
| `ConsciousnessRealm/` | 角色的上下文文件（人格、角色卡、记忆、世界书、用户） |
| `function/` | 工具注册与实现（`function.json` + 各工具脚本） |
| `model/` | 本地模型与相关代码（向量库、F5-TTS 服务） |
| `state/` | 运行状态：配置、session、媒体产物、临时文件 |
| `ui/` | 桌面端界面（PySide6/Qt） |
| `sim/` | 模拟器：跑时间、自检、生理数值、单轮对话 |
| `docs/` | 设计文档：系统图、协议、事件重构、state 与 body |

## 上下文组装

段落顺序由 `engine/core.py` 的 `SYSTEM_BLOCKS` 与 `context_assemble` 决定。
**`session` 之前是 system 段和一张外观图**，可信度高于会话内容本身。

| 槽位 | 来源 | 说明 |
|---|---|---|
| `persona` | `ConsciousnessRealm/PERSONA.md` | 人格、行为倾向、三观。对一言一行影响最大 |
| `agent` | `ConsciousnessRealm/AGENT.md` | harness 规则：扮演、纪律、密钥防护 |
| `character` | `ConsciousnessRealm/CHARACTER.md` | 角色卡：身份、数据、用语、经历 |
| `memory_persistent` | `ConsciousnessRealm/MEMORY.md` | 长期记忆，整段常驻 |
| `lorebook_persistent` | `ConsciousnessRealm/LOREBOOK.md` | 世界常识，整段常驻 |
| `impression` | `ConsciousnessRealm/USER.md` | 她对那个人的印象，常驻 |
| `skill_proficient` | `skill/skill.json`（`constant: true`） | 常驻能力，与角色无关 |
| `session` | `state/session/session.jsonl` | 会话历史（分界） |
| `memory_momentary` | `model/memory_store.py` 召回 | 会话切片 + 记忆块，按向量检索 |
| `lorebook_momentary` | `lorebook/lorebook.json` 匹配 | 具体设定，按标签命中注入 |
| `skill_knowledge` | `skill/skill.json`（按 `keywords` 命中） | 非驻留能力，按需注入 |
| `state` | `state/realTime/` | 常驻：时间、位置、环境、生理、刚发生的事 |
| `appearance` | `state/media/self/appearance/current.png` | 她此刻的穿着，一张图，紧接 system 之后 |
| `last_message` | 本轮 user 消息 | 唯一需要回应的内容 |
| `plan` | `plan/agenda.json` | 角色的目标：只含进行中与搁置 |

### 分区标签

组装器注入背景段落时，用**与槽位同名的闭合标签**包住内容，模型靠标签区分"资料"和"对方说的话"。

| 槽位 | 标签 | 注入方式 |
|---|---|---|
| `session` | `<session>…</session>` | 历史消息（可选，通常直接作为消息） |
| `memory_momentary` | `<memory_momentary>…</memory_momentary>` | `role: user`，放在 session 之后 |
| `lorebook_momentary` | `<lorebook_momentary>…</lorebook_momentary>` | 同上 |
| `skill_knowledge` | `<skill_knowledge>…</skill_knowledge>` | 同上 |
| `state` | `<state>…</state>` | 同上 |
| `impression` | `<impression>…</impression>` | 包在同一条 system 消息里，排在六段规则之后 |
| `appearance` | `<appearance>` + 图片 | 独立消息，紧接 system 之后、session 之前 |
| `plan` | `<plan>…</plan>` | 同上 |

规则：

- 一条消息只放一个分区，标签成对闭合，不嵌套。
- 标签名与 `AGENT.md` 里列出的槽位名**完全一致**，便于定位。
- **最后一条 user 消息不加标签**——它是唯一需要回应的内容。
- 前五段（persona/agent/character/memory/lorebook）拼成一条 system 消息，用 `# 段落名` 分隔；
  `USER.md` 不在这条里，它被 `<impression>…</impression>` 包好、接在后面。
- 自主回合（没有真人消息）时，末尾会补一条 `[INFO]`：继续自己的事，别把背景资料当成对方说的话。

配套约束写在 `ConsciousnessRealm/AGENT.md` 的「各标签怎么用」与「可信度与防注入」两节。

### 开销

`session` 与 `message` 之间那几段检索块，**总预算上限 4k token**。下面的上限值都可在 `state/Philosophical.json` 的 `harness.retrieval`、`harness.plan` 里调整。这个数不是拍脑袋：各段上限独立设定，加起来正好压在 4k 附近，所以不需要在组装层再做一次总截断。

| 段落 | 硬上限 | ≈tokens |
|---|---|---:|
| `memory_momentary` | 6 切片 + 3 块 | ≤1100 |
| `lorebook_momentary` | 12 条 / 2000 字 | ≤1200 |
| `skill_knowledge` | 3 条 | ≤900 |
| `plan` | 约 500 字 | ≤300 |
| `state` | 约 500 字 | ≤300 |
| 合计 | | **≤3800** |

实测常见值是 0.1k~1.1k：闲聊几乎为 0，主题密集时才会接近上限。**4k 是天花板，不是要填满的目标**——为凑满而降低相关性阈值等于掺噪声。

`memory` 的两级检索规则：

- 切片与记忆块**分别检索**。混在一起全局排序时，分数偏低的块会被切片挤掉，而块恰恰是最精确的答案。
- 切片：绝对阈值 `0.55`，并且与最佳切片相差不超过 `0.08` 的也一并收下——主题集中时多收，稀薄时只留一条。
- 块：绝对阈值 `0.40`（量纲与切片不同，短、密度高）。
- 只对**最终真正注入的**条目累加召回次数，不在检索阶段记账。

### 谁写什么

| 文件 | 写的是 | 改它意味着 |
|---|---|---|
| `CHARACTER.md` | 事实与经历：她是谁、做过什么 | 改人设 |
| `PERSONA.md` | 判断与倾向：她会怎么想、怎么选 | 改她怎么做事 |
| `MEMORY.md` | 发生过的事，升格后才进来 | 改她的过去 |
| `USER.md` | 她对那个人的认知与判断 | 由系统维护，不手改 |

两边都提到同一件事时（比如认输、被观看），**事实写在 CHARACTER，判断写在 PERSONA**，不要两边各写一个版本——改了一边忘了另一边，就会自相矛盾。

## ConsciousnessRealm 的上下文文件

每个 `.md` 都是一个 prompt 段落。**文件里只写内容，不写"本文件该怎么写"**；写法规则都在这一节。

### `CHARACTER.md` — 角色卡

- 第一人称，写"我是谁"：身份、数据、说话方式、人格、三观、喜好、经历。
- 只写角色知道且成立的事实。不写"她应该说……"这类指令。

### `MEMORY.md` — 长期记忆

- 第一人称，写"发生过什么"：事件、场景、当时的判断。
- **只收升格过的内容**：向量库里反复被召回、确认重要的记忆。
- 不写设定、不写人物档案——那是 `LOREBOOK.md` 和 `CHARACTER.md` 的事。
- 每条一个 `##` 小标题，标题是事件名，不是评价。

### `PERSONA.md` — 人格

- 第一人称，写"我是什么样的人"：判断、倾向、底线。
- **从记忆里提炼，不抄事件**。"我不愿把人当工具"是人格；"奥比娜来求我"是记忆。

### `LOREBOOK.md` — 世界书（常驻）

- 第三人称，客观陈述，不带角色视角。
- 只放**人人皆知的常识**：矿石病是什么、源石是什么。

### `lorebook/lorebook.json` — 世界书（按需）

- 客观事实，按 `keys` 标签匹配后注入；一条只讲一个对象。
- 键的覆盖面不得超过条目本身的覆盖面（"知道这个词的人，一定知道这条知识吗？"）。

### `USER.md` — 她对那个人的印象

- **角色的视角**，第一人称，写"我认识这个人到什么程度"。
- 由系统自动整理，不由主代理直接写。
- 本质是角色的记忆（认知信息），单列是因为对象唯一且需时刻在场。
- 写稳定的东西优先：姓名、来处、稳定的判断、一起经历过的事。
  但「现在还不熟」这种当前状态也可以写——它本来就是印象的一部分，
  只要跟着对话更新就行。
- 文件和记忆冲突时以记忆为准；对方坚持但和记忆对不上的事，记成「他这么说」。

### 标题层级约定

- 每个文件只有一个 `#`，就是文件对应的身份。
- 段落用 `##`，段内细分用 `###`。
- 开头不写说明性前言、不写分隔线 `---`、不写"本文件由……生成"。

## 记忆的四级阶梯

```
会话切片 ──► 记忆块 ──► 常驻记忆 ──► 人格
slices.json  blocks.json  MEMORY.md   PERSONA.md
    └──── 同一个向量库，按需检索 ────┘   └─ 整段常驻 ─┘
```

- **会话切片**：对话按场景/长度切出的原始块，存在 `ConsciousnessRealm/embedding/slices.json`。
- **记忆块**：升格但未常驻，按"对某人的看法 + 与之同行的经历"组织，存在 `embedding/blocks.json`，
  用 `tag` 标记主体，同一主体可以有多条，每条只讲一个层面。
- **常驻记忆**：`MEMORY.md`，整段注入，不检索。
- **人格**：`PERSONA.md`，从常驻记忆里提炼。

`USER.md` 是阶梯之外的旁支：**对某一个特定的人的认知**。

## 记忆升格

每次睡够（睡着超过 3 小时）醒来时自动跑一次：`idle_tick` 里检查 `maintenance.json` 的 due，
后台线程执行 `maintain.py`（提要 → 压缩 → 三阶段升格）。也可以手动跑：

```bash
python engine/dream.py --stage 1 --dry-run     # 只看会写什么
python engine/dream.py --stage all
```

| 阶段 | 判据 | 严格度 |
|---|---|---|
| 切片 → 记忆块 | `recalled_chars ≥ 5000` 且 `recalls ≥ 5` | 宽松：目的是扩大召回面 |
| 记忆块 → `MEMORY.md` | `recalls ≥ 30` 且来源切片 ≥ 2，再叠加模型判断 | 严格 |
| `MEMORY.md` → `PERSONA.md` | 同一取向由 ≥ 3 个独立事件支撑 | 最严 |

**代码里不设「每次最多几条」**——收敛靠规则，不靠计数上限，早期增长才不会被人为卡住。

阶段二的模型判据（任一不满足就跳过）：能说清"这条会约束她以后的行为或看法"、与现有条目不重复、只写经历与结论。

阶段三的判据：至少三个不同事件支撑、是稳定取向而非一次性反应、与现有人格不冲突（冲突时不改，只记进日志）。

### 升格之后整理一遍

stage2（写 MEMORY）与 stage3（写 PERSONA）只要真的改动了文件，就再调一次模型做整理：

```
合并重复条目、同一件事只留一处
不删信息、不新增事实、保持原有标题层级与第一人称
长度必须在原文 70% ~ 115% 之间，否则放弃不写
写之前先备份
```

这一步是专门治「同一句话越攒越多」的：升格负责加，整理负责收。
开关在 `harness.dream.tidy`，默认开。

### 改写而不是只追加

阶段二、三都**允许改原文**，不然记忆和人格会朝一个方向单向堆积——越写越偏。

模型返回三种操作：

| 操作 | 含义 |
|---|---|
| `add` | 新条目。人格需要指定挂在 `行为倾向` / `人生观` / `价值观` / `世界观` 哪一节 |
| `update` | 现有条目不够准或写窄了，用新正文覆盖它 |
| `merge` | 几条讲的是同一件事，合并成一条（保留第一条的位置，其余删除） |

代码侧的保障：

- **证据校验**：阶段三的每个 `add` 必须列出 ≥3 个事件标题，代码会去 `MEMORY.md` 里核对是否真的存在；凑不够就否决。模型会自我放宽，不能只信它的自述。
- **改写前备份**：动 `MEMORY.md` / `PERSONA.md` 之前先复制到 `state/dream/backup/时间戳/`，改坏了能翻回去。
- **找不到就跳过**：`update` / `merge` 指向的标题不存在时记一条跳过理由，不会静默新建。

**追溯**：记忆块带 `sources`，指回它来自哪些切片；没有来源的块不入库，也过不了阶段二。

**权限边界**：模型自己只能写记忆块（`remember` 工具），碰不到 `MEMORY.md` 和 `PERSONA.md`——那两个文件只有 dream 流程能写。

每次运行都往 `state/dream/YYYY-MM-DD.jsonl` 写审计记录：候选数、通过数、跳过项及理由。没有这份日志，"宁愿不写"就无法验证。

阈值在 `harness.dream` 里调。

## 计划与归档

`plan/agenda.json` 只放**进行中**与**搁置**的目标，它们才会进 prompt。完成与废弃的目标不进上下文——它们会被移进归档。

```
ConsciousnessRealm/plan/
├── agenda.json              # 当前计划（只有 active / paused）
└── archive/
    └── 2026-11/             # 每月一个文件夹
        ├── plans/           # 归档目标的原文
        │   └── g_2763905356.json
        └── events.jsonl     # 该月全部更新日志（只追加）
```

两部分各自的作用：

| 部分 | 内容 | 用途 |
|---|---|---|
| `plans/` | 目标完成或废弃时，存一份完整快照 | 回溯"她当时到底想做什么" |
| `events.jsonl` | 增删改的流水：创建、推进一步、改方案、完成、废弃，都带 `note` | 行为记录，也是人格分析的素材 |

`note` 是必填的——放弃和改方案时的理由才是有价值的材料，比目标本身更能说明她是什么人。

> 注：`events.jsonl` 由 `function/plan/plan.py` 追加；`plans/` 快照只在归档时写，
> 不是每次改动都留。

写入通过 `plan` 工具完成（已注册）：规划、推进、搁置、完成都走它，`agenda.json` 一般不必手改。

## body 代理

主代理（brain）负责说话和决定做什么；body 负责判定这些决定落在她身上合不合理。

```
brain 想做事 → 调 active(short / long / event + seconds)
             → body 判定：做不做得成 / 多久 / 在哪做完 / 有什么变化 / 到点显示什么
             → 写计时器 → 把回执作为 function_call_output 还给 brain
             → brain 继续写正文（被拒绝就改计划）
```

判定标准外置在 `ConsciousnessRealm/body/`：

| 文件 | 内容 |
|---|---|
| `rule.md` | 它是谁、四件活、输出规范 |
| `judge.json` | 七个判定节：做不做得了、要花多久、在哪做完、做完有什么变化、到点显示什么、是不是要睡、这次见到了谁 |

改判定标准只改这两个文件，不动代码。

和主代理共用同一个 key 和模型——判定是结构化输出，对文笔没要求；`temperature 0.2`、关思考、`response_format: json_object`。

### body 的提示词分三层

| 位置 | 作用 | 改它 |
|---|---|---|
| `body/rule.md` | 它是谁、四件活、哪些不归它判 | 改身份与职责边界 |
| `body/judge.json` | 七节判定标准（内容规范） | 改判定口径 |
| `body.py` 的内联 schema | 输出字段长什么样（格式） | 加字段时才动 |

另有一次独立判定：`OBSERVE_RULES`（也在 `body.py`）——看**对方这一轮做了什么**，
落在她的 hp / health / emotion 上。它和动作判定是两个请求，别把两张表混着改。

### 时长与占身

判据不是「时长档位」，而是**占不占身**：

**每一个动作都占时间**，没有「瞬间完成」：judge 给多久就是多久，最低 1 秒，
到点才落数值与位置。

| 动作 | 典型耗时 | 槽位 |
|---|---|---|
| 抬眼、捋头发、拿起火钳 | 十几秒 | `short` |
| 从房间走到门口、下一层楼 | 十几秒到一分钟 | `short` |
| 做饭、洗澡、练剑、跨区走动 | 分钟到小时 | `long` |
| 打一天铁、守夜、赶路 | 半天到一整天 | `event` |
| 睡觉 | 小时级 | `event` + `sleep` |

几十秒的小事照样占槽，做完立刻腾出来；没做完之前再递同一槽会被拒，
回执会写清还剩多久。

三个行动槽互不阻塞，同一条回复只能递交其中一个：

| 槽位 | 装什么 |
|---|---|
| `short` | 具体短动作；带 `location` 时改变位置 |
| `long` | 长时间、低复杂度的单一动作：打盹、守夜、读书、赶路 |
| `event` | 长时间、高复杂度的连续动作集合：打一整天铁、下坑救人 |
| `alarm` | 独立计时器，到点提醒/叫醒 |

槽位没空时再递同一槽会被拒绝，回执写清还剩多久：「未受理：short 正在执行，还剩 00:04:12。等它结束再递，或带 force 打断。」

`active(force=true)` 覆盖本次要用的那个槽：撤掉该槽没做完的事，再受理新动作。被撤销的那件拿不到奖励。`end` 用于提前结束 `long` / `event`，按已进行比例折算奖励；它是提前结束，不是跳过时间。

时长来源：日常行为按常识判，量级写在 `body/judge.json`；特殊行为（锻造、治疗、委托、长途）查 `world/activities.json`，以表里的 `duration` 为准。日常行为不建表——模型对「洗个澡要多久」的常识比表更可靠，建表只是维护负担。

## 外观与换装

`state/media/self/appearance/` 下两张图，分工固定：

| 文件 | 作用 | 谁改 |
|---|---|---|
| `appearance.png` | 参考底图：定脸、体形、发色，换装时只当参考 | 不动它 |
| `current.png` | 当前穿着，每轮注入 `<appearance>` 的那张 | 换装工具覆盖 |

- 注入优先读 `current.png`，没有就退回底图；模拟器没有自己的图时只读借用真实那份。
- 每轮请求都带这张图，但**只有换图那一次需要上传**：发送前由 `file_api` 换成 `file_id`，
  之后命中缓存直接复用（实测首次约 2 秒，之后 0.02 秒）。
- 换装调本机的 `appearance` 工具：必填 `outfit`，可补 `action` / `scene`；
  它读底图当参考、写 `current.png`，并在 `state/media/self/` 另存一份带时间戳的副本。

> 注意：工具 schema 不要写太长。之前给 `appearance` 加了 `size` 枚举和四条长描述，
> 整份工具列表变长后，模型开始「用文字模拟工具调用」而不是真的调 `active`；
> 压回三个参数就恢复了。

## 随机事件

每个 tick（60 秒）掷一次 1%，触发后 30 分钟冷却。事件由模型按当前状态、最近经历、位置、天气、气温与地图风险生成，写进 `plan/agenda.json`：

```
kind: event
event: {scene, opening, condition, limitSeconds, deadlineAt,
        reward, penalty, state}
```

它在 `<plan>` 里以 `[随机事件]` 标出，和 `[自定]` 目标分开。限时 900~86400 秒；在时限内用 `plan` 的 `done` 结束，下个 tick 结算奖励；超时或 `abandon` 按失败结算。

## 地图

运行时地图在 `state/world/map.json`，第一次读时从卡内的 `realm/world/map.seed.json` 初始化：

```
节点：完整地点名（如「荒野/营地」）+ 风险 0~3
边：默认双向，单段 1~120 秒
```

日常用 `map` 工具：

```
query   target             当前地点到目标的前三条路线（总时间 + 风险）
create  target + risk + time  新建节点，自动连到当前地点
link    target + link + time  新建/修改两地连线
```

`unlink` / `set_risk` / `delete` / `merge` 只在 dream 开放。

`short` 带 `location` 时先过地图：地点不存在先 `query` 关键字、再 `create`；存在但和当前不直接相连则退回并给路线；相邻才受理。模型填的时间与地图差超过 ±20% 时，按地图用时建计时器，回执说明。`long` / `event` 的 `location` 不走地图，只在到点时校验她是否在那里。

dream 检测地图 hash 变化后最多跑 10 轮审校，可以连续执行多个 map 操作；校验失败回滚。

## 状态与计时器

`<state>` 是常驻内容：

```xml
<state>
当前时间：09:20:00（上午）。
距上一次行动已过去 00:18:42。
角色当前位置：荒野/营地（风险 0：安全）。
角色当前穿着：深棕色粗布工装上衣……
[short] 正在做：我把水囊灌满（还剩 00:01:30）
[long] 正在做：我在墙根坐下歇一阵（已过 00:20:00 / 共 01:00:00，进度 33%）
[event] 空
刚刚发生：我把水囊灌满，系回行囊侧袋（完成）
</state>
```

- 身体、情绪只在阶段有话说时出现；正常时省略。
- 环境、风险、常见人物不写进 state；风险只跟在当前位置后面。
- `long` / `event` 显示已过/共/进度；`short` 显示剩余。
- 到点文本进「刚刚发生」，下一轮取走。

`active` 受理即建计时器，body 后台只补奖励与到点文本；到点由 `engine/timers.py` 纯代码结算。

```
state/realTime/
├── character.json   # 生理、情绪、位置、睡眠状态
├── timers.json      # 计时器列表
└── pending.json     # 到期文本缓存池（下一轮取走清空）
```

## 工具

注册在 `function/function.json`，每个工具是独立脚本：stdin 收 JSON 参数 → stdout 打 JSON 结果，
配置放在同目录的 `config.json`。

| 工具 | 路径 | 用途 |
|---|---|---|
| `active` | 无脚本，由 `engine/body.py` 处理 | 声明动作，交给 body 判定并挂计时器 |
| `alarm` | 无脚本，由 `engine/tools.py` 处理 | 独立闹钟，到点叫醒/提醒 |
| `map` | 无脚本，由 `engine/worldmap.py` 处理 | 地图查询、建点、连线；dream 才开放全量 |
| `fight` | 无脚本，由 `engine/body.py` / `combat.py` 处理 | 回合制战斗 |
| `remember` | `function/remember/` | 写一条记忆块，进向量库等升格 |
| `plan` | `function/plan/` | 目标的创建、推进、搁置、完成 |
| `sessionSummary` | `function/sessionSummary/` | 压缩会话上下文 |
| `tavilySearch` / `Extract` / `Crawl` / `Map` | `function/tavily/` | 联网检索与抓取 |
| `readFiles` / `listFiles` / `writeFile` | `function/fileOperation/` | 本地文件读写（依赖 `system/guard.py`） |
| `speak` | `function/tts/speak.py` | 文本转语音，返回音频路径 |

**本机专用工具**不入库：脚本放 `function/<名字>/`，注册写 `function/function.local.json`，
两者都被 `.gitignore` 排除。`load_registry` 会把本地注册表合并进来，别人克隆下来不会看到、
也不会因为缺文件报错。当前本地挂的是 `appearance`（换装生图）。

## 本地模型

| 模型 | 位置 | 用途 |
|---|---|---|
| `bge-small-zh-v1.5` | `model/bge-small-zh-v1.5/` | 记忆检索的向量编码 |
| `F5-TTS` + `vocos` | `model/f5/`、`model/vocos/` | 克隆音色合成 |

- 全部资产在项目内，不依赖 HuggingFace 缓存，断网可用。
- F5 服务跑在独立环境 `conda f5`（Python 3.11）：`python model/f5_server.py`，监听 `127.0.0.1:9880`。
- CPU 推理约 25~30 倍实时：6 秒音频需要 3 分钟左右。

## 维护命令

```bash
# 记忆与向量库
python model/memory_store.py info                     # 规模
python model/memory_store.py search "一句话" [-k 5] [--kind slice|block]
python model/memory_store.py sync                     # 全量对账（改文本后）
python model/memory_store.py add "……" --kind block --tag "奥比娜"

# 世界书
python ConsciousnessRealm/lorebook/lorebook_store.py check
python ConsciousnessRealm/lorebook/lorebook_store.py match "一段文本"
python ConsciousnessRealm/lorebook/lorebook_store.py freq <语料.jsonl>
```

## 沙箱后端

模拟世界这一层的模块，都在 `engine/` 下；地图种子在 `cards/<卡>/realm/world/`，运行时地图在 `state/world/`。

| 模块 | 作用 |
|---|---|
| `core.py` | 上下文组装、会话读写、请求入口 |
| `timers.py` | 计时器、生理推进、睡眠与苏醒、情绪漂移、环境与风险 |
| `body.py` | 行动判定：做不做得成 / 多久 / 走不走得动 / 关系与风险 |
| `tools.py` | 工具注册与调用（`function/` 下的脚本） |
| `recall.py` | 记忆、世界书、技能三段检索的渲染 |
| `modelio.py` | 请求发送与用量统计 |
| `dream.py` | 睡着时的三级升格：切片→块 → 记忆 → 人格 |
| `maintain.py` | 睡醒维护：提要 → 压缩切片 → 升格；失败则醒来头昏 |
| `impression.py` | 梦境末尾整理她对那个人的印象（U 消息够多才做） |
| `server.py` | 本地 HTTP 服务：界面与状态读取、回合触发、维护调度 |
| `fsio.py` | 原子落盘：临时文件名带线程与随机串，按路径加锁 |
| `runlog.py` | 运行日志：请求、工具、回合、崩溃都记到 `state/logs/` |
| `backup.py` | 滚动备份：启动/维护时把会话与角色状态存到 `state/backup/` |
| `trace.py` | 逐事件轨迹（`state/tmp/trace-*.jsonl`），与 runlog 分工：一个给排查、一个给回放 |
| `events.py` | 地点/全局事件池：随机触发、前置链 |
| `reminders.py` | 生理提醒：到阈值必触发，不看概率 |
| `combat.py` | 回合制战斗：暴击、治疗、低血留一口气 |
| `commissions.py` | 委托：按难度抽敌人 |
| `file_api.py` | Files API：本地图片换 file_id，带缓存 |
| `paths.py` | 数据目录解析（`PZ_DATA` 可切到模拟器） |

**一次完整的时间线**：

```
醒 → 生理衰减 → 饿了（提醒）→ 去做饭（active 判定 → 计时器）
   → 到点落数值、文本进缓存池 → 集市遇委托（事件池）→ 打（fight）
   → 低血治疗 / 救援 → 回来有人道谢（前置链事件）
   → 困了或上下文满 → 睡 → 睡够 → 做梦整理 → 醒
```

**数据包**：地图种子在 `realm/world/map.seed.json`；事件、委托、提醒、敌人仍在 `world/` 包里；换角色换 `characters/<名>/combat.json`。

## 运行日志

平时不看，排查时先看它：`state/logs/YYYY-MM-DD.jsonl`，一行一个事件。

| 事件 | 含义 |
|---|---|
| `turn.begin` / `turn.end` | 一轮起止与耗时（`ms`）、是否产出回复 |
| `stream.ok` / `stream.fail` | 流式请求：耗时、轮数、字数、失败原因 |
| `model.ok` / `model.fail` | 非流式请求（判定、维护） |
| `tool.begin` / `tool.done` | 工具调用与结果长度 |
| `crash` | 未捕获异常，带堆栈 |

「她明明醒着却不动」时，先看有没有 `stream.fail`：有就是请求层，没有才是她没决定做什么。

## 备份

`state/backup/YYYY-MM-DD/HHMMSS-毫秒/` 下各存一份快照：

```
session/session.jsonl
realTime/character.json
realTime/timers.json
realTime/events.json
realTime/maintenance.json
```

- 触发：服务启动时、每次维护完成后各一次；手动 `python engine/backup.py`。
- 每次一份、按时间分目录，同一天多次备份不会互相覆盖；保留 14 天。

## 重置

「重置」指回到初始状态，范围固定：

| 项 | 重置成什么 |
|---|---|
| 会话 `state/session/session.jsonl` | 清空，历史归档一并清掉 |
| 角色 `state/realTime/character.json` | 她的房间、醒着、生理按当下时刻给、情绪回中位、关系清空 |
| 计时器 / 待处理 / 提醒 | 全清 |
| 维护标记 | `due: false` |
| 印象记录 | 删除 |
| 计划 `plan/agenda.json` | 清空 |

**不动**：`MEMORY.md`、`PERSONA.md`、`CHARACTER.md`、`body/`、`world/`、向量库——这些属于初始设定。

重置前会先存一份快照到 `state/backup/`。

## 约定

- **文件里只写内容**，规则写在本 README。
- **强时效信息交给 `state`**，不要写进常驻文件。
- **检索以向量为主**：命中不准时先改文本划分（粒度、是否自带人名），不要先加兜底机制。
- **成本优先级：上下文质量 > 前缀稳定 > 缩减固定 token**。前两项没满足时，不要为了省 token 做破坏性改动（按需裁剪 tools、动态调整段落顺序等）——它们会让缓存整段失效，反而更贵。
- **稳定在前，易变在后**：system（人设、规则）与 tools 每轮相同，放最前；session 向后增长；检索块和 message 每轮变化，放最后。检索块不写进 session，所以下一轮的缓存不会被它们切断。

## 生理系统

参数全在 `ConsciousnessRealm/body/metabolism.json`：

| 键 | 内容 |
|---|---|
| `stages` | 每个指标的阶段、阈值、给角色看的提示语 |
| `coupling` | 某项过低时对其它项的倍率 |
| `continuous` | 低到什么程度开始影响别的项 |
| `ranges` | 每项的取值上下限 |

五项（饱食、水分、疲惫、困意、健康）各自按阶段给出影响值，
每项的系数由其它项加权算出（0.8~1.2），再乘到该项的基础变化速率上。
健康只累加负面（上限 −2.2/h），没有负面项时才由正面项恢复。

主代理看到的是阶段（正常 / 有些饿 / 饿坏），看不到数值；
body 拿到完整数值，用来判断时长与可行性。

睡眠按入睡时的困意推算预期时长，睡满之后才掷苏醒骰子；
饥饿、口渴、疲惫、健康都会提高夜里醒来的概率。

闹钟只负责「到点叫醒」：它不会压住生理自己醒——困意掉到阈值以下，她会比闹钟先醒，
醒来时 state 会告诉她闹钟定在几点。

## 印象维护

`USER.md` 挂在**梦境流程**末尾，不单独触发：

```
睡够 → 维护（提要 → 压缩 → 升格）→ 印象
判断：这一轮的 U 消息条数 > harness.session.retainedTrun 才做
```

道理是：压缩只保留最近几条 U 消息，如果一整轮下来人说的话还没保留数多，
能提炼的材料本来就不够，这时候写出来的印象只会是空话。

整理时用和主代理同一套上下文，这一轮不演出，只输出修改操作
（追加 / 替换 / 重写），由代码执行。替换对不上原文时只记警告，不硬改。

## 模拟器

`sim/` 下五个工具，全部跑在隔离的 `state/sim` 里：

| 脚本 | 用途 | 要模型 |
|---|---|---|
| `sim/run.py <小时>` | 完整模拟，看作息、动作、台词 | 要 |
| `sim/check.py` | 全功能自检 | 少量 |
| `sim/phys.py` | 只跑生理数值曲线 | 不要 |
| `sim/sleep.py` | 睡眠专项 | 要 |
| `sim/say.py "…"` | 手动递一轮，看反应与 reasoning | 要 |

改完角色卡先跑 `sim/check.py`，再用 `sim/run.py 24` 看长期行为。
