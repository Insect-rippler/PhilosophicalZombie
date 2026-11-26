# function.local.json

本机专用工具，不入库。和 `function/function.json` 结构相同，多一个 `path` 指向工具脚本；脚本路径相对仓库根。

```json
[
    {
        "type": "function",
        "path": "function/appearance/appearance.py",
        "function": {
            "name": "appearance",
            "description": "换装：按新穿着重画她的立绘，覆盖当前外观图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "outfit": {
                        "type": "string",
                        "description": "新穿着"
                    }
                },
                "required": [
                    "outfit"
                ]
            }
        }
    }
]
```

| 字段 | 说明 |
|---|---|
| `type` | 固定 `function` |
| `path` | 工具脚本路径；调用时以 stdin 收 JSON、stdout 回 JSON |
| `function.name` | 工具名，模型看到的就是它 |
| `function.description` | 给模型看的用途说明 |
| `function.parameters` | JSON Schema |

文件不存在时只用 `function/function.json` 里的公共工具，不会报错。
