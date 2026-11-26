# slices.json

会话切片库。`sessionSummary` 压缩时把旧轮次切成片，和 `blocks.json` 共用一份向量表。

```json
{
    "model": "bge-small-zh-v1.5",
    "dim": 512,
    "items": [
        {
            "id": "s-000001",
            "text": "……",
            "recalls": 0,
            "importance": 1,
            "why": "",
            "recallsAt": [],
            "lastRecall": 0.0,
            "lastInjected": 0.0,
            "recalled_chars": 0,
            "opportunities": 0
        }
    ],
    "searches": 0
}
```

| 字段 | 说明 |
|---|---|
| `model` / `dim` | 向量模型和维度；换模型要重建 |
| `items[].text` | 切片原文 |
| `items[].recalls` / `opportunities` | 被召回次数 / 有机会被召回的次数 |
| `items[].recalled_chars` | 被召回时贡献的字数，升格判据用它 |
| `items[].lastRecall` / `lastInjected` | 最近被召回 / 注入的时间（Unix 秒） |
| `items[].why` / `importance` | 生成时留的说明和权重 |
| `searches` | 一共检索过多少次 |

切片由 `sessionSummary` 和 dream 流程写，主代理不直接改。升格成记忆块的阈值在 `harness.dream.block`。
向量存在同目录的 `memory_vectors.npz`，见 `memory_vectors.md`。
