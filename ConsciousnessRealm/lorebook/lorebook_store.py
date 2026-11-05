"""世界书库：关键词/标签精确匹配（不走向量、不调模型）。

## 这一层在 prompt 里的位置

本模块只负责 `lorebook_momentary`：session 之后、按 `keys` 匹配后注入的具体设定。
整段常驻的世界常识在 `ConsciousnessRealm/LOREBOOK.md`（`lorebook_persistent`）。
paragraph 顺序的权威定义见 `ConsciousnessRealm/AGENT.md`，本文件不读任何配置。

判据：**凡是不容被会话内容盖过的，别放这里的 JSON。**
- 世界基础常识 → `LOREBOOK.md`
- 角色人格与三观 → `PERSONA.md`
- 角色身份、数据、用语 → `CHARACTER.md`
- 具体设定细节 → 本文件

## 数据格式 lorebook.json

    {"entries": [
      {
        "id": "rhodes-island",
        "title": "罗德岛",
        "keys": ["罗德岛", "制药", "阿米娅", "博士"],
        "text": "……",          # 只写这个世界书本身，不要写某个角色的经历
        "tags": ["组织"],
        "priority": 10,
        "min_hits": 1         # 可选：命中几个标签才注入
      }
    ]}

**keys 是对内容打的标签，不是"文中出现过的专有名词"**：
一条知识凡是从某个话题能被想起，那个话题词就该进 keys。

**但 key 的覆盖面不能比条目本身更宽**（概括关系）：
拿更宽的概念当窄条目的标签，等于"提到泛称就注入某个具体实体"。
判定问句：**知道这个词的人，一定知道这条知识吗？**
例：谁都知道"感染者"，但不一定认识"罗德岛"——所以"感染者"不能是「罗德岛」的 key，
它属于 memory_persistent 的世界常识，不在本文件。

**正文只写该知识**。角色是谁的徒弟、谁出身哪里——那是 character 的事。

## min_hits

标签写宽了（"博士""骑士"），条目会长期常驻。`"min_hits": 2` 要求至少命中两个标签。
实测在剧情语料上，min_hits=2 会把触发率从 6.5% 压到 0.4%，属于过度过滤，
所以默认仍是 1。用 `freq` 子命令量过再决定。

## 用法

    from lorebook_store import lookup
    hits = lookup("我们在大骑士领见到了烛骑士")

    python lorebook_store.py match "一段文本"
    python lorebook_store.py check
    python lorebook_store.py freq <session.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE / "lorebook.json"

MIN_KEY_LEN = 2
DEFAULT_MAX_ITEMS = 8
DEFAULT_BUDGET_CHARS = 1200


class LorebookStore:
    """lorebook.json 的内存索引。改文件后重新构造即可生效。"""

    def __init__(self, path: str | Path = DATA_PATH):
        self.path = Path(path)
        self.entries: list[dict] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.entries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[warn] {self.path.name} 读不出来（{exc}），按空库处理", file=sys.stderr)
            self.entries = []
            return
        entries = data.get("entries") if isinstance(data, dict) else data
        self.entries = [e for e in (entries or []) if isinstance(e, dict) and e.get("text")]

    def __len__(self) -> int:
        return len(self.entries)

    # ---- 匹配 ----
    def match_entry(self, entry: dict, haystack: str) -> list[str]:
        return [k for k in (entry.get("keys") or [])
                if len(k) >= MIN_KEY_LEN and k.lower() in haystack]

    def lookup(
        self,
        text: str,
        *,
        max_items: int = DEFAULT_MAX_ITEMS,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """命中即注入。排序：命中标签数 → 最长标签 → priority，均降序。"""
        if not text or not self.entries:
            return []
        haystack = text.lower()

        hits = []
        for entry in self.entries:
            if tags and not (set(tags) & set(entry.get("tags") or [])):
                continue
            matched = self.match_entry(entry, haystack)
            if not matched or len(matched) < int(entry.get("min_hits") or 1):
                continue
            hits.append({
                "id": entry.get("id"),
                "title": entry.get("title") or entry.get("id"),
                "text": entry["text"],
                "tags": entry.get("tags") or [],
                "matched": matched,
                "_rank": (len(matched), max(len(k) for k in matched),
                          int(entry.get("priority") or 0)),
            })

        hits.sort(key=lambda h: tuple(-x for x in h["_rank"]))

        out, used = [], 0
        for h in hits:
            h.pop("_rank", None)
            if len(out) >= max_items or used >= budget_chars:
                break
            out.append(h)
            used += len(h["text"])
        return out

    # ---- 自检 ----
    def check(self) -> list[str]:
        problems = []
        seen_ids: set[str] = set()
        key_owner: dict[str, str] = {}
        for i, e in enumerate(self.entries):
            eid = e.get("id")
            if not eid:
                problems.append(f"第 {i} 条缺 id")
            elif eid in seen_ids:
                problems.append(f"id 重复：{eid}")
            else:
                seen_ids.add(eid)
            keys = e.get("keys") or []
            if not keys:
                problems.append(f"{eid or i}：keys 为空，永远不会被命中")
            mh = int(e.get("min_hits") or 1)
            if mh > max(1, len(keys)):
                problems.append(f"{eid}: min_hits={mh} 大于 key 数量 {len(keys)}，永不触发")
            if mh > 1 and len(keys) < mh + 1:
                problems.append(f"{eid}: min_hits={mh} 但只有 {len(keys)} 个 key，容易永不触发")
            for k in keys:
                if len(k) < MIN_KEY_LEN:
                    problems.append(f"{eid}: 标签「{k}」短于 {MIN_KEY_LEN} 字，容易误命中")
                low = k.lower()
                if low in key_owner and key_owner[low] != eid:
                    problems.append(f"标签「{k}」同时属于 {key_owner[low]} 与 {eid}")
                key_owner[low] = eid
        return problems

    # ---- 触发率统计 ----
    def freq(self, corpus_path: str | Path) -> dict:
        """在真实语料上统计每条知识的触发率和每个标签的出现率。"""
        texts = []
        for line in Path(corpus_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            blocks = rec.get("content")
            if isinstance(blocks, list):
                t = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
            elif isinstance(blocks, str):
                t = blocks
            else:
                continue
            if t.strip():
                texts.append(t.lower())
        n = len(texts) or 1

        rows = []
        for e in self.entries:
            fired = sum(1 for t in texts
                        if len(self.match_entry(e, t)) >= int(e.get("min_hits") or 1))
            per_key = {k: sum(1 for t in texts if k.lower() in t) for k in (e.get("keys") or [])}
            rows.append({"id": e.get("id"), "fired": fired, "rate": fired / n, "keys": per_key})
        return {"messages": n, "rows": rows}


# ---------------- 模块级入口 ----------------

_default: LorebookStore | None = None


def store() -> LorebookStore:
    global _default
    if _default is None:
        _default = LorebookStore()
    return _default


def lookup(text: str, **kwargs) -> list[dict]:
    """按 mention 查世界书。命中的条目注入 lorebook_momentary 段。"""
    return store().lookup(text, **kwargs)


# ---------------- 命令行 ----------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lorebook_store.py", description="世界书标签匹配")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("match", help="对一段文本做匹配")
    m.add_argument("text")
    m.add_argument("-n", "--max-items", type=int, default=DEFAULT_MAX_ITEMS)

    sub.add_parser("check", help="结构自检")

    f = sub.add_parser("freq", help="在语料上统计触发率")
    f.add_argument("corpus")

    args = ap.parse_args(argv)
    st = store()

    if args.cmd == "match":
        hits = st.lookup(args.text, max_items=args.max_items)
        if not hits:
            print("（无命中）")
        for h in hits:
            print(f"[{h['id']}] {h['title']}   命中标签: {', '.join(h['matched'])}")
            print(f"   {h['text'][:110]}")

    elif args.cmd == "check":
        problems = st.check()
        print(f"条目 {len(st)} 条，标签 {sum(len(e.get('keys') or []) for e in st.entries)} 个")
        print("结构检查通过。" if not problems else "")
        for p in problems:
            print("  !", p)

    else:
        rep = st.freq(args.corpus)
        print(f"语料 {rep['messages']} 条消息\n")
        print(f"{'条目':<16} {'触发':>6} {'触发率':>7}   各标签出现率")
        for r in rep["rows"]:
            keys = "  ".join(f"{k}:{v / rep['messages']:.0%}" for k, v in
                             sorted(r["keys"].items(), key=lambda x: -x[1]))
            print(f"{r['id']:<16} {r['fired']:>6} {r['rate']:>6.0%}   {keys}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


