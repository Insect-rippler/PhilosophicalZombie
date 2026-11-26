# PhilosophicalZombie

一个本地运行的角色模拟环境：模型不是按剧本表演，而是通过 `state`、工具、计时器和外部角色文档，持续地做选择、承担后果，并和用户保有一段连续的关系。

当前正式角色是 **陆青禾**：失忆、16~18 岁外表、混沌恶底色、元气外壳，把用户当成专属饲养员。角色设定全部在 `ConsciousnessRealm/` 的外部文档里，不在代码里。

## 目录

| 目录 | 作用 |
|---|---|
| `engine/` | 请求组装、会话、计时器、生理、body 判定、事件、手机、HTTP 服务 |
| `ConsciousnessRealm/` | 角色文档：AGENT、人格、角色卡、记忆、世界书、用户印象、body 数据 |
| `function/` | 工具注册表与各工具脚本 |
| `model/` | 本地向量模型、记忆库、F5-TTS 服务 |
| `state/` | 运行状态：配置、session、计时器、媒体、手机、备份 |
| `ui/` | PySide6 桌面界面：主会话、通信设备、识域、技能、设置 |
| `system/` | 文件工具的安全边界 |
| `docs/` | 设计文档：架构、上下文可信度、协议、事件、realm 格式 |
| `vivarium/` | PySide6 相关笔记 |

## 运行

先配置一次：按 `state/Philosophical.example.md` 里的结构，新建 `state/Philosophical.json`，填入 `payload.api`。

之后双击即可，不依赖终端（脚本只在本机，不入库）：

```text
启动.bat     后端 + 桌面界面；关掉界面时，本次启动的后端一起停
停止.bat     停掉仍在后台跑的后端
```

终端里等价：

```powershell
.\start.ps1              # 后端 + 界面
.\start.ps1 -NoUI        # 只启动后端（前台，Ctrl+C 停止）
.\start.ps1 -Check       # 只检查 Python / 配置 / 端口
.\stop.ps1               # 只停后端
```

不经过脚本时就是原始命令：

```powershell
python engine\server.py
python ui\app.py
```

默认地址：

```text
后端：http://127.0.0.1:8765
界面：读取 PZ_SERVER，默认同上
```

常用环境变量：

| 变量 | 作用 |
|---|---|
| `PZ_DATA` | 把整个运行数据目录换到别处 |
| `PZ_CARD_ROOT` | 使用另一张卡片的 realm 目录 |
| `PZ_SERVER` | 界面连接的后端地址 |
| `PZ_HIDE_USER` | 调试时隐藏用户印象 |

服务只监听本机回环地址，不会自动对外开放。

## 一次回合

```text
界面/调用方 POST /turn
  → core.context_assemble 组装上下文
  → 模型流式回复
  → 有 tool_calls 就执行、把回执追加进 session、再叫模型
  → 直到没有工具，或所有工具都不需要下一次唤醒
```

工具循环上限由 `harness.loop.maxRounds` 控制，默认 8 次。工具输出上限由 `harness.loop.maxToolOutput` 控制。

## 上下文顺序

实际顺序由 `engine/core.py` 的 `build_system()`、`build_background()`、`context_assemble()` 决定：

```text
system: <agent>（只有 AGENT.md）

background（各自一条 user 消息）：
  <persona>
  <character>
  <memory>
  <lorebook>
  <impression>       ← USER.md
  <tools>
  <skill>            ← 常驻技能，没有则为空

<appearance>          ← 图片；有 current.png 用 current，否则用底图
session               ← 历史 JSONL
<memory_momentary>
<lorebook_momentary>
<skill_knowledge>
<state>
<plan>
<voice_check>
<guide>
<opening>             ← 只在新会话/空 session 时出现
[INFO]                ← 用户 loop / 自主 loop
<user>                ← 本轮要回应的话
[INFO]                ← 用户 loop 时可能追加在末尾
```

规则：

