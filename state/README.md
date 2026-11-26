# state — 运行时状态

本目录放运行时产物和本机配置。

**运行数据不入库**，只提交：

```text
state/README.md
state/Philosophical.example.json
```

`Philosophical.json`、session、媒体、日志、备份、手机消息都由本机生成。

## 目录

| 路径 | 内容 |
|---|---|
| `Philosophical.json` | 本机运行配置，含 API key，**不入库** |
| `Philosophical.example.json` | 配置模板 |
| `realTime/` | `character.json`、`timers.json`、`pending.json`、`environment.json`、`maintenance.json` |
| `session/` | `session.jsonl` 与 `history/YYYY-MM.jsonl` 归档 |
| `phone/` | 手机聊天、动态、日记、媒体与未读状态 |
| `world/` | 运行时地图 `map.json` |
| `media/` | 外观图、音频、参考音色等媒体产物 |
| `usage/` | 用量记录 |
| `logs/` | `YYYY-MM-DD.jsonl` 运行日志 |
| `backup/` | 按日期/时间分的滚动备份 |
| `dream/` | dream 审计与改写备份 |
| `tmp/` | 临时文件，可随时清空 |

## 配置

复制：

```powershell
copy state\Philosophical.example.json state\Philosophical.json
```

至少填：

```json
"payload": {"api": "sk-你的key"}
```

### 常用字段

| 字段 | 作用 |
|---|---|
| `payload.api` | DeepSeek API key |
| `payload.model` | 模型名 |
| `payload.temperature` | 采样温度；工具场景建议 ≤1.0 |
| `payload.user_id` | 用户名/手机里显示的用户名 |
| `harness.name` | 当前角色名（手机、界面显示用） |
| `harness.usage.maxTurn` | 用量统计窗口上限 |
| `harness.session.compressionThreshold` | 上下文达到多少 token 时提示压缩 |
| `harness.session.retainedTrun` | 压缩后保留最近多少轮 |
| `harness.session.stripReasoning` | 组装上下文时剔除历史 reasoning，不改 session 文件 |
| `harness.loop.maxRounds` | 一轮里最多几次“模型 → 工具 → 再模型” |
| `harness.loop.maxToolOutput` | 单次工具结果回灌的最大字数 |
| `harness.subprocess.timeout` | 工具子进程超时（秒） |
| `harness.retrieval.*` | 记忆/世界书/技能检索参数 |
| `harness.plan.*` | 计划渲染窗口和条数 |
| `harness.network.verifySSL` | 是否校验 TLS；走本地代理 MITM 时才设 `false` |
| `harness.debug.dump` / `dumpTail` | 调试转储开关 |
| `harness.dream.*` | 升格阈值、人格最小事件数、最大 token |
| `tools.tavily.api` | Tavily 联网工具 key |

`harness.retrieval` 的默认结构：

```json
{
  "queryTurns": {"withMessage": 2, "loopRound": 3, "maxChars": 400},
  "memory": {
    "topK": 6,
    "blockTopK": 3,
    "sliceFloor": 0.55,
    "sliceMargin": 0.08,
    "blockFloor": 0.4
  },
  "lorebook": {"maxItems": 12, "budgetChars": 2000}
}
```

`harness.dream` 的默认结构：

```json
{
  "block": {"minRecalledChars": 5000, "minRecalls": 5},
  "memory": {"minRecalls": 30, "minSources": 2},
  "persona": {"minEvents": 3},
  "maxTokens": 3000
}
```

代码对所有字段都有默认值；缺失或类型不对时回退，不会因为少一个字段直接崩。

## 环境变量

| 变量 | 作用 |
|---|---|
| `PZ_DATA` | 把整个运行数据目录换到别处 |
| `PZ_CARD_ROOT` | 换一张卡片的 `ConsciousnessRealm` 目录 |
| `PZ_SERVER` | 界面连接的后端地址 |
| `PZ_HIDE_USER` | 调试时隐藏 `USER.md` |
| `PZ_DREAM=1` | 打开 dream 专属地图操作 |

## phone

手机数据独立于主 session：

```text
phone/
├─ chat.jsonl
├─ moments.jsonl
├─ diary.jsonl
├─ state.json        # 她的已读、用户的已读、点赞
└─ media/            # 用户上传或角色发送的图片
```

消息里的 `sender` 是 `user` / `self`。角色名和用户名分别来自：

```text
realTime/character.json → name
Philosophical.json      → payload.user_id
```

不要在代码里写死名字；换卡只改这两个外部来源。

## 闹钟

`<state>` 会写当前闹钟的响铃时间和剩余时间，例如：

```text
闹钟：10:32:47（还剩 00:28:19） — 半小时到了，下去厨房看看。
```

模型不需要自己估算闹钟有没有跑完；睡着时也会显示这条。

## 运行时地图

```text
world/map.json
```

第一次读时如果卡内有 `ConsciousnessRealm/world/map.seed.json` 就从种子初始化；当前卡没有种子时使用已有地图。地图节点、边、风险和路线由 `map` 工具维护。

## 备份与日志

```text
backup/YYYY-MM-DD/HHMMSS-毫秒/
logs/YYYY-MM-DD.jsonl
```

备份只覆盖：

```text
session/session.jsonl
realTime/character.json
realTime/timers.json
realTime/events.json
realTime/maintenance.json
```

保留 14 天；不备份向量库和媒体。

日志里先看：

```text
stream.fail / model.fail   请求层失败
tool.begin / tool.done     工具执行
maintain.done / failed     维护
crash                      未捕获异常
```

## 清空

`tmp/` 可以随时清。  
不要把 `Philosophical.json`、`session/`、`phone/` 和 `realTime/` 当成可随手重置的目录；需要重置时先备份。
