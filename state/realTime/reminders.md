# reminders.json

生理提醒的“上膛”状态。规则本身在 `ConsciousnessRealm/world/<世界>/reminders.json`，这里只记每条规则是否已经触发过。

```json
{
    "hunger": true,
    "thirst": false
}
```

| 字段 | 说明 |
|---|---|
| `hunger` / `thirst` / … | 规则 id（取自世界里的 `reminders.json`）→ 是否已上膛 |

规则用“越界触发”：条件从假变真时提醒一次并置 `false`；回落到正常后重新置 `true`，下次越界再提醒。文件不存在时所有规则都当作上膛。