- `system` 里只有 `AGENT.md`，不要把人设塞进 system。
- 背景段落、检索段落、state、plan 都是**背景/指导**，不是用户发言。
- `<user>` 才是唯一需要回应的内容；自主 loop 看末尾 `[INFO]`，不要看有没有 `<user>`。
- 标签同名闭合；文件内容不写“本文件该怎么用”，规则在 AGENT 和本 README。
- 检索块只在第一次组装时算，工具循环的后续轮复用同一份，避免重复召回。

### 检索开销

`harness.retrieval` 控制查询轮数、召回条数、阈值和预算。默认上限：

| 段落 | 默认 |
|---|---|
| `memory_momentary` | 6 切片 + 3 块 |
| `lorebook_momentary` | 12 条 / 2000 字 |
| `skill_knowledge` | 3 条 |
| `plan` | 步骤窗口 1，进行中 3，搁置 2 |
| `state` | 约 500 字 |

检索是“按相关性给材料”，不是把预算填满；阈值在 `harness.retrieval` 里调。

## Session 与压缩

会话文件：

```text
state/session/session.jsonl
state/session/history/YYYY-MM.jsonl   # 压缩归档
```

- 一行一条消息；`assistant` 的 `reasoning_content` 会保留在文件里。
- `harness.session.stripReasoning` 只控制**发给模型时**是否剔除历史 reasoning，不改文件。
- `harness.session.compressionThreshold` 是上下文 token 提示阈值。
- `sessionSummary` 工具会把旧轮次归档、切片入库，只留最近 `retainedTrun` 轮和末尾提要。
- 提要由 `engine/maintain.py` 生成；提示词明确要求**只写已经发生的事**，不写现在、不写之后，避免压缩时预写“醒来后/第二天”。
- 睡着超过 3 小时后，维护标记会进入待处理；维护会依次跑提要、压缩、升格、印象整理。失败时不写完成标记，醒来会带负面状态。

`sessionSummary` 的提要是 session 末尾的一条 `assistant` 消息，不是 system。

## 记忆

```text
会话切片 → 记忆块 → MEMORY.md → PERSONA.md
slices.json  blocks.json            常驻      人格
```

- `ConsciousnessRealm/embedding/slices.json`：会话切片。
- `blocks.json`：升格后的记忆块，带 `tag`、`sources`、召回记录。
- `memory_vectors.npz`：向量。
- `MEMORY.md`：整段常驻的长期记忆。
- `PERSONA.md`：从长期记忆提炼的人格。

`model/memory_store.py` 负责向量库读写。主代理只能通过 `remember` 写记忆块；`MEMORY.md` 和 `PERSONA.md` 只由 dream 流程写。

升格阈值在 `harness.dream`：

| 阶段 | 默认判据 |
|---|---|
| 切片 → 块 | `recalled_chars ≥ 5000` 且 `recalls ≥ 5` |
| 块 → MEMORY | `recalls ≥ 30` 且来源切片 ≥ 2，再叠加模型判断 |
| MEMORY → PERSONA | 同一取向由 ≥ 3 个独立事件支撑 |

手动查看：

```powershell
python engine\dream.py --stage 1 --dry-run
python engine\dream.py --stage all
python model\memory_store.py info
python model\memory_store.py search "一句话" -k 5
```

## body：动作和生理

动作判定不在主提示词里，而在 `ConsciousnessRealm/body/`：

| 文件 | 内容 |
|---|---|
| `body/rule.md` | body 是谁、负责什么、不负责什么 |
| `body/judge.json` | 可行性、时长、地点、变化、到点文本等判定标准 |
| `body/metabolism.json` | 阶段阈值、耦合、连续影响、取值范围、睡眠参数 |

`engine/body.py` 负责：

- 接收 `active` 的 `short` / `long` / `event`；
- 判定动作是否成立、需要多久、在哪儿完成、产生什么变化；
- 写计时器，到点由 `engine/timers.py` 纯代码结算；
- 单独观察用户这一轮对她身体和情绪的影响。

