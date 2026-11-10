"""F5-TTS 本地服务：POST /tts 返回 wav 字节流。

必须在 f5 环境里跑（Python 3.11）：

    <conda env f5>\\python.exe function\\tts\\f5_server.py

环境变量：
    F5_CKPT     权重 .safetensors（默认 model/f5/F5TTS_v1_Base/model_1250000.safetensors）
    F5_VOCAB    vocab.txt
    F5_VOCODER  声码器目录（config.yaml + pytorch_model.bin，默认 model/vocos）
    F5_REF      默认参考音频（不传时使用）
    F5_REF_TEXT 默认参考文本
    F5_PORT     端口，默认 9880

项目自包含：模型、词表、声码器都在 model/ 下，不读写 HuggingFace 缓存，
也不需要联网（启动时强制 HF_HUB_OFFLINE）。

接口：
    GET  /health -> {"ok": true, "model": "F5TTS_v1_Base"}
    POST /tts    -> {"text": "...", "ref_audio_path": "...", "ref_text": "...", "nfe_step": 16}
                    返回 audio/wav

CPU 上 RTF 约 25~30×：6 秒音频需要 3 分钟左右，调用方要把超时放宽到 300 秒以上。
"""

import io
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # 本文件在 model/ 下
MODEL_DIR = ROOT / "model" / "f5" / "F5TTS_v1_Base"
CKPT = Path(os.environ.get("F5_CKPT", MODEL_DIR / "model_1250000.safetensors"))
VOCAB = Path(os.environ.get("F5_VOCAB", MODEL_DIR / "vocab.txt"))
VOCODER = Path(os.environ.get("F5_VOCODER", ROOT / "model" / "vocos"))
REF = Path(os.environ.get("F5_REF",
            ROOT / "state" / "media" / "voice" / "char_4098_vvana" / "ref_cn032.wav"))
REF_TEXT = os.environ.get("F5_REF_TEXT",
           "博士，暂时接受此刻灵感的枯竭，并记住这种感受。对这未完的诗篇，我将满怀期待。")
PORT = int(os.environ.get("F5_PORT", "9880"))

# 全部资产在本项目内，禁掉联网与 HF 缓存查找
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# torchaudio 2.11 的 load() 依赖 torchcodec，而后者的 FFmpeg DLL 在这台机器上加载不了。
# f5 只用它读参考音频，换成 soundfile 即可，必须在 import f5_tts 之前打补丁。
import torch
import torchaudio
import soundfile as sf


def _sf_load(path, **kwargs):
    # 调用方可能传 D:\xxx/yyy 这种混合斜杠，libsndfile 不认，先归一化成原生路径
    data, sr = sf.read(str(Path(path)), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr


torchaudio.load = _sf_load

from f5_tts.api import F5TTS  # noqa: E402

print(f"[f5] 加载 {CKPT.name} ...", flush=True)
MODEL = F5TTS(model="F5TTS_v1_Base", ckpt_file=str(CKPT),
              vocab_file=str(VOCAB), vocoder_local_path=str(VOCODER),
              device="cpu")
print("[f5] 就绪", flush=True)


def synth(payload: dict) -> bytes:
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("text 为空")
    ref = str(Path(payload.get("ref_audio_path") or REF).resolve())
    ref_text = payload.get("ref_text") or REF_TEXT
    nfe = int(payload.get("nfe_step") or 16)

    wav, sr, _ = MODEL.infer(
        ref_file=str(ref), ref_text=ref_text, gen_text=text,
        nfe_step=nfe, cfg_strength=2.0, sway_sampling_coef=-1.0,
    )
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return buf.getvalue()


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "model": "F5TTS_v1_Base"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/tts"):
            self._json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            data = synth(payload)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"[f5] 合成 {len(payload.get('text',''))} 字 -> {len(data)} B", flush=True)
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"[f5] listening on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


