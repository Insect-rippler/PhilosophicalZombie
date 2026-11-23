# 前后端协议（主会话）

前端是静态渲染器，后端是唯一的状态来源。

```
前端负责：布局、控件、动画、滚动、输入、倒计时显示
后端负责：消息、顺序、id、去重、空消息、工具折叠、计时器、文案、当前视图
```

前端不查 `/history`、不读 `/state` 拼字段、不比较多个计时器、不计算增量。

---

## 一、入口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/view` | 首屏、手动刷新、页面切换，返回完整视图 |
| GET | `/events?since=<rev>` | SSE 长连接，后端主动推送 |
| POST | `/sync` | 计时器到点，请求结算 |
| POST | `/turn` | 用户输入，返回 202，不返回正文流 |

`/state`、`/history`、`/usage`、`/config` 继续存在，作调试和旧页面兜底；主会话不再使用。

当前实现：tick 推完整 `view`；当前 turn 推 `stream` / `tool` / `done`；`patch` 仍预留。

---

## 二、完整视图

`GET /view`：

```json
{
  "rev": 123,
  "serverNow": 1794300000.0,
  "messages": [
    {"id": "m1", "kind": "user", "text": "今晚还修得好吗？", "ts": "2026-11-17 20:35:00"},
    {"id": "m2", "kind": "narration", "text": "雨声压在屋檐上。", "ts": "..."},
    {"id": "m3", "kind": "line", "text": "修得好。", "ts": "..."},
    {"id": "m4", "kind": "tip", "items": [
      {"name": "plan", "text": "整理了目标"}
    ], "ts": "..."}
  ],
  "timer": {
    "id": "short:172...",
    "kind": "short",
    "label": "和泥",
    "endsAt": 1794300120.0,
    "remainingMs": 95000,
    "graceMs": 200
  },
  "state": {
    "location": "边城工坊",
    "status": "醒着",
    "metabolismText": "饱食 55　水分 58　疲劳 30"
  },
  "usage": {
    "text": "输入 20.0k　输出 176　平均命中 87%"
  },
  "plan": []
}
```

`messages[].kind`：

```
user        用户
assistant   正文
scene       场景
action      动作
line        台词
narration   旁白
tip         工具提示组，items 由后端折叠好
```

前端只按 `kind` 选控件，不改写文本。

`timer` 可以为 `null`。

---

## 三、计时器

### 类型

```
short / long / event    动作计时器
alarm                   闹钟
silence                 静默计时器
```

### 选择

后端每次重算，只推**最近到期的一个**：

```
最早到期优先
同一时刻：alarm > action(short/long/event) > silence
睡着时排除 silence
没有有效计时器 → timer: null
```

### 前端倒计时

后端推送的 `remainingMs` 必须是推送那一刻重算的值。

前端收到 timer 时：

```
deadline = monotonic_now + remainingMs + graceMs
```

`graceMs` 固定 200。

规则：

- **每次收到 timer 都重新对齐**，不是在旧倒计时上累加。
- 没有 `timer` 字段的普通 patch 不碰倒计时。
- 到零后 `POST /sync`。
- 前端用单调时钟，不用系统墙钟。

200ms 让后端有窗口先推送下一个状态；网络延迟超过 200ms 时，靠 `/sync` 兜底。

### `/sync`

请求：

```json
{"timerId": "short:...", "sinceRev": 123}
```

后端：

```
检查 timerId 是否到期
已到期 → timers.settle()，重算视图，返回 patch
未到期 → 返回当前 timer / 空 patch
```

响应：

```json
{"rev": 124, "view": { ...完整视图... }}
```

`/sync` 必须幂等，重复请求不能重复结算。

---

## 四、SSE 事件

连接：

```
GET /events?since=<rev>
Accept: text/event-stream
```

当前版本忽略 `since`，连接后先发一份完整 `view`；重连也整页替换。

事件：

### view

完整视图，首连或 `since` 太旧时发送。

```json
event: view
data: { ...完整视图... }
```

### patch

```json
event: patch
data: {
  "rev": 124,
  "ops": [
    {"op": "append", "path": "messages", "value": {"id": "m5", "kind": "line", "text": "..."}},
    {"op": "replace", "path": "timer", "value": {...}},
    {"op": "replace", "path": "state", "value": {...}},
    {"op": "replace", "path": "usage", "value": {...}}
  ]
}
```

`op`：

```
append   往 messages 末尾加一条
replace  按 id 替换 messages 中的一条
remove   按 id 移除 messages 中的一条
replace  替换 timer / state / usage / plan
```

### tool

工具事件只在**后端验证受理之后**发送；被拒的动作不进入 `tool`，只保留合法台词。

```json
event: tool
data: {"kind": "scene|action|line|tip", "name": "active", "text": "..."}
```

前端只负责把它临时画上去；本轮结束后的 `view` 会整页替换成后端 canonical 版本。

### stream

当前 turn 的正文增量：

```json
event: stream
data: {"id": "m6", "kind": "assistant", "delta": "她把"}
```

前端第一次收到某 id 的 `stream` 时创建气泡，后续追加。

### done

```json
event: done
data: {"id": "m6", "text": "最终正文"}
```

前端用最终文本兜底替换；不产生重复消息。

### error

```json
event: error
data: {"message": "..."}
```

### heartbeat

```
: ping
```

只保活，不产生视图更新。

`patch` 的事件格式保留在协议里，当前版本尚未启用；`stream`、`tool`、`done` 已用于当前 turn。

---

## 五、推送时机

后端主动推：

```
tick 算完 → 视图有变化才推 patch
timer 每次 tick 重新算 → 推 timer patch（仅当有 timer）
新 session 行 → patch
turn 的正文 → stream / done
usage 变化 → patch
```

不推：

```
tick 没有变化
同一 timer 的纯展示秒数（前端本地倒计时）
```

前端主动要：

```
首屏 / 手动刷新 / 页面切换 → GET /view
计时器到零 → POST /sync
断线重连 → GET /events?since=rev
```

---

## 六、`/turn`

请求：

```json
{"text": "今晚还修得好吗？"}
```

响应：

```json
202 {"ok": true, "rev": 123}
```

正文、动作、台词、工具提示都走 `/events`，`/turn` 不再返回 SSE。

如果 SSE 断开，前端用 `GET /view` 兜底，不能靠 `/turn` 响应拿正文。

---

## 七、去重与顺序

```
消息 id 由后端生成，稳定不变
rev 单调递增
stream 的 id 在 done 后不再复用
同一消息不会同时出现在 patch 和 stream 里
active-only 没有正文 → 后端不创建 assistant 消息
被拒动作 → 不推 action；line 正常推
工具提示由后端折叠成一条 tip，前端不再自己分组
```

---

## 八、重连

```
前端保存最后 rev
断线后 GET /events?since=lastRev
后端能补 patch → 补发
补不了 / since 太旧 → 直接发 view
```

`/view` 到达后前端整页替换，不做增量合并。

---

## 九、边界

```
前端仍负责：窗口、布局、滚动、倒计时显示、输入
后端不推 Qt 控件、样式、像素
后端只推语义 kind 和文本
```

`/view`、`/events`、`/sync` 先只服务主会话；设置、识域、技能页继续用现有接口，后续再迁。
