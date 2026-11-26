# environment.json

当前环境天气快照，由 `engine/environment.py` 更新，写进 `<state>` 和随机事件的提示。

```json
{
    "season": "秋",
    "condition": "多云",
    "temperatureC": 7.8,
    "updatedAt": 1767225600.0
}
```

| 字段 | 说明 |
|---|---|
| `season` | 季节 |
| `condition` | 天气 |
| `temperatureC` | 气温（摄氏） |
| `updatedAt` | 上次更新时间（Unix 秒） |

文件不存在时按空环境处理。
