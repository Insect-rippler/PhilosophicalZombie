"""升格第二步：记忆块 → 常驻记忆。

升格率只决定要不要调模型：这一轮有达标的块才调一次。
模型采纳的写成常驻，没采纳的删除。
"""

import json
import os
import time
from pathlib import Path

import promote

PROMPT = Path(__file__).resolve().parent / "prompts" / "resident.md"
BATCH = 20


def _log(row):
    try:
        path = Path(os.environ["PZ_DATA"]) / "dream.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                       # noqa: BLE001
        pass


def pick(items, cfg=None):
    out = []
    for item in items:
        if item.get("kind") != "block":
            continue
        if item.get("level") == "resident":
            continue
        if promote.eligible(item, stage=2, cfg=cfg):
            out.append(item)
    return out


def _payload(blocks, residents):
    import score as scorer
    system = {"role": "system", "content": PROMPT.read_text(encoding="utf-8")}
    lines = []
    for item in blocks:
        lines.append(f"### {item['id']}（重要性 {item.get('importance') or 0}）\n"
                     f"{item['text']}")
    body = "## 待整理的记忆块\n\n" + "\n\n".join(lines)
    if residents:
        body += "\n\n## 已有的常驻记忆（避免重复）\n\n" + "\n".join(
            f"- {r['text'][:200]}" for r in residents[:10])
    user = {"role": "user", "content": body}
    try:
        background = scorer._background()
    except Exception:                       # noqa: BLE001
        background = []
    import core
    return {
        "model": (core.harness.get("model") or "deepseek-flash"),
        "messages": [system] + background + [user],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def _call(blocks, residents):
    import modelio
    reply = modelio.send(_payload(blocks, residents)) or {}
    text = (((reply.get("choices") or [{}])[0].get("message") or {})
            .get("content") or "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def run(lib, cfg=None, dry_run=False, limit=BATCH):
    blocks = pick(lib.items, cfg=cfg)[:limit]
    if not blocks:
        return {"called": False, "accepted": [], "rejected": []}
    residents = [it for it in lib.items if it.get("level") == "resident"]
    data = _call(blocks, residents)
    if data is None:
        return {"called": True, "accepted": [], "rejected": [], "error": "输出不是 JSON"}

    known = {b["id"] for b in blocks}
    accepted = []
    used = set()
    for row in data.get("accepted") or []:
        body = str(row.get("text") or "").strip()
        sources = [str(x) for x in (row.get("sources") or []) if str(x) in known]
        if not body or not sources:
            continue
        accepted.append({"text": body, "sources": sources,
                         "importance": int(row.get("importance") or 0)})
        used.update(sources)
    # 没被任何常驻记忆用到的块，一律删除
    rejected = known - used

    if not dry_run:
        stamp = time.strftime("%Y%m%d%H%M%S")
        now = time.time()
        for n, row in enumerate(accepted):
            lib.items.append({
                "id": f"resident-{stamp}-{n:03d}",
                "kind": "block",
                "level": "resident",
                "text": row["text"],
                "importance": row["importance"] or 3,
                "recalls": 0,
                "opportunities": 0,
                "createdAt": now,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        lib.items = [it for it in lib.items
                     if it["id"] not in used and it["id"] not in rejected]
        lib.sync_all()
        _log({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "resident_call",
              "accepted": [r["sources"] for r in accepted],
              "rejected": sorted(rejected)})
    return {"called": True, "accepted": [r["text"][:60] for r in accepted],
            "rejected": sorted(rejected)}
