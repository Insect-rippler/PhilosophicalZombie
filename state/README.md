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
| `harness.prompt_type_order` | 上下文段落顺序，`session` 之前的都是 system 级 |
