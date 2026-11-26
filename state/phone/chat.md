# chat.jsonl

手机“消息”应用，一行一条，只追加。角色名和用户名分别来自 `realTime/character.json` 的 `name` 和 `Philosophical.json` 的 `payload.user_id`。

```jsonl
{"sender":"user","from":"用户","kind":"text","text":"在吗","media":"","caption":"","ts":"2026-01-01 12:00:00"}
{"sender":"self","from":"陆青禾","kind":"image","text":"","media":"state/phone/media/2026-01-01-120500.png","caption":"今天的晚饭","ts":"2026-01-01 12:05:00"}
```

| 字段 | 说明 |
|---|---|
| `sender` | `user` / `self`；未读、角标都按它算 |
| `from` | 发送者显示名；旧数据没有 `sender` 时用它兜底 |
| `kind` | `text` / `image` / `voice` |
| `text` | 文字内容 |
| `media` | 图片或语音的绝对路径，必须位于 `state/phone/media/` 下 |
| `caption` | 附言 |
| `ts` | 发送时间 |

未读位置和点赞不在这个文件里，见 `state.md`。手机消息默认不叫醒角色，只有 `phone` 工具带 `wake=true` 才继续下一拍。
