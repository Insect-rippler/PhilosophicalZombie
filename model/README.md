# model — 本地模型与相关代码

## 这里应该放什么

```
model/
├─ memory_store.py            记忆 ↔ 向量库（会话切片 / 记忆块，共用一份 npz）
├─ f5_server.py               F5-TTS 本地服务，POST /tts 返回 wav
├─ bge-small-zh-v1.5/         向量模型（约 92 MB）
├─ f5/                        语音克隆模型（约 1.28 GB）
└─ vocos/                     声码器（约 52 MB）
```

**权重不入库**，按下面的方式自己放。

## 取权重

| 模型 | 来源 |
|---|---|
| bge-small-zh-v1.5 | HuggingFace `BAAI/bge-small-zh-v1.5`（含 tokenizer 与 model.safetensors） |
| F5TTS_v1_Base | ModelScope `AI-ModelScope/F5-TTS` → `F5TTS_v1_Base/model_1250000.safetensors` + `vocab.txt` |
| vocos-mel-24khz | HuggingFace `charactr/vocos-mel-24khz` → `config.yaml` + `pytorch_model.bin` |

F5 服务需要的目录结构：

```
model/f5/F5TTS_v1_Base/model_1250000.safetensors
model/f5/F5TTS_v1_Base/vocab.txt
model/vocos/config.yaml
model/vocos/pytorch_model.bin
```

## 运行环境

F5 依赖 PyTorch 与 f5-tts，且需要 Python 3.11（3.12+ 部分依赖没有轮子）：

```bash
conda create -n f5 python=3.11 -y
conda run -n f5 pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
conda run -n f5 pip install --index-url https://mirrors.huaweicloud.com/repository/pypi/simple f5-tts
```

## 跑起来

```bash
conda run -n f5 python model/f5_server.py     # 监听 127.0.0.1:9880
```

服务接口：

```text
GET  /health   → {"ok": true, "model": "F5TTS_v1_Base"}
POST /tts      → audio/wav 字节流
```

常用环境变量：

| 变量 | 作用 |
|---|---|
| `F5_CKPT` | 权重 `.safetensors` |
| `F5_VOCAB` | `vocab.txt` |
| `F5_VOCODER` | 声码器目录 |
| `F5_REF` | 默认参考音频 |
| `F5_REF_TEXT` | 默认参考文本 |
| `F5_PORT` | 监听端口，默认 9880 |

两个已知坑，代码里已经处理：

- `torchaudio.load()` 在 2.11 强制走 torchcodec，而 torchcodec 的 FFmpeg DLL 常加载失败。
  `f5_server.py` 在导入前把 `torchaudio.load` 换成 soundfile 实现。
- `f5-tts_infer-cli` 出错时会静默退出（exit 1、无 traceback），所以服务直接调 `f5_tts.api`。
