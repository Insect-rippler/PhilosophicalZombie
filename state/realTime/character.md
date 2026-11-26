# character.json

角色的实时状态。服务启动、tick 结算、动作落账时写回；文件不存在时按默认值重建。

```json
{
    "name": "陆青禾",
    "local": "客厅",
    "state": {
        "active": "wake",
        "since": "2026-01-01 09:00",
        "lastAdjusted": "2026-01-01 12:00:00:1a2b",
        "sleepStarts": [
            1767225600.0
        ],
        "sleepStartSleepiness": 70.0
    },
    "metabolism": {
        "hp": 100.0,
        "health": 100.0,
        "bodyTemperature": 36.5,
        "satiety": 80.0,
        "hydration": 70.0,
        "fatigue": 10.0,
        "sleepiness": 10.0,
        "sexualDesire": 10.0
    },
    "emotion": {
        "emotion": 120.0
    },
    "relations": {},
    "items": [
        "一只旧铁皮罐"
    ],
    "lastUpdate": 1767225600.0
}
```

| 字段 | 说明 |
|---|---|
| `name` | 显示名，手机和界面读它 |
| `local` | 当前地点，对应地图里的节点名 |
| `state.active` | `wake` / `sleep` |
| `state.since` | 进入当前状态的时间 |
| `state.lastAdjusted` | 最近一次“观察用户这句话对身体影响”的标记，格式 `时间戳:哈希`，用来避免重复结算 |
| `state.sleepStarts` | 最近 12 次入睡的 Unix 时间；判断“睡够多久”用 |
| `state.sleepStartSleepiness` | 入睡时的困倦值 |
| `metabolism.*` | 生理值；阶段、范围和耦合由 `ConsciousnessRealm/body/metabolism.json` 定义 |
| `emotion.emotion` | 情绪值，同上 |
| `relations` | 对其他人物的关系增量 |
| `items` | 随身物品 |
| `lastUpdate` | 上一次生理结算的 Unix 时间；`timers.advance()` 按它补算 |

写这个文件的地方：`engine/timers.py`（结算、生理）、`engine/body.py`（动作奖励、判定）、`engine/phone.py` 只读。
