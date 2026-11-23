"""升格第一步：把选中的切片整理成记忆块。

不调背景上下文，只给切片与规则；模型输出合并后的块与来源 id。
"""

import json
import time
from pathlib import Path

import promote
from pathlib import Path as _P
import score as scorer

PROMPT = Path(__file__).resolve().parent / "prompts" / "block.md"
BATCH = 30


def _log(row):
    try:
        import os
        path = _P(os.environ["PZ_DATA"]) / "dream.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(__import__("json").dumps(row, ensure_ascii=False) + "\n")
    except Exception:                       # noqa: BLE001
        pass


def select(items, now=None, cfg=None, limit=BATCH):
    picked = []
    for item in items:
        if item.get("kind") != "slice":
            continue
        if item.get("level") not in (None, "slice"):
            continue
        if not promote.eligible(item, stage=1, cfg=cfg):
            continue
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _payload(slices):
    system = {"role": "system", "content": PROMPT.read_text(encoding="utf-8")}
    lines = []
    for item in slices:
        lines.append(f"### {item['id']}（{item.get('ts') or '时间不详'}，"
                     f"重要性 {item.get('importance') or 0}）\n{item['text']}")
    user = {"role": "user", "content": "## 切片\n\n" + "\n\n".join(lines)}
    try:
        background = scorer._background()
    except Exception:                       # noqa: BLE001
        background = []
    return {
        "model": (__import__("core").harness.get("model") or "deepseek-flash"),
        "messages": [system] + background + [user],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def make_blocks(slices):
    import modelio
    reply = modelio.send(_payload(slices)) or {}
    text = (((reply.get("choices") or [{}])[0].get("message") or {})
            .get("content") or "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    known = {item["id"] for item in slices}
    out = []
    for row in data.get("blocks") or []:
        body = str(row.get("text") or "").strip()
        sources = [str(x) for x in (row.get("sources") or []) if str(x) in known]
        if not body or not sources:
            continue
        out.append({
            "text": body,
            "sources": sources,
            "importance": int(row.get("importance") or 0) or
                          max(int(s.get("importance") or 0) for s in slices
                              if s["id"] in sources),
        })
    return out


def store_blocks(lib, blocks, dry_run=False):
    stamp = time.strftime("%Y%m%d%H%M%S")
    now = time.time()
    added = []
    used = set()
    for n, row in enumerate(blocks):
        ident = f"block-{stamp}-{n:03d}"
        lib.items.append({
            "id": ident,
            "kind": "block",
            "text": row["text"],
            "importance": int(row.get("importance") or 0),
            "recalls": 0,
            "recallsAt": [now],
            "level": "block",
            "createdAt": now,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        used.update(row["sources"])
        added.append(ident)
    for row, ident in zip(blocks, added):
        _log({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "id": ident,
              "action": "block", "sources": row["sources"],
              "importance": row["importance"]})
    if not dry_run and added:
        lib.items = [it for it in lib.items if it["id"] not in used]
        lib.sync_all()
    return {"blocks": added, "merged": sorted(used)}


def run(lib, cfg=None, dry_run=False, limit=BATCH):
    slices = select(lib.items, cfg=cfg, limit=limit)
    if not slices:
        return {"blocks": [], "merged": [], "picked": 0}
    blocks = make_blocks(slices)
    result = store_blocks(lib, blocks, dry_run=dry_run)
    result["picked"] = len(slices)
    return result
