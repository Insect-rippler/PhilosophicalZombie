# timers.json

待结算的动作、事件和睡眠计时器。数组；没有计时器时是 `[]`。

```json
[
    {
        "id": "t-123456789-000",
        "at": 1767225600.0,
        "minutes": 0.2,
        "kind": "action",
        "semantic": "她起身去倒水",
        "text": "她端着水回来，放到桌上。",
        "slot": "short",
        "slotId": "short",
        "chance": 1000,
        "silent": false,
        "source": "body",
        "createdAt": 1767225588.0,
        "location": "客厅",
        "reward": {
            "emotion": 2.0,
            "fatigue": -0.01
        }
    }
]
```

| 字段 | 说明 |
|---|---|
| `id` | 计时器 id；结算流水里也用它 |
| `at` / `createdAt` | 到期时间 / 创建时间（Unix 秒） |
| `minutes` | 时长（分钟），`at = createdAt + minutes * 60` |
| `kind` | `action` / `event` / `sleep` 等，由创建方决定 |
| `semantic` | 动作原文，喂给判定和检索 |
| `text` | 到点时写进“刚刚发生”的结算文本 |
| `slot` / `slotId` | body 槽位；`short` 可移动，`long` / `event` 只校验到点位置 |
| `silent` | `true` 时不写 pending、不触发下一拍 |
| `location` | 到点时她应处的地点 |
| `reward` | 到点结算的数值增减，交给 `timers._apply_reward()` |
| `pending` / `note` | 可选，要求下一拍处理或附带说明 |

tick 只结算、不调用模型：到点后写回 `character.json`、把 `text` 推进 `pending.json`，再把已结算的条目从本文件删掉。

另有一个只追加的结算流水 `state/realTime/timer_log.jsonl`，每行是 `{"id": "...", "kind": "action"}`，用于排查和去重。
