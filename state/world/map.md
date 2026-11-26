# map.json

运行时地图。第一次读取时，如果卡内有 `ConsciousnessRealm/world/map.seed.json` 就从种子初始化，否则用已有地图或空图。

```json
{
    "nodes": {
        "客厅": {
            "risk": 0,
            "createdAt": 1767225600.0,
            "updatedAt": 1767225600.0
        },
        "楼下便利店": {
            "risk": 1,
            "createdAt": 1767225600.0,
            "updatedAt": 1767225600.0
        }
    },
    "edges": [
        {
            "a": "客厅",
            "b": "楼下便利店",
            "seconds": 90
        }
    ],
    "hash": "0000000000000000",
    "refactoredHash": "0000000000000000",
    "updatedAt": "2026-01-01 12:00:00"
}
```

| 字段 | 说明 |
|---|---|
| `nodes` | 地点名 → 属性；`risk` 0~3，数值越大越危险 |
| `edges` | 无向边；`a` / `b` 是两端节点，`seconds` 是单段用时（1~120 秒） |
| `hash` / `refactoredHash` | 地图迁移去重用的标记，一般不用手改 |
| `updatedAt` | 最近一次改动时间 |

日常通过 `map` 工具维护：`query` 查路线，`create` 建点，`link` 建或改连线。`unlink` / `set_risk` / `delete` / `merge` 只在 dream 环境开放。

`short` 跨地点时先过地图；地图没有目标先 `query` 再 `create`；不相邻就逐段走。`long` / `event` 的 `location` 只在到点校验。
