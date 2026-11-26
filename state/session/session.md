# session.jsonl

主会话历史，一行一条消息，只追加不重写。压缩后的旧轮次归档到 `state/session/history/YYYY-MM.jsonl`，格式相同。

```jsonl
{"role":"user","name":"用户","content":"我回来了。","ts":"2026-01-01 12:00:00","local":"客厅"}
{"role":"assistant","content":"","reasoning_content":"她听见门响……","tool_calls":[{"id":"call_000","type":"function","function":{"name":"active","arguments":"{\"location\":\"客厅\",\"seconds\":10,\"short\":\"她抬头看过去\"}"}}],"ts":"2026-01-01 12:00:02","local":"客厅"}
{"role":"tool","tool_call_id":"call_000","content":"{\"ok\": true}","ts":"2026-01-01 12:00:03","local":"客厅","actions":["short:她抬头看过去"]}
```

| 字段 | 说明 |
|---|---|
| `role` | `user` / `assistant` / `tool` |
| `name` | 只有 user 行带；取自 `payload.user_id` |
| `content` | 正文；tool 行是工具回执 JSON |
| `reasoning_content` | assistant 的思考内容；按 `harness.session.stripReasoning` 决定发给模型时是否剔除，但文件里保留 |
| `tool_calls` | assistant 的工具调用；后面必须有对应的 tool 行，否则请求会 400 |
| `tool_call_id` | tool 行对应哪次调用 |
| `ts` | 写入时间 |
| `local` | 写入时她所在的地点 |
| `actions` | 写入时还没结算的计时器摘要 |
| `sleep` | 睡着时写的标记 |

其它文件：`rev.txt` 是 session 的版本号（前端轮询用）；`history/` 里是压缩归档。
服务启动时会调 `core.repair_session()` 修掉断掉的 `tool_calls`，解析失败的行会被跳过并记日志。
