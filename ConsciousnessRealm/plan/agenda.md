# agenda.json

计划与随机事件。`plan` 工具写这个文件，事件也写进来；`<plan>` 段读它。

```json
{
    "goal": [
        {
            "id": "g-123456789-000",
            "kind": "self",
            "objective": "把阳台收拾出来",
            "status": "active",
            "createdAt": "2026-01-01 12:00:00",
            "updatedAt": "2026-01-01 12:30:00",
            "note": "",
            "steps": [
                {
                    "id": 1,
                    "title": "把纸箱搬下去",
                    "status": "doing",
                    "fallback": "搬不动就先挪到门口",
                    "evidence": ""
                }
            ]
        },
        {
            "id": "g-123456789-001",
            "kind": "event",
            "objective": "……",
            "status": "active",
            "createdAt": "2026-01-01 13:00:00",
            "updatedAt": "2026-01-01 13:00:00",
            "note": "",
            "steps": [],
            "event": {
                "scene": "……",
                "opening": "……",
                "condition": "……（至少两种处理方向）",
                "limitSeconds": 600,
                "deadlineAt": "2026-01-01 13:10:00",
                "reward": {
                    "emotion": 3.0
                },
                "penalty": {
                    "emotion": -3.0
                },
                "state": "pending",
                "resolvedAt": ""
            }
        }
    ]
}
```

| 字段 | 说明 |
|---|---|
| `kind` | `self`（自定目标） / `event`（随机事件） |
| `status` | `active` / `paused` / `done` / `abandoned` / `failed` |
| `objective` | 目标一句话 |
| `steps` | 步骤数组；`status` 是 `doing` / `pending` / `done` |
| `event` | 只有事件目标有；`condition` 必须留下至少两种处理方向，否则生成会被拒绝重试 |

事件里的 `scene` / `opening` / `condition` / 奖励惩罚由 `engine/events.py` 生成，`deadlineAt` 到点未处理会按 `penalty` 结算。
文件不存在时按空计划处理。
