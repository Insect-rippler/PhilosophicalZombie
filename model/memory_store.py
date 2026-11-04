"""记忆 ↔ 向量库。两级文本、一个向量库。

## 记忆的四级阶梯

    会话切片 ──► 记忆块 ──► 常驻记忆 ──► 人格
    slices.json  blocks.json  MEMORY.md   PERSONA.md
    ↑ 都走向量检索，按上下文省开销          ↑ 整段常驻，不检索

- **会话切片**（slices.json）：一场对话按时间/长度切出来的原始块。
- **记忆块**（blocks.json）：已经升格、但还没常驻。按「对某人的看法 + 与之同行的经历」
  组织，语义上既不是知识也不是常驻记忆，是"特殊记忆"。省上下文所以不放进 MEMORY.md，
  但也不是普通切片，检索时优先级更高。

  写法约定：**text 自带人名**，别把人物和描述拆开。
  ✅「伊奥莱塔·罗素，一位大骑士长，也是我的养母。她把我养大……」
  ❌「大骑士长，我的养母。她把我养大……」（开头没有名字，读者不知道在说谁）
  `tag` 标记这条描述的主体（如"奥比娜"），同一主体可以有多条块，tag 相同的归为一组。
- **常驻记忆**（MEMORY.md）：整段注入，不检索。
- **人格**（PERSONA.md）：从常驻记忆里提炼，最顶层。

## 文件

- `slices.json` / `blocks.json` —— 文本与元数据，手改的就是它们：

      {"model": "bge-small-zh-v1.5", "dim": 512,
       "items": [{"id": "mem-000000", "text": "……", "recalls": 3}]}

  切片 id 前缀 `mem-`，记忆块前缀 `blk-`；记忆块额外带 `tag`（主体标签，同主体可多条）。

- `memory_vectors.npz` —— **两级的向量都在这里**，按 id 存，不是两份索引。
  删了能从两个 json 重建。

## 功能

1. `search(query, top_k, min_score, kind=None)` → `[{id, text, score, chars, kind, tag}]`
2. 单条维护：`add(texts, kind=...)` / `update(id, text)` / `delete(ids)` / `sync_one(id)`
3. 全量对账：`sync_all()`
4. 命中次数：`hits(id)` / `hits()`

改了 text 就 vector 失效。npz 里存了 sha1 指纹，载入时能发现并自动重算。

## 命令行

    python memory_store.py search "一段文本" [-k 5] [-t 0.55] [--kind slice|block]
    python memory_store.py sync              # 全量对账
    python memory_store.py sync mem-000012   # 只重算这一条
    python memory_store.py add "新切片" --kind slice
    python memory_store.py add "奥比娜，奥格尼斯科的铁匠……" --kind block --tag "奥比娜"
    python memory_store.py hits              # 命中次数
    python memory_store.py info
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent          # model/
ROOT = HERE.parent                              # 项目根
MODEL_DIR = HERE / "bge-small-zh-v1.5"          # 向量模型随代码放在 model/
# 记忆数据属于产物，留在 ConsciousnessRealm/embedding/ 不动
DATA_DIR = ROOT / "ConsciousnessRealm" / "embedding"
SLICES_PATH = DATA_DIR / "slices.json"          # 会话切片
BLOCKS_PATH = DATA_DIR / "blocks.json"          # 记忆块（升格但未常驻）
VEC_PATH = DATA_DIR / "memory_vectors.npz"      # 两级共用的向量库

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
MAX_TOKENS = 512
DIM = 512
DEFAULT_MIN_SCORE: float | None = 0.55

# 记忆块是蒸馏过的短文本，和查询的字面重合少，分数天然比切片低一截。
# 实测：同一句提问，切片 0.61，对应的人物记忆块 0.52。所以两级分开设下限，
# 否则 0.55 会把所有记忆块挡在外面。数值可按真实语料再调。
BLOCK_MIN_SCORE: float | None = 0.40

PREFIX = {"slice": "mem", "block": "blk"}

_lock = threading.RLock()
_model = None
_tokenizer = None


class MemoryError_(RuntimeError):
    """记忆库操作失败。"""


# ---------------- 编码 ----------------

def _load_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    import torch
    from transformers import AutoModel, AutoTokenizer
    from transformers.utils import logging as hf_logging

    hf_logging.disable_progress_bar()
    if not MODEL_DIR.is_dir():
        raise MemoryError_(f"找不到模型目录：{MODEL_DIR}")
    _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    _model = AutoModel.from_pretrained(str(MODEL_DIR))
    _model.eval()
    torch.set_grad_enabled(False)
    return _model, _tokenizer


def encode(texts: Sequence[str], *, is_query: bool = False, batch_size: int = 32) -> np.ndarray:
    """文本 → (n, 512) float32，L2 归一化。is_query 时加检索指令前缀。"""
    if not texts:
        return np.zeros((0, DIM), dtype="float32")
    import torch

    model, tokenizer = _load_model()
    if is_query:
        texts = [QUERY_INSTRUCTION + t for t in texts]

    out = []
    for i in range(0, len(texts), batch_size):
        batch = tokenizer(list(texts[i:i + batch_size]), padding=True, truncation=True,
                          max_length=MAX_TOKENS, return_tensors="pt")
        with torch.no_grad():
            hidden = model(**batch).last_hidden_state
        out.append(hidden[:, 0].cpu().numpy().astype("float32"))     # CLS 池化
    vecs = np.vstack(out)
    return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# ---------------- 库 ----------------

class MemoryLibrary:
    """两级记忆（切片 / 记忆块）+ 一个向量库。"""

    def __init__(self,
                 slices_path: str | Path = SLICES_PATH,
                 blocks_path: str | Path = BLOCKS_PATH,
                 vector_path: str | Path = VEC_PATH):
        self.slices_path = Path(slices_path)
        self.blocks_path = Path(blocks_path)
        self.vector_path = Path(vector_path)
        self.items: list[dict] = []
        self.vecs: dict[str, np.ndarray] = {}
        self.hashes: dict[str, str] = {}
        self._load()

    # ---- 读 ----
    def _load(self) -> None:
        self._load_items(self.slices_path, "slice")
        self._load_items(self.blocks_path, "block")
        self._load_vectors()      # 先读向量，分配 id 时才能避开孤儿 id
        self._assign_ids()

    def _load_items(self, path: Path, kind: str) -> None:
        if not path.exists() or not path.stat().st_size:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MemoryError_(f"{path.name} 不是合法 JSON：{exc}") from None
        raw = data.get("items") if isinstance(data, dict) else data
        for it in raw or []:
            if isinstance(it, dict) and str(it.get("text") or "").strip():
                item = {
                    "id": str(it.get("id") or "").strip() or None,
                    "text": it["text"],
                    "recalls": int(it.get("recalls") or 0),
                    "kind": kind,
                }
                if kind == "block" and it.get("tag"):
                    item["tag"] = str(it["tag"])
                self.items.append(item)

    def _assign_ids(self) -> None:
        # 避开两级文件里已有的 id，也避开向量库里的孤儿 id，防止新条目捡到别人的旧向量
        used = {it["id"] for it in self.items if it["id"]} | set(self.vecs)
        for kind, prefix in PREFIX.items():
            nums = [int(i.split("-")[-1]) for i in used
                    if i.startswith(f"{prefix}-") and i.split("-")[-1].isdigit()]
            nxt = max(nums, default=-1) + 1
            for it in self.items:
                if it["kind"] != kind or it["id"]:
                    continue
                while f"{prefix}-{nxt:06d}" in used:
                    nxt += 1
                it["id"] = f"{prefix}-{nxt:06d}"
                used.add(it["id"])
                nxt += 1

    def _load_vectors(self) -> None:
        if not self.vector_path.exists():
            return
        try:
            with np.load(self.vector_path, allow_pickle=False) as b:
                if "ids" not in b.files:
                    print(f"[warn] {self.vector_path.name} 是旧格式（无 id），将重建",
                          file=sys.stderr)
                    return
                vecs = np.asarray(b["vecs"], dtype="float32")
                hashes = [str(h) for h in b["hashes"]]
                ids = [str(i) for i in b["ids"]]
        except (OSError, ValueError, KeyError) as exc:
            print(f"[warn] {self.vector_path.name} 读不出来（{exc}），将重建", file=sys.stderr)
            return
        for i, ident in enumerate(ids):
            if i < len(vecs) and i < len(hashes):
                self.vecs[ident] = vecs[i]
                self.hashes[ident] = hashes[i]

    # ---- 写 ----
    def _save(self) -> None:
        self._assign_ids()
        for kind, path in (("slice", self.slices_path), ("block", self.blocks_path)):
            items = [{k: v for k, v in it.items() if k != "kind"}
                     for it in self.items if it["kind"] == kind]
            payload = {"model": "bge-small-zh-v1.5", "dim": DIM, "items": items}
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)

        if not self.items:
            self.vector_path.unlink(missing_ok=True)
            return
        ids = [it["id"] for it in self.items]
        missing = [i for i in ids if i not in self.vecs]
        if missing:
            raise MemoryError_(f"以下条目没有向量，先 sync：{missing[:5]}")
        tmpv = self.vector_path.with_suffix(".npz.tmp.npz")
        with open(tmpv, "wb") as f:          # 传文件对象，numpy 不会自己补后缀
            np.savez(f,
                     vecs=np.vstack([self.vecs[i] for i in ids]).astype("float32"),
                     hashes=np.asarray([self.hashes.get(i, "") for i in ids], dtype="<U16"),
                     ids=np.asarray(ids, dtype="<U16"))
        Path(tmpv).replace(self.vector_path)

    # ---- 向量对齐 ----
    def _ensure_vectors(self, ids: Iterable[str] | None = None) -> int:
        """缺失或指纹不符的条目就地重算，返回重算条数。"""
        targets = list(ids) if ids is not None else [it["id"] for it in self.items]
        by_id = {it["id"]: it for it in self.items}
        todo = [i for i in targets
                if i in by_id and (i not in self.vecs or
                                   self.hashes.get(i) != _sha(by_id[i]["text"]))]
        if not todo:
            return 0
        fresh = encode([by_id[i]["text"] for i in todo], is_query=False)
        for row, ident in enumerate(todo):
            self.vecs[ident] = fresh[row]
            self.hashes[ident] = _sha(by_id[ident]["text"])
        return len(todo)

    # ---------- 功能 1：检索 ----------
    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float | None = DEFAULT_MIN_SCORE,
        block_min_score: float | None = BLOCK_MIN_SCORE,
        record: bool = True,
        kind: str | None = None,
    ) -> list[dict]:
        """返回 [{id, text, score, chars, kind, tag}]，按向量相似度降序。

        纯向量召回，没有名字兜底。记忆块的 text 自带人名，靠文本本身被检索到；
        分数不够就先改文本划分，而不是加别的机制。
        kind=None 检索两级，'slice'/'block' 只查其中一级。
        """
        pool = [it for it in self.items if kind is None or it["kind"] == kind]
        if not pool or not query.strip():
            return []

        hits: list[dict] = []
        self._ensure_vectors([it["id"] for it in pool])
        q = encode([query.strip()], is_query=True)[0]
        ids = [it["id"] for it in pool]
        scores = np.vstack([self.vecs[i] for i in ids]) @ q
        order = np.argsort(-scores)

        for idx in order[:max(1, min(int(top_k), len(order)))]:
            score = float(scores[idx])
            item = pool[int(idx)]
            floor = block_min_score if item["kind"] == "block" else min_score
            if floor is not None and score < floor:
                continue
            hit = {"id": item["id"], "kind": item["kind"], "text": item["text"],
                   "score": round(score, 4), "chars": len(item["text"])}
            if item.get("tag"):
                hit["tag"] = item["tag"]
            hits.append(hit)
            if len(hits) >= top_k:
                break

        hits = hits[:top_k]
        if record and hits:
            for h in hits:
                for it in self.items:
                    if it["id"] == h["id"]:
                        it["recalls"] += 1
                        break
            self._save()
        return hits

    # ---------- 功能 2：单条维护 ----------
    def add(self, texts: str | Sequence[str], *, kind: str = "slice",
            tag: str | None = None) -> list[str]:
        """新增条目并计算向量，返回新 id。kind='block' 时可带 tag（对谁）。"""
        if kind not in PREFIX:
            raise MemoryError_(f"kind 只能是 slice 或 block，收到 {kind}")
        texts = [texts] if isinstance(texts, str) else [t for t in texts if str(t).strip()]
        if not texts:
            return []
        prefix = PREFIX[kind]
        new_ids = []
        for text in texts:
            idx = 0
            while any(it["id"] == f"{prefix}-{idx:06d}" for it in self.items):
                idx += 1
            ident = f"{prefix}-{idx:06d}"
            item = {"id": ident, "text": text, "recalls": 0, "kind": kind}
            if kind == "block" and tag:
                item["tag"] = tag
            self.items.append(item)
            new_ids.append(ident)
        self._ensure_vectors(new_ids)
        self._save()
        return new_ids

    def update(self, ident: str, text: str) -> None:
        """改文本并重算该条向量。"""
        for it in self.items:
            if it["id"] == ident:
                if not text.strip():
                    raise MemoryError_("text 不能为空；要删就调用 delete")
                it["text"] = text
                self._ensure_vectors([ident])
                self._save()
                return
        raise MemoryError_(f"没有这条记忆：{ident}")

    def delete(self, ids: str | Sequence[str]) -> int:
        """删除条目及其向量，返回删除条数。"""
        ids = [ids] if isinstance(ids, str) else list(ids)
        before = len(self.items)
        self.items = [it for it in self.items if it["id"] not in set(ids)]
        for ident in ids:
            self.vecs.pop(ident, None)
            self.hashes.pop(ident, None)
        if len(self.items) != before:
            self._save()
        return before - len(self.items)

    def sync_one(self, ident: str) -> bool:
        """只重算指定条目的向量。"""
        if not any(it["id"] == ident for it in self.items):
            raise MemoryError_(f"没有这条记忆：{ident}")
        n = self._ensure_vectors([ident])
        if n:
            self._save()
        return bool(n)

    # ---------- 功能 3：全量对账 ----------
    def sync_all(self) -> dict:
        """把向量库对齐到两个记忆文件：删孤儿、补新增、重算文本变更。"""
        current = {it["id"] for it in self.items}
        removed = [i for i in list(self.vecs) if i not in current]
        for ident in removed:
            self.vecs.pop(ident, None)
            self.hashes.pop(ident, None)

        before = dict(self.hashes)
        n = self._ensure_vectors()
        added = sum(1 for i in (it["id"] for it in self.items) if i not in before)
        updated = n - added
        self._save()
        return {
            "total": len(self.items),
            "slices": sum(1 for it in self.items if it["kind"] == "slice"),
            "blocks": sum(1 for it in self.items if it["kind"] == "block"),
            "added": added,
            "updated": updated,
            "removed": len(removed),
            "unchanged": len(self.items) - added - updated,
        }

    # ---------- 功能 4：命中次数 ----------
    def hits(self, ident: str | None = None):
        """ident=None 返回 {id: 次数}；给定 id 返回该条的次数。"""
        if ident is not None:
            for it in self.items:
                if it["id"] == ident:
                    return it["recalls"]
            raise MemoryError_(f"没有这条记忆：{ident}")
        return {it["id"]: it["recalls"] for it in self.items}


# ---------------- 模块级默认实例 ----------------

_default: MemoryLibrary | None = None


def lib() -> MemoryLibrary:
    global _default
    if _default is None:
        _default = MemoryLibrary()
    return _default


def search(query: str, *, top_k: int = 5, min_score: float | None = DEFAULT_MIN_SCORE,
           block_min_score: float | None = BLOCK_MIN_SCORE,
           record: bool = True, kind: str | None = None) -> list[dict]:
    return lib().search(query, top_k=top_k, min_score=min_score,
                        block_min_score=block_min_score, record=record, kind=kind)


def add(texts, *, kind: str = "slice", tag: str | None = None) -> list[str]:
    return lib().add(texts, kind=kind, tag=tag)


def update(ident: str, text: str) -> None:
    lib().update(ident, text)


def delete(ids) -> int:
    return lib().delete(ids)


def sync_one(ident: str) -> bool:
    return lib().sync_one(ident)


def sync_all() -> dict:
    return lib().sync_all()


def hits(ident: str | None = None):
    return lib().hits(ident)


# ---------------- 命令行 ----------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="memory_store.py", description="记忆 ↔ 向量库")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="检索")
    s.add_argument("query")
    s.add_argument("-k", "--top-k", type=int, default=5)
    s.add_argument("-t", "--min-score", type=float, default=DEFAULT_MIN_SCORE,
                   help="相似度下限；传 0 表示不过滤")
    s.add_argument("--kind", choices=("slice", "block"), default=None,
                   help="只查某一级；不传则两级都查")

    y = sub.add_parser("sync", help="对账（不给 id 则全量）")
    y.add_argument("id", nargs="?")

    a = sub.add_parser("add", help="新增一条")
    a.add_argument("text")
    a.add_argument("--kind", choices=("slice", "block"), default="slice")
    a.add_argument("--tag", default=None, help="记忆块：这条描述的主体标签")

    h = sub.add_parser("hits", help="命中次数")
    h.add_argument("id", nargs="?")

    sub.add_parser("info", help="规模与配置")

    args = ap.parse_args(argv)
    L = lib()

    try:
        if args.cmd == "search":
            rows = L.search(args.query, top_k=args.top_k,
                            min_score=args.min_score, kind=args.kind)
            if not rows:
                print("（无命中）")
            for r in rows:
                label = f"{r['kind']}"
                if r.get("tag"):
                    label += f"·{r['tag']}"
                print(f"{r['score']:.4f}  {r['id']}  [{label}]  {r['chars']} 字  "
                      f"命中 {L.hits(r['id'])} 次")
                for line in r["text"].splitlines()[:3]:
                    print(f"   {line[:100]}")
        elif args.cmd == "sync":
            if args.id:
                print("重算" if L.sync_one(args.id) else "无需重算", args.id)
            else:
                r = L.sync_all()
                print(f"共 {r['total']} 条（切片 {r['slices']} / 记忆块 {r['blocks']}）："
                      f"新增 {r['added']}，重算 {r['updated']}，"
                      f"删孤儿 {r['removed']}，未变 {r['unchanged']}")
        elif args.cmd == "add":
            ids = L.add(args.text, kind=args.kind, tag=args.tag)
            print("新增", ids)
        elif args.cmd == "hits":
            if args.id:
                print(f"{args.id}: {L.hits(args.id)} 次")
            else:
                rows = sorted(L.hits().items(), key=lambda kv: -kv[1])
                for ident, n in rows[:20]:
                    print(f"  {ident}  {n} 次")
                print(f"合计 {sum(L.hits().values())} 次 / {len(rows)} 条")
        else:
            print(f"切片：{L.slices_path.name}  "
                  f"{sum(1 for it in L.items if it['kind'] == 'slice')} 条")
            print(f"记忆块：{L.blocks_path.name}  "
                  f"{sum(1 for it in L.items if it['kind'] == 'block')} 条")
            print(f"向量：{L.vector_path.name}  {len(L.vecs)} 条（两级共用）")
            print(f"默认阈值：{DEFAULT_MIN_SCORE}")
    except MemoryError_ as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



