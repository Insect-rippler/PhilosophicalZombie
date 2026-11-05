# state — 运行时状态

本目录不入库，只保留配置模板。

## 需要的文件

| 路径 | 内容 | 说明 |
|---|---|---|
| `Philosophical.json` | 运行配置 | 含 API key，**不入库**；格式见 `Philosophical.example.json` |
| `session/` | 会话历史 `session.jsonl` | 运行产物 |
| `media/` | 音频、图片、参考音色 | 运行产物，含版权素材 |
| `usage/` | 用量记录 | 运行产物 |
| `tmp/` | 临时文件 | 可随时清空 |

## 配置

复制 `Philosophical.example.json` 为 `Philosophical.json`，填入自己的 key。

必填/常用字段：

| 字段 | 作用 |
|---|---|
| `payload.api` | DeepSeek API key |
| `payload.model` | 模型名（如 `deepseek-flash`） |
| `payload.temperature` | 采样温度。工具调用场景建议 ≤1.0，1.5 会让工具回合发散 |
| `harness.loop.maxRounds` | 一轮对话里最多几次「模型→工具→再模型」 |
| `harness.loop.maxToolOutput` | 单个工具结果回灌的最大字数 |
| `harness.session.compressionThreshold` | 上下文达到多少 token 时提示压缩 |
| `harness.retrieval.*` | 检索参数：查询轮数、召回条数、相关性阈值、世界书预算 |
| `harness.plan.*` | 计划渲染：步骤窗口、各类目标条数 |
| `harness.subprocess.timeout` | 工具执行超时（秒） |
| `harness.network.verifySSL` | 是否校验 TLS 证书；走本地代理 MITM 时设 `false` |
| — | 段落顺序已从配置移到 `ConsciousnessRealm/AGENT.md` |

检索参数的语义和取值依据见项目 `README.md` 的「开销」一节。代码里对这些字段都带默认值：缺失或类型不对时会回退并在 stderr 提示，不会崩。
