"""记一条记忆块。

这是模型唯一能直接写记忆的入口：把当下值得记住的事写进块向量库，
之后靠召回进入上下文。它**不能**写 MEMORY.md 或 PERSONA.md——
那两个文件只在 dream 流程里、按严格判据才会被改。

stdin: {"text": "...", "tag": "涉及的人或事物"}
stdout: {"ok": true, "id": "blk-000123"}
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

import memory_store  # noqa: E402

MIN_CHARS = 10
MAX_CHARS = 200


def main():
    raw = sys.stdin.read().strip()
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"[ERROR] 参数解析失败：{e}") from e
    if not isinstance(args, dict):
        raise ValueError("[ERROR] 参数应为 JSON 对象。")

    text = str(args.get("text") or "").strip()
    tag = str(args.get("tag") or "").strip()
    if len(text) < MIN_CHARS:
        raise ValueError(f"[ERROR] text 太短（至少 {MIN_CHARS} 字）：记不下具体内容。")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    ids = memory_store.add(text, kind="block", tag=tag or None, sources=["对话"])
    print(json.dumps({"ok": True, "id": ids[0] if ids else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
