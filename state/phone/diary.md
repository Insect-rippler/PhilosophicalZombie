# diary.jsonl

手机“日记”。只有角色会写，一行一条，只追加；字段和 `moments.jsonl` 相同。

```jsonl
{"sender":"self","from":"陆青禾","kind":"text","text":"今天他回来得比平时晚。","media":"","caption":"","ts":"2026-01-01 23:10:00"}
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

用户的点赞记在 `state.md` 的 `likes.diary` 里。
