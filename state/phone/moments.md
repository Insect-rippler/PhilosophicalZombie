# moments.jsonl

手机“动态”。只有角色会发，一行一条，只追加。

```jsonl
{"sender":"self","from":"陆青禾","kind":"image","text":"","media":"state/phone/media/2026-01-01-120500.png","caption":"今天的晚饭","ts":"2026-01-01 12:05:00"}
```

| 字段 | 说明 |
|---|---|
| `sender` | 固定 `self` |
| `from` | 角色显示名 |
| `kind` | `text` / `image` / `voice` |
| `text` | 正文 |
| `media` | 媒体绝对路径，必须位于 `state/phone/media/` 下 |
| `caption` | 附言 |
| `ts` | 发布时间 |

用户不能直接改动态，只能点赞；点赞记在 `state.md` 的 `likes.moments` 里。
