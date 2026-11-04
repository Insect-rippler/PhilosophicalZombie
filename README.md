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

`state/Philosophical.json` 的 `harness.prompt_type_order` 决定段落顺序，**`session` 之前的都是 system 级**，可信度高于会话内容本身。

| 槽位 | 来源 | 说明 |
|---|---|---|
| `persona` | `ConsciousnessRealm/PERSONA.md` | 人格、行为倾向、三观。对一言一行影响最大 |
| `agent` | `ConsciousnessRealm/AGENT.md` | harness 规则：扮演、纪律、密钥防护 |
| `character` | `ConsciousnessRealm/CHARACTER.md` | 角色卡：身份、数据、用语、经历 |
| `memory_persistent` | `ConsciousnessRealm/MEMORY.md` | 长期记忆，整段常驻 |
| `lorebook_persistent` | `ConsciousnessRealm/LOREBOOK.md` | 世界常识，整段常驻 |
| `user` | `ConsciousnessRealm/USER.md` | 角色对用户的认知，常驻 |
| `skill_proficient` | — | harness 能力，与角色无关 |
| `session` | — | 会话历史（分界） |
| `memory_momentary` | `model/memory_store.py` 召回 | 会话切片 + 记忆块，按向量检索 |
| `lorebook_momentary` | `lorebook/lorebook.json` 匹配 | 具体设定，按标签命中注入 |
| `skill_knowledge` | — | harness 能力 |
| `state` / `last_message` / `plan` | — | 运行时状态 |

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

## 工具

注册在 `function/function.json`，每个工具是独立脚本：stdin 收 JSON 参数 → stdout 打 JSON 结果，
配置放在同目录的 `config.json`。

| 工具 | 路径 | 用途 |
|---|---|---|
| `tavilySearch` / `Extract` / `Crawl` / `Map` | `function/tavily/` | 联网检索与抓取 |
| `readFiles` / `listFiles` / `writeFile` | `function/fileOperation/` | 本地文件读写 |
| `sessionSummary` | `function/sessionSummary/` | 压缩会话上下文 |
| `speak` | `function/tts/speak.py` | 文本转语音，返回音频路径 |

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

## 约定

- **文件里只写内容**，规则写在本 README。
- **强时效信息交给 `state`**，不要写进常驻文件。
- **检索以向量为主**：命中不准时先改文本划分（粒度、是否自带人名），不要先加兜底机制。
