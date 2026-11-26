# Philosophical.json

本机运行配置。真实文件是 `state/Philosophical.json`，含 API key，**不入库**；
仓库里只保留这份结构示例，字段缺失时代码按默认值回退，不会因为少一个键直接崩。

新建时把下面的 JSON 复制到 `state/Philosophical.json`，把 `payload.api` 换掉。

```json
{
    "payload": {
        "model": "deepseek-flash",
        "api": "sk-你的key",
        "user_id": "user",
        "thinking": {
            "type": "enabled"
        },
        "reasoning_effort": "high",
        "max_tokens": 16384,
        "stream": true,
        "temperature": 1.0,
        "top_p": 1,
        "tool_choice": "auto"
    },
    "harness": {
        "name": "陆青禾",
        "usage": {
            "maxTurn": 100,
            "avgWindow": 100
        },
        "session": {
            "compressionThreshold": 340000,
            "retainedTrun": 8,
            "stripReasoning": true
        },
        "subprocess": {
            "timeout": 300
        },
        "loop": {
            "maxRounds": 8,
            "maxToolOutput": 8000
        },
        "retrieval": {
            "queryTurns": {
                "withMessage": 2,
                "loopRound": 3,
                "maxChars": 500,
                "windowMinutes": 60
            },
            "memory": {
                "topK": 6,
                "blockTopK": 3,
                "sliceFloor": 0.55,
                "sliceMargin": 0.08,
                "blockFloor": 0.4
            },
            "lorebook": {
                "maxItems": 12,
                "budgetChars": 2000
            }
        },
        "plan": {
            "stepWindow": 1,
            "activeLimit": 3,
            "pausedLimit": 2
        },
        "network": {
            "verifySSL": false
        },
        "debug": {
            "dump": false,
            "dumpTail": true
        },
        "dream": {
            "block": {
                "minRecalledChars": 5000,
                "minRecalls": 5
            },
            "memory": {
                "minRecalls": 30,
                "minSources": 2
            },
            "persona": {
                "minEvents": 3
            },
            "maxTokens": 3000
        }
    },
    "tools": {
        "tavily": {
            "api": ""
        }
    }
}
```

## 常用字段

| 字段 | 说明 |
|---|---|
| `payload.api` | DeepSeek API key，必填 |
| `payload.model` | 模型名 |
| `payload.user_id` | 用户名；手机和会话里显示 |
| `payload.temperature` | 采样温度；工具场景建议 ≤ 1.0 |
| `harness.name` | 角色显示名；手机、界面、随机事件读它 |
| `harness.loop.maxRounds` | 一轮里最多几次“模型 → 工具 → 再模型” |
| `harness.loop.maxToolOutput` | 单次工具结果回灌的最大字数 |
| `harness.session.compressionThreshold` | 上下文 token 达到多少时提示压缩 |
| `harness.session.retainedTrun` | 压缩后保留最近多少轮 |
| `harness.session.stripReasoning` | 发给模型时是否剔除历史 reasoning（不改 session 文件） |
| `harness.network.verifySSL` | 走本地代理 MITM 时才设 `false` |
| `tools.tavily.api` | 联网工具 key，可空 |

其余字段（检索、计划窗口、dream 阈值、调试转储）的完整含义见 `state/README.md`。