`active` 的 `location` 是必填参数。`short` 可以移动；`long` / `event` 只校验到点位置，不移动她。跨地点 `short` 会过地图，模型填的时长与地图差超过 20% 时按地图用时。

`metabolism.json` 的 `stages` 是阶段，`sleep` 段是睡眠参数：`restTo`、`ramp`、`wakeChance`、`recoveryPerHour`。睡眠苏醒阈值跟随 `sleepiness` 的「清醒」段上界，不写死在代码里。

## 随机事件

`engine/events.py` 每个 tick 以 1% 概率触发，30 分钟冷却。事件写入：

```text
ConsciousnessRealm/plan/agenda.json
kind: event
event: {scene, opening, condition, limitSeconds, deadlineAt,
        reward, penalty, state}
```

生成事件时动态读取：

```text
PERSONA.md
CHARACTER.md
LOREBOOK.md
```

再加上当前 `<state>`、最近言行、地点/风险、天气和已有计划。代码里不写死某个人格或某个世界观；换卡只改这些外部文档。

事件条件必须留下至少两种处理方向；如果只写成单一任务，`_condition_open()` 会拒绝并重试。最近 24 小时出现过的事件，触发方式、意象、道具和解决方向都要换。

事件在 `<plan>` 里显示为 `[随机事件]`，和 `[自定]` 目标分开。

## 手机 / 通信设备

手机是独立的一层，不是主会话的换皮：

```text
state/phone/
├─ chat.jsonl
├─ moments.jsonl
├─ diary.jsonl
├─ state.json
└─ media/
```

- 角色名从 `state/realTime/character.json` 的 `name` 读取；
- 用户名从 `state/Philosophical.json` 的 `payload.user_id` 读取；
- 新消息带 `sender: user|self`，不再靠硬编码姓名判断未读；
- 用户可以在通信设备里发文字/图片；
- 角色通过 `phone` 工具查看消息、发消息、写动态/日记；
- 手机消息不叫醒角色，`phone` 默认不唤醒，只有 `wake=true` 才继续下一拍；
- 她的未读和用户的未读分开：`state` 里的 `手机消息（N）` 是她的未读，通信设备角标是用户还没点开的回复；
- phone 的调用和返回正常写入 session，但主会话界面不显示，只在通信设备里显示。

当前界面第一级是手机主屏：消息、动态、日记、状态。动态/日记可看、可点赞；用户不能直接修改角色内容。

## 地图

运行时地图：

```text
state/world/map.json
```

第一次读取时，如果卡内有 `ConsciousnessRealm/world/map.seed.json`，就从种子初始化；没有种子则使用已有地图或空图。

```text
节点：地点名 + 风险 0~3
边：单段 1~120 秒
```

日常用 `map` 工具：

```text
query   target                 当前地点到目标的前三条路线
create  target + risk + time   新建节点并连到当前地点
link    target + link + time   新建或修改连线
```

`unlink` / `set_risk` / `delete` / `merge` 只在 dream 环境开放。

`short` 跨地点时先过地图；地图没有目标先 `query` 再 `create`；不相邻则逐段走。`long` / `event` 的 `location` 只在到点校验。

## 工具

注册表：

```text
function/function.json          公共工具
function/function.local.json    本机工具，不入库
```

虚拟工具由 `engine/tools.py` 直接处理，不需要脚本；其余工具是独立进程，stdin 收 JSON，stdout 回 JSON。

