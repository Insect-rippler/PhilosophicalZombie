# 前后端协议与每 tick 的内容

前端（`ui/app.py`）与后端（`engine/server.py`）之间只有两种通道：

```
HTTP 请求       拉数据、发一句话、改配置
SSE 流          一轮对话的增量输出
```

**每个 tick 不推送任何东西。** 前端靠轮询，服务端不主动通知。

---

## 一、HTTP 接口

### 读

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/state` | 她的当前状态，见下 |
| GET | `/history?limit=N` | `{"messages": [...], "total": 总条数}` |
| GET | `/config` | 完整配置树（前端只显示白名单字段） |
| GET | `/usage` | 缓存命中统计的窗口数据 |
| GET | `/skill` | `{"items": [...]}` |
| GET | `/realm` | `{"files": [...]}`，识域根目录下的 md |
| GET | `/realm/<名字>` | `{"text": "..."}` |

`/state` 的形状：

```json
{
  "lastMessageAt": 1794300000.0,
  "local": "奥格尼斯科/城外废矿坑底",
  "active": "wake",
  "metabolism": {"sleepiness": 42, "fatigue": 30, "satiety": 55,
                 "hydration": 58, "hp": 100, "health": 99, "bodyTemperature": 36.5},
  "timers": [{"slotId": "long", "text": "…", "at": 1794300120}],
  "pending": []
}
```

### 写

| 方法 | 路径 | 请求体 | 返回 |
|---|---|---|---|
| POST | `/turn` | `{"text": "…"}` | **SSE 流**（下节） |
| POST | `/config` | `{"config": {...}}` 或 `{"payload": {...}}` | 合并结果 |
| POST | `/skill` | `{"items": [...]}` | `{"count": N}` |
| PUT | `/realm/<名字>` | `{"text": "..."}` | `{"chars": N}` |

---

## 二、SSE 流

`POST /turn` 返回 `text/event-stream`，事件五种：

| event | data | 含义 |
|---|---|---|
| `tool` | `{"kind": "scene"\|"action"\|"line"\|"narration", "name": "active", "text": "…"}` | 场景 / 动作 / 台词 / 旁白 |
| `delta` | `"正文片段"` | 她说的话，逐段追加 |
| `usage` | `{"round":1, "prompt":19058, "cache":42.3, "avg":86.5, …}` | 这一轮的用量 |
| `done` | `"最终正文"` | 收尾；前端用它兜底替换 |
| `error` | `"错误描述"` | 出错 |

约定：

- 一条消息里**多个 kind 按顺序到达**：scene → action → line → delta。
- 被代码拒绝的动作**不发** scene/action，但**发 line**（她确实开口了）。
- `done` 之后流关闭；前端把游标推到 `/history` 的 `total`，避免重复渲染。

---

## 三、每 tick 做什么

`TICK_SECONDS = 60`，一分钟一次，`idle_loop()` 里顺序执行：

```
1  timers.advance()             按真实流逝推进生理（睡眠时走睡眠速率）
2  timers.settle()              到期的计时器落账：位置、状态、数值
3  reminder_hooks.check()       生理阈值提醒
4  event_hooks.check()          地点事件 + 全局事件掷骰（按池按槽位）
5  should_wake()                该不该醒
     ├─ pending 非空        → 醒
     └─ 静默超过 N 分钟且醒着 → 醒
6  idle_tick()                  走一轮完整对话（组装上下文 → 模型 → 工具）
```

`should_wake()` 的两个条件互相独立：**计时器到期必醒**；静默唤醒只在醒着时计。

### tick 不产生推送

```
tick 只改 state/session
前端每 6 秒 GET /history?limit=N 和 /state，自己比对
```

原因：tick 里可能什么都没有发生，推送大部分是空包；轮询更简单，也避免了长连接断线重连。

### 一轮对话（`/turn` 或 `idle_tick`）内部的顺序

```
① 组装上下文       内存里完成：search_text → 三路检索 → system→图→session→块→state/plan→user
② 模型流式回复     delta / tool 事件实时推给前端
③ 工具执行         active 判定、计时器、事件
④ 写 session       助手消息 + 工具回执
⑤ 再组装（工具轮） 回到 ②，最多 LOOP_MAX 轮
⑥ 收尾            done
```

---

## 四、现在不一致 / 待改的地方

1. **`/history` 没有游标**：只认 `limit`，前端靠 `total` 自己算增量。
   条数一多就每次拉全量，浪费带宽（目前 limit=60，尚可）。
2. **tick 的结果前端不知道**：计时器到期、事件触发都要等前端下一次轮询；
   最长 6 秒延迟。可接受，但“事件触发”其实值得推送。
3. **`/state` 与 `/history` 两次请求**：状态和最后几条消息总是同一次轮询里取，
   可以合并成一个 `/snapshot`。
4. **`/turn` 的错误只走 SSE**：如果连接在流开始前就断了，前端拿不到原因。
5. **没有版本号**：前后端各改各的，字段变了只能靠报错发现。

这些都不急着改，先把协议写到文档里，改的时候有依据。

---

## 五、记一件以后要做的事

**把 body 的判定交还给主代理**，能省掉接近一半的模型请求：

```
现在   主代理（流）→ 工具 active → body 代理（温度 0.2，判可行性/时长）
以后   主代理自己判，工具只负责落账
```

代价是主代理要同时扮演“她”和“她的身体”，两种视角容易串，
需要在提示词里分清“她想做”和“她做得到”。先记着，不在这一轮做。
