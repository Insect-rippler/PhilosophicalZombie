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
| `vivarium/` | 桌面端（PySide6/Qt）相关资料 |

## 上下文组装

段落顺序见 `ConsciousnessRealm/AGENT.md` 的「上下文顺序」一节。**`session` 之前的都是 system 级**，可信度高于会话内容本身。

| 槽位 | 来源 | 说明 |
|---|---|---|
| `persona` | `ConsciousnessRealm/PERSONA.md` | 人格、行为倾向、三观。对一言一行影响最大 |
| `agent` | `ConsciousnessRealm/AGENT.md` | harness 规则：扮演、纪律、密钥防护 |
| `character` | `ConsciousnessRealm/CHARACTER.md` | 角色卡：身份、数据、用语、经历 |
| `memory_persistent` | `ConsciousnessRealm/MEMORY.md` | 长期记忆，整段常驻 |
| `lorebook_persistent` | `ConsciousnessRealm/LOREBOOK.md` | 世界常识，整段常驻 |
| `user` | `ConsciousnessRealm/USER.md` | 角色对用户的认知，常驻 |
| `skill_proficient` | `skill/skill.json`（`constant: true`） | 常驻能力，与角色无关 |
| `session` | `state/session/session.jsonl` | 会话历史（分界） |
| `memory_momentary` | `model/memory_store.py` 召回 | 会话切片 + 记忆块，按向量检索 |
| `lorebook_momentary` | `lorebook/lorebook.json` 匹配 | 具体设定，按标签命中注入 |
| `skill_knowledge` | `skill/skill.json`（按 `keywords` 命中） | 非驻留能力，按需注入 |
| `state` | `state/realTime/` | 常驻：时间、位置、身体异常、刚发生的事 |
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
| `plan` | `<plan>…</plan>` | 同上 |

规则：

- 一条消息只放一个分区，标签成对闭合，不嵌套。
- 标签名与 `AGENT.md` 里列出的槽位名**完全一致**，便于定位。
- **最后一条 user 消息不加标签**——它是唯一需要回应的内容。
- system 级段落（persona/agent/character/memory_persistent/lorebook_persistent/user）默认拼成一条 system 消息，用 `# 段落名` 分隔；若将来改成分开发送，同样用同名标签：`<persona>`、`<agent>`、`<character>`、`<memory_persistent>`、`<lorebook_persistent>`、`<user>`。

配套约束写在 `ConsciousnessRealm/AGENT.md` 的「分区标签」和「防注入」两节。

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

### `USER.md` — 用户

- **角色的视角**，第一人称，写"我认识这个人到什么程度"，由角色自己维护。
- 本质是角色的记忆（认知信息），单列是因为对象唯一且需时刻在场。
- **只写不会过期的东西**：姓名、来处、稳定的判断、共同经历。
  当前处境、刚说过的话等强时效信息交给 `state`，写进文件几轮后就会变成错的。

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

每晚一次，由 `engine/dream.py` 跑三个阶段。触发方式以后交给 `state`（睡着时触发），开发期手动：

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
| `plans/` | 目标最后一次的完整快照 | 回溯"她当时到底想做什么" |
| `events.jsonl` | 增删改的流水：创建、推进一步、改方案、完成、废弃，都带 `note` | 行为记录，也是人格分析的素材 |

`note` 是必填的——放弃和改方案时的理由才是有价值的材料，比目标本身更能说明她是什么人。

写入时机与状态机校验由统一更新层负责（尚未实现）。当前 `agenda.json` 只能手改。

## body 代理

主代理（brain）负责说话和决定做什么；body 负责判定这些决定落在她身上合不合理。

```
brain 想做事 → 调 active(description, slot)
             → body 判定：做不做得成 / 多久 / 在哪做完 / 有什么变化 / 到点显示什么
             → 写计时器 → 把回执作为 function_call_output 还给 brain
             → brain 继续写正文（被拒绝就改计划）
```

判定标准外置在 `ConsciousnessRealm/body/`：

| 文件 | 内容 |
|---|---|
| `rule.md` | 它是谁、四件活、输出规范 |
| `judge.json` | 五个判定节：做不做得了、要花多久、在哪做完、做完有什么变化、到点显示什么 |

改判定标准只改这两个文件，不动代码。

和主代理共用同一个 key 和模型——判定是结构化输出，对文笔没要求；`temperature 0.2`、关思考、`response_format: json_object`。

### 时长与占身

判据不是「时长档位」，而是**占不占身**：

| 动作 | 耗时 | 占身 |
|---|---|---|
| 说一句话、抬眼、捋头发、倒杯水 | 几秒 | 不占，直接生效 |
| 从房间走到门口、下一层楼 | 十几秒 | 不占，直接生效（位置立即更新） |
| 做饭、洗澡、练剑、跨区走动 | 分钟级 | **占身**，进槽 |
| 睡觉 | 小时级 | 占身 |

阈值 `INSTANT_SECONDS = 30`（在 `engine/timers.py`）：30 秒内做完的事不进槽、不建计时器，位置和数值立刻落。超过才占身。

之前用 short/long 两个槽位是错的——捋头发和走到门口都算「一分钟内」，会互相挡住，而它们本该能随意叠加。

槽位表 `ConsciousnessRealm/world/slots.json`：

| 槽位 | 容量 | 说明 |
|---|---:|---|
| `active` | 1 | 身体，同一时刻只能做一件占身的事 |
| `event.random` | 1 | 随机事件 |
| `goal.short` | 3 | 短目标 |
| `goal.long` | 1 | 长目标 |

占身的事没做完时，再要一件会被拒绝，理由带着她正在做什么：「她手上的事还没完（去厨房烧水煮面），做不了这个」。