| 工具 | 实现 | 用途 |
|---|---|---|
| `active` | `engine/body.py` | 递交动作，交给 body 判定并挂计时器 |
| `fight` | `engine/body.py` / `combat.py` | 回合制战斗 |
| `alarm` | `engine/tools.py` | 独立闹钟；默认不唤醒，`wake=true` 才继续 |
| `emotion` | `engine/tools.py` | 情绪锁越界 |
| `line` | `engine/tools.py` | 场景台词；默认不唤醒，`wake=true` 才继续 |
| `pause` | `engine/tools.py` | short 槽非空时暂停一拍 |
| `sleep` | `engine/tools.py` / `body.py` | 两段式入睡 |
| `phone` | `engine/tools.py` / `phone.py` | 手机消息、动态、日记、图片 |
| `map` | `engine/worldmap.py` | 地图查询/建点/连线 |
| `plan` | `function/plan/plan.py` | 目标创建、推进、搁置、完成 |
| `remember` | `function/remember/` | 写记忆块 |
| `sessionSummary` | `function/sessionSummary/` | 压缩会话 |
| `appearance` | `function/appearance/`（本机） | 换装生图，覆盖当前外观 |
| `speak` | `function/tts/speak.py` | 文本转语音，返回音频路径 |
| `readFiles` / `listFiles` / `writeFile` | `function/fileOperation/` | 本机文件工具，受 `system/guard.py` 限制 |
| `tavilySearch` / `Extract` / `Crawl` / `Map` | `function/tavily/` | 联网检索与抓取 |

唤醒规则：

```text
active(short)        否
active(long/event)   是
line                 否
line(wake=true)      是
pause（受理）        否
sleep（受理）        否
alarm                否
alarm(wake=true)     是
phone                否
phone(wake=true)     是
map / plan / remember / appearance / emotion / fight / speak /
sessionSummary / 文件与联网工具    是
```

睡着时，虚拟工具都只写回执、不触发下一次唤醒；普通工具仍按自己的规则。

## 界面

```text
主会话      面对面：动作、scene、line、图片
通信设备    手机：消息、动态、日记、状态
识域        AGENT / CHARACTER / PERSONA / USER 等文本
技能        skill.json
设置        模型、API、循环、检索、压缩、调试
```

主会话只渲染面对面内容。手机工具调用和返回仍写入 session，但不会出现在主会话画面。

## 日志、备份、维护

日志：

```text
state/logs/YYYY-MM-DD.jsonl
```

记录 `turn.*`、`stream.*`、`model.*`、`tool.*`、`maintain.*`、`crash` 等事件。排查“醒着但不动”先看有没有 `stream.fail`。

备份：

```text
state/backup/YYYY-MM-DD/HHMMSS-毫秒/
```

服务启动、维护完成、手动任务都会存一份；只备份 session、character、timers、events、maintenance 这些小文件，保留 14 天。

手动任务在设置页或服务端任务里：

```text
backup    备份一次
reindex   重算向量库
repair    修复 session
maintain  跑一次维护
```

## 配置

完整字段看：

```text
state/Philosophical.example.md
state/README.md
```

最常改的：

```text
payload.api
payload.model
payload.temperature
harness.loop.maxRounds
harness.session.compressionThreshold
harness.session.retainedTrun
harness.session.stripReasoning
harness.retrieval.*
harness.plan.*
harness.dream.*
harness.network.verifySSL
tools.tavily.api
```

所有代码读取的配置都有默认值；缺失时回退，不会因为少一个字段直接崩。

## 文档

| 文档 | 内容 |
|---|---|
| `docs/architecture.md` | 整体架构与模块边界 |
| `docs/context-trust.md` | 上下文可信度 |
| `docs/protocol.md` | HTTP/SSE 协议 |
| `docs/events-redesign.md` | 随机事件重构 |
| `docs/state-and-body.md` | state 与 body |
| `docs/realm-format.md` / `realm-schema.md` | 角色文档格式 |
| `ConsciousnessRealm/README.md` | 角色文档怎么写 |
| `model/README.md` | 本地模型与 TTS |

## 约定

- 角色内容只写外部文档，不写进代码；换卡不改代码。
- 强时效信息交给 `state`，不要写进常驻人格/角色卡。
- 事实归 `CHARACTER`，判断归 `PERSONA`，经历归 `MEMORY`，世界常识归 `LOREBOOK`，对用户的认知归 `USER`。
- 文件里只写内容；规则写在 AGENT 和 README。
- 先跑起来、先看日志，再改代码；不要用静默兜底掩盖真实失败。
