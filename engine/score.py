"""给记忆切片打重要性：1~5，按她的背景判。

上下文与主代理同步（背景段），但 system 换成评分任务；
不带 session、plan、state。
"""

import json
from pathlib import Path

import core
import modelio

PROMPT = Path(__file__).resolve().parent / "prompts" / "score.md"
BATCH = 20


def _background():
    """和主代理一样的背景段，不带 session / state / plan。"""
    try:
        return core.build_background(core.load_skills())
    except Exception:                       # noqa: BLE001
        return []


def _payload(background, batch):
    system = {"role": "system", "content": PROMPT.read_text(encoding="utf-8")}
    lines = []
    for item in batch:
        lines.append(f"### {item['id']}\n{item['text']}")
    user = {"role": "user", "content": "## 待评切片\n\n" + "\n\n".join(lines)}
    body = {
        "model": (core.harness.get("model") or "deepseek-flash"),
        "messages": [system] + background + [user],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    return body


def score_slices(items, batch_size=BATCH):
    """就地写入 importance 与 why；返回 items。"""
    background = _background()
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        reply = modelio.send(_payload(background, batch)) or {}
        text = (((reply.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        got = {str(row.get("id")): row for row in (data.get("items") or [])}
        for item in batch:
            row = got.get(item["id"]) or {}
            try:
                score = int(row.get("importance") or 0)
            except (TypeError, ValueError):
                score = 0
            if 1 <= score <= 5:
                item["importance"] = score
            item["why"] = str(row.get("why") or "")[:80]
    return items
