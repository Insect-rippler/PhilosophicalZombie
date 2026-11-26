# blocks.json

升格后的记忆块。由切片升格而来，带标签和来源，常驻检索的 `blockTopK` 从这里面取。

```json
{
    "model": "bge-small-zh-v1.5",
    "dim": 512,
    "items": [
        {
            "id": "b-000001",
            "text": "……",
            "tag": "relation",
            "sources": [
                "s-000001",
                "s-000002"
            ],
            "recalls": 0,
            "importance": 1,
            "why": "",
            "recallsAt": [],
            "lastRecall": 0.0,
            "lastInjected": 0.0,
            "recalled_chars": 0,
            "opportunities": 0
        }
    ]
}
```

| 字段 | 说明 |
|---|---|
| `sources` | 来源切片 id；升格到 `MEMORY.md` 要求至少 2 个独立来源 |
| `tag` | 记忆的分类标签 |
| `recalls` / `recalled_chars` | 召回计数，升格到长期记忆的判据 |
| `why` | 生成时留的理由 |

主代理只能通过 `remember` 工具写记忆块；`MEMORY.md` 和 `PERSONA.md` 只由 dream 流程写。
阈值在 `harness.dream`。向量存在同目录的 `memory_vectors.npz`，见 `memory_vectors.md`。
