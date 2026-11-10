# -*- coding: utf-8 -*-
"""锚点检索实验：不改引擎，只验证“她是谁”能不能被向量召回。

用法：
    python lab/anchors/retrieve.py --stats
    python lab/anchors/retrieve.py --search "有人坚持说我记错了" --top 5
    python lab/anchors/retrieve.py --search "他要我写诗换城市" --top 3 --json
    python lab/anchors/retrieve.py --list --kind boundary
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "model"))

ANCHORS = HERE / "anchors.jsonl"
CACHE = HERE / "vectors.npz"


def index_text(item):
    """索引时带上标签：抽象问法（「我们算不算熟」）只靠正文召回很弱。"""
    tags = "、".join(item.get("tags") or [])
    return item["text"] + (f"。关键词：{tags}" if tags else "")


def load_anchors(kind=None):
    items = []
    for line in ANCHORS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if kind and item.get("kind") != kind:
            continue
        items.append(item)
    return items


def build_vectors(items):
    """有缓存就用缓存；文本变了才重新编码。"""
    import hashlib
    import numpy as np

    def sha(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    keys = [sha(index_text(it)) for it in items]
    cached = {}
    if CACHE.exists():
        try:
            data = np.load(CACHE, allow_pickle=True)
            cached = dict(zip(list(data["keys"]), data["vecs"]))
        except Exception:                        # noqa: BLE001
            cached = {}
    todo = [it for it, k in zip(items, keys) if k not in cached]
    if todo:
        from model.memory_store import encode
        fresh = encode([index_text(it) for it in todo], is_query=False)
        for it, key, vec in zip(todo, [keys[items.index(it)] for it in todo], fresh):
            cached[key] = vec
        try:
            np.savez(CACHE,
                     keys=np.array(list(cached.keys())),
                     vecs=np.stack(list(cached.values())))
        except OSError:
            pass
    return [cached[k] for k in keys]


def search(query, top=5, kind=None):
    import numpy as np
    from model.memory_store import encode

    items = load_anchors(kind)
    if not items:
        return []
    vecs = np.stack(build_vectors(items))
    t0 = time.perf_counter()
    q = encode([query], is_query=True)[0]
    scores = vecs @ q
    t1 = time.perf_counter()
    order = np.argsort(-scores)[:top]
    out = []
    for idx in order:
        item = dict(items[int(idx)])
        item["score"] = round(float(scores[int(idx)]), 4)
        out.append(item)
    return out, round((t1 - t0) * 1000, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--kind")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.stats or (not args.search and not args.list):
        items = load_anchors()
        kinds = {}
        for it in items:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        print(f"锚点 {len(items)} 条：" + "，".join(f"{k} {v}" for k, v in kinds.items()))
        print(f"平均长度 {sum(len(i['text']) for i in items) // max(1, len(items))} 字")
        return 0

    if args.list:
        for it in load_anchors(args.kind):
            print(f"{it['id']}  {it['kind']:<10} {it['text']}")
        return 0

    hits, ms = search(args.search, args.top, args.kind)
    if args.json:
        print(json.dumps({"query": args.search, "ms": ms, "hits": hits},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"查询：{args.search}   （编码+检索 {ms} ms）")
    for it in hits:
        print(f"  {it['score']:.3f}  {it['id']}  {it['kind']:<10} {it['text'][:52]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
