# maintenance.json

维护标记。睡着超过 3 小时后，维护依次跑提要、压缩、升格、印象整理；这里记录“该不该跑”和“上次跑完的结果”。

已完成的形态：

```json
{
    "due": false,
    "doneFor": 1767225600.0,
    "finishedAt": 1767229200.0,
    "summary_chars": 104
}
```

待处理的形态：

```json
{
    "due": true,
    "sleepStartedAt": 1767225600.0
}
```

| 字段 | 说明 |
|---|---|
| `due` | `true` 表示维护待处理 |
| `sleepStartedAt` | 待处理时：这次睡眠的开始时间 |
| `doneFor` | 跑完后：对应哪次睡眠的开始时间；成功才写 |
| `finishedAt` | 跑完的时间（Unix 秒） |
| `summary_chars` | 这次生成的提要字数 |

任何一步失败都不写 `doneFor`/`finishedAt`，标记保持待处理，醒来会带负面状态。
