# events.json

活动时间标记。静默唤醒和随机事件都靠它；没有 `lastMessageAt` 时，服务首次启动会补一个当前时间。

```json
{
    "lastMessageAt": 1767225600.0,
    "lastUserAt": 1767225590.0,
    "lastEventAt": 1767200000.0
}
```

| 字段 | 说明 |
|---|---|
| `lastMessageAt` | 最后一次模型回合的时间；静默超过 `harness.idle.afterMinutes`（默认 30 分钟）就自主醒一次 |
| `lastUserAt` | 用户最后一次说话的时间 |
| `lastEventAt` | 上一次随机事件的时间，用于 30 分钟冷却 |

时间都是 Unix 秒。睡着的角色只由生理和闹钟叫醒，静默到点不会叫。
