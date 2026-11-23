"""入库：归档 session → 切段 → 切片 → 打重要性 → 写入向量库。"""

import time
from datetime import datetime

import memory_store
import score
import segment
import slice as slicer


def names(rows):
    """用户与角色的称呼：优先用消息里的 name，其次角色卡。"""
    user = next((str(r.get("name")) for r in rows
                 if r.get("role") == "user" and r.get("name")), "")
    char = ""
    try:
        import realm
        char = str(realm.load(realm_path_of("CHARACTER"), "CHARACTER").get("name") or "")
    except Exception:                       # noqa: BLE001
        char = ""
    return {"user": user, "char": char}


def realm_path_of(kind):
    import core
    return core.REALM_ROOT / f"{kind}.md"


def _log(row):
    import json as _json
    import os
    from pathlib import Path as _P
    try:
        path = _P(os.environ["PZ_DATA"]) / "dream.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                       # noqa: BLE001
        pass


def build(rows):
    """只要切片，不落库。"""
    segs = segment.segment(rows)
    out = []
    for n, seg in enumerate(segs):
        part = rows[seg["from"]:seg["to"]]
        for item in slicer.slice_segment(part, seg_index=n, names=names(rows)):
            item["seg_reason"] = seg["reason"]
            out.append(item)
    return out


def store(items, dry_run=False):
    lib = memory_store.lib()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = "slice-" + time.strftime("%Y%m%d%H%M%S")
    existing = {it.get("id") for it in lib.items}
    added = 0
    for n, item in enumerate(items):
        ident = f"{prefix}-{n:04d}"
        while ident in existing:
            n += 1
            ident = f"{prefix}-{n:04d}"
        row = {
            "id": ident,
            "kind": "slice",
            "text": item["text"],
            "importance": int(item.get("importance") or 0),
            "recalls": 0,
            "level": "slice",
            "ts": item.get("ts"),
            "created": stamp,
            "createdAt": time.time(),
            "recallsAt": [],
        }
        lib.items.append(row)
        existing.add(ident)
        added += 1
        _log({"at": stamp, "action": "slice", "id": ident,
              "importance": row["importance"], "chars": len(row["text"]),
              "why": item.get("why") or "",
              "seg_reason": item.get("seg_reason") or ""})
    if not dry_run and added:
        lib.sync_all()
    return added


def run(rows, dry_run=False):
    items = build(rows)
    if items:
        score.score_slices(items)
    added = store(items, dry_run=dry_run)
    return {"slices": len(items), "stored": added}


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        print("用法: python engine/ingest.py <归档 session.jsonl>")
        raise SystemExit(1)
    rows = [json.loads(l) for l in
            open(path, encoding="utf-8").read().splitlines() if l.strip()]
    print(run(rows))
