# state.json

手机应用的已读和点赞状态。三个应用共用这一个文件。

```json
{
    "read": {
        "chat": [
            1,
            2,
            3
        ]
    },
    "likes": {
        "moments": [
            1
        ],
        "diary": []
    },
    "ui_read": {
        "chat": 3
    }
}
```

| 字段 | 说明 |
|---|---|
| `read.<app>` | 她已经读到第几行（行号数组），用来算**她**的未读 |
| `likes.<app>` | 用户点过赞的行号，只对 `moments` / `diary` 有意义 |
| `ui_read.<app>` | 用户界面读到第几行，用来算**用户**的角标 |

她的未读 = `sender` 是 `user` 且行号不在 `read.chat` 里的消息数；
用户的未读 = `sender` 是 `self` 且行号大于 `ui_read.chat` 的消息数。两边分开算。
