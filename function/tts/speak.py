"""把文本合成为语音文件，返回音频路径。

调用方式与其它工具一致：stdin 传 JSON 参数，stdout 打印 JSON 结果。

输入：
    {"text": "要读出来的话", "voice": "可选，覆盖音色", "rate": "可选，如 +10%", "backend": "可选 edge|local"}

输出：
    {"audio": "<绝对路径>", "chars": 24, "backend": "edge", "voice": "...", "cloned": false, "bytes": 45678}

两个后端（在 config.json 里切换）：
- edge  ：微软 Edge 在线语音。零配置、快、中文自然，**但不是克隆音色**，只是占位。
- local ：POST 到本地 TTS 服务（GPT-SoVITS / CosyVoice / F5-TTS 等），
          用 ref_audio_path 指向 PRTS 下载的原声做 zero-shot 克隆。
          服务没起时直接报错，不会假装合成成功。
"""

from pathlib import Path
import asyncio
import json
import sys
import uuid
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = Path(__file__).resolve().parent / "config.json"


def loadConfig():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8")).get("speak", {})
    except FileNotFoundError:
        raise FileNotFoundError(
            f"[Error] 配置文件不存在：{CFG_PATH}。\n"
            "请检查路径是否正确、文件是否被移动或删除。\n"
            "不得编造配置内容，不得假装合成成功。\n"
            "应以角色身份表达困惑、停顿或询问，不得原样复述本警告。"
        )
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"[Error] 配置文件解析失败：{CFG_PATH}：{e}。\n"
            "不得编造配置内容，不得假装合成成功。\n"
            "应以角色身份表达困惑、停顿或询问，不得原样复述本警告。"
        ) from e


def outPath(outDir, ext):
    d = ROOT / outDir
    d.mkdir(parents=True, exist_ok=True)
    name = f"speak-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.{ext}"
    return d / name


def synthEdge(text, cfg, dest):
    try:
        import edge_tts
    except ImportError as e:
        raise ImportError(
            "[Error] 缺少 edge-tts。安装：pip install --index-url https://pypi.org/simple edge-tts\n"
            "安装前不要声称已经说话。"
        ) from e

    async def run():
        comm = edge_tts.Communicate(
            text,
            cfg.get("voice") or "zh-CN-XiaoxiaoNeural",
            rate=cfg.get("rate") or "+0%",
            pitch=cfg.get("pitch") or "+0Hz",
            volume=cfg.get("volume") or "+0%",
        )
        await comm.save(str(dest))

    asyncio.run(run())


def _fill(template, text):
    """递归替换 {text} 与 {root}，其余原样保留。"""
    if isinstance(template, dict):
        return {k: _fill(v, text) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, text) for v in template]
    if isinstance(template, str):
        return template.replace("{text}", text).replace("{root}", str(ROOT))
    return template


def synthLocal(text, cfg, dest):
    try:
        import requests
    except ImportError as e:
        raise ImportError("[Error] 缺少 requests，无法调用本地 TTS 服务。") from e

    url = (cfg.get("url") or "").strip()
    if not url:
        raise ValueError(
            "[Error] local 后端缺少 url。请先在 function/tts/config.json 里填写本地 TTS 服务地址。\n"
            "服务未配置时不得假装已经说话。"
        )
    body = _fill(cfg.get("body") or {"text": "{text}"}, text)
    headers = cfg.get("headers") or {}
    method = (cfg.get("method") or "POST").upper()

    try:
        if method == "GET":
            resp = requests.get(url, params=body, headers=headers, timeout=(10, 300))
        else:
            resp = requests.post(url, json=body, headers=headers, timeout=(10, 300))
    except Exception as e:
        raise RuntimeError(
            f"[Error] 本地 TTS 服务不可达：{url}（{type(e).__name__}: {e}）。\n"
            "请确认服务已启动、端口与路径正确。服务不可用时不得假装已经说话，"
            "也不得编造音频路径。"
        ) from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"[Error] 本地 TTS 服务返回 HTTP {resp.status_code}：{resp.text[:200]}。\n"
            "不得假装已经说话或编造音频路径。"
        )
    if not resp.content:
        raise RuntimeError("[Error] 本地 TTS 服务返回空音频。不得假装已经说话。")
    dest.write_bytes(resp.content)


def main():
    raw = input()
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"[ERROR] 参数解析出现错误：{e}") from e
    if not isinstance(args, dict):
        raise ValueError(
            "[ERROR] 参数格式错误：期望一个 JSON 对象，实际收到 "
            f"{type(args).__name__}。请以指定 tool 形式重新调用。"
        )

    config = loadConfig()
    backend = (args.get("backend") or config.get("backend") or "edge").lower()
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError(
            "[Error] text 不能为空。要出声就得给出具体内容；"
            "不得用空白文本调用，也不得假装已经说话。"
        )

    maxChars = int(config.get("maxChars") or 600)
    if len(text) > maxChars:
        raise ValueError(
            f"[Error] text 有 {len(text)} 字，超过单次合成上限 {maxChars} 字。\n"
            "请拆成几句分别调用；不要截断原文，也不要省略后半段。"
        )

    outDir = config.get("outDir") or "state/media/audio"
    voice = args.get("voice") or ""

    if backend == "edge":
        cfg = dict(config.get("edge") or {})
        if voice:
            cfg["voice"] = voice
        if args.get("rate"):
            cfg["rate"] = args["rate"]
        dest = outPath(outDir, "mp3")
        synthEdge(text, cfg, dest)
        engine = f"edge:{cfg.get('voice')}"
        cloned = False
    elif backend == "local":
        cfg = dict(config.get("local") or {})
        dest = outPath(outDir, cfg.get("ext") or "wav")
        synthLocal(text, cfg, dest)
        engine = f"local:{cfg.get('url')}"
        cloned = True
    else:
        raise ValueError(
            f"[Error] 未知 backend：{backend}。只能是 'edge' 或 'local'。"
        )

    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(
            f"[Error] 合成未产出音频：{dest}。\n"
            "不得声称已经发出语音，也不得编造音频路径。"
        )

    print(json.dumps({
        "audio": str(dest),
        "chars": len(text),
        "backend": backend,
        "voice": voice or (config.get("edge") or {}).get("voice") if backend == "edge" else "",
        "engine": engine,
        "cloned": cloned,
        "bytes": dest.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
