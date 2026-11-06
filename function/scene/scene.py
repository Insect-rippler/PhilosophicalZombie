"""场景工具：交代环境变化（光、声、天气、旁人的动作）。

    echo '{"description": "风把云推开了"}' | python function/scene/scene.py

正文只写台词，动作走 active，环境走这里。
"""

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "state" / "realTime" / "scene.jsonl"


def main():
    raw = sys.stdin.read().strip() or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    text = str(data.get("description") or "").strip()
    if not text:
        print(json.dumps({"未记录": "场景描述为空"}, ensure_ascii=False))
        return

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": stamp, "text": text}, ensure_ascii=False) + "\n")

    print(json.dumps({"已记录": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