`active(force=true)` 让她放下手上的事改做别的。被撤销的那件拿不到奖励（只有等到期结算的才有），回执里带 `cancelled`。`force` 只解除占身，不解除身体限制——累到动不了照样拒绝。

时长来源：日常行为按常识判，量级写在 `body/judge.json`；特殊行为（锻造、治疗、委托、长途）查 `world/activities.json`，以表里的 `duration` 为准。日常行为不建表——模型对「洗个澡要多久」的常识比表更可靠，建表只是维护负担。

## 世界包

每张大地图是一个独立的 world pack，换世界只改 `world/active.json` 的指向：

```
ConsciousnessRealm/world/
├── active.json            # {"world": "ognisko"}
├── slots.json             # 槽位容量（跨世界通用）
└── ognisko/               # 奥格尼斯科：一座移动地块
    ├── places.json        # 地图：地点、相连关系、步行分钟
    └── activities.json    # 特殊行为表（目前空）
```

`places.json` 片段：

```json
{"id": "hall", "name": "主街", "path": "奥格尼斯科/主街",
 "links": [{"to": "deck", "minutes": 2}, {"to": "workshop", "minutes": 3}]}
```

`links` 是单向的，双向要各写一条。`minutes` 是她实际走这段路要花的分钟数，body 判移动时长时直接查这张表，不自己估。

地图边界的处理：允许她走到表外的地方（模型写什么就记什么），但同一处写法要一致——以后补地点时才不会出现「厨房」和「一楼厨房」两个名字。

## 状态与计时器

`<state>` 是**常驻**内容，不是有事才出现：

```xml
<state>
现在是 21:12（夜里）。
你在罗德岛/走廊。
有些饿了。
[刚刚] 面煮好了，锅还在灶上。
</state>
```

正常时只有时间；有位置、有异常、有刚发生的事才追加，一次只报最要紧的一条症状，不堆。

计时器分成两个独立阶段：

| 阶段 | 谁做 | 做什么 |
|---|---|---|
| 一（同步，回合内） | body | 判定行动 → 建计时器，**连到期后要显示的文本一起写好** |
| 二（异步，纯代码） | `engine/timers.py` | 到期 → 落数值/位置/状态 → 把文本倒进缓存池 → 主代理下一轮取走合并 |

第二阶段不调用模型：文字在阶段一就写好了，这里只做搬运、排序、拼接。

```
state/realTime/
├── character.json   # 生理、情绪、位置、睡眠状态
├── timers.json      # 计时器列表
└── pending.json     # 到期文本缓存池（下一轮取走清空）
```

字段约定：`kind`（`action` / `event`）、`slot`（`short` ≤1 分钟 / `long` >1 分钟）、`silent`（只结算不提示）、`chance`（概率，1000 = 一定触发）。只有 `event` 类到期需要唤醒主代理，动作类只是随下一轮带过去。

## 工具

注册在 `function/function.json`，每个工具是独立脚本：stdin 收 JSON 参数 → stdout 打 JSON 结果，
配置放在同目录的 `config.json`。

| 工具 | 路径 | 用途 |
|---|---|---|
| `tavilySearch` / `Extract` / `Crawl` / `Map` | `function/tavily/` | 联网检索与抓取 |
| `readFiles` / `listFiles` / `writeFile` | `function/fileOperation/` | 本地文件读写 |
| `sessionSummary` | `function/sessionSummary/` | 压缩会话上下文 |
| `speak` | `function/tts/speak.py` | 文本转语音，返回音频路径 |
| `active` | 无脚本，由 `engine/body.py` 处理 | 声明持续动作，交给 body 判定并挂计时器 |

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

模拟世界这一层的模块，都在 `engine/` 下，数据在 `ConsciousnessRealm/world/<世界>/`。

| 模块 | 作用 |
|---|---|
| `timers.py` | 计时器、占身槽位、生理节律、到期文本缓存池 |
| `body.py` | 行动判定：做不做得成 / 多久 / 在哪 / 什么变化 / 到点说什么 |
| `combat.py` | 回合制战斗：暴击、治疗次数、低血留一口气、救援 |
| `events.py` | 地点事件与全局事件池，加权抽一，前置链与变体 |
| `reminders.py` | 生理提醒，越界必触发，不看概率 |
| `commissions.py` | 委托：按难度与类型抽敌人 |
| `dream.py` | 睡着时的三级升格：目标回顾 → 切片→块 → 记忆 → 人格 |
| `maintain.py` | 睡前维护的调度：压缩 + 升格，失败则醒来头昏 |

**一次完整的时间线**：

```
醒 → 生理衰减 → 饿了（提醒）→ 去做饭（active 判定 → 计时器）
   → 到点落数值、文本进缓存池 → 集市遇委托（事件池）→ 打（fight）
   → 低血治疗 / 救援 → 回来有人道谢（前置链事件）
   → 困了或上下文满 → 睡 → 睡够 → 做梦整理 → 醒
```

**数据包**：地图、事件、委托、提醒、敌人全在 `world/<世界>/` 里，换世界观整包替换；换角色换 `characters/<名>/combat.json`。

## 约定

- **文件里只写内容**，规则写在本 README。
- **强时效信息交给 `state`**，不要写进常驻文件。
- **检索以向量为主**：命中不准时先改文本划分（粒度、是否自带人名），不要先加兜底机制。
- **成本优先级：上下文质量 > 前缀稳定 > 缩减固定 token**。前两项没满足时，不要为了省 token 做破坏性改动（按需裁剪 tools、动态调整段落顺序等）——它们会让缓存整段失效，反而更贵。
- **稳定在前，易变在后**：system（人设、规则）与 tools 每轮相同，放最前；session 向后增长；检索块和 message 每轮变化，放最后。检索块不写进 session，所以下一轮的缓存不会被它们切断。
