"""检索层：记忆、世界书、技艺的召回与渲染。"""

import os
import re


import core

LOREBOOK_MAX_ITEMS = core._num("retrieval.lorebook.maxItems", 12)
LOREBOOK_BUDGET_CHARS = core._num("retrieval.lorebook.budgetChars", 2000)
_ROLE_TAG = re.compile(r"<(?:user|assistant|system)><([^>]*)>\s*")
_BARE_TAG = re.compile(r"<[a-z_]+>\s*")


def render_transcript(text):
    """把切片的原始转写改写成干净文本。

    切片里带着 `{...}` 外壳和 `<user><名字>` / `<assistant><名字>` 标记。
    这些尖括号标记与本项目的分区标签同形，混进上下文会让模型误以为
    是 session 里的对话，所以在注入前全部抹掉，改成人名加冒号。
    """
    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    text = _ROLE_TAG.sub(lambda m: (m.group(1) + "：") if m.group(1) else "", text)
    text = _BARE_TAG.sub("", text)
    return text.strip()


def memory_recall(search_text, record=True):
    """返回 <memory_momentary> 块，没有命中返回空串。

    切片与记忆块**分别检索**：混在一起全局排序时，分数低的块会被切片挤掉，
    而块恰恰是最精确的答案（短、密度高）。
    """
    if core.memory_search is None or not search_text:
        return ""

    try:
        slices = core.memory_search(search_text, top_k=core.MEMORY_TOP_K, kind="slice",
                               min_score=core.SLICE_FLOOR, record=False)
        blocks = core.memory_search(search_text, top_k=core.BLOCK_TOP_K, kind="block",
                               min_score=None, record=False)
    except Exception as e:                   # noqa: BLE001
        core.ERROR.append(f"[core.ERROR] 记忆检索失败：{type(e).__name__}: {e}")
        return ""

    blocks = [b for b in blocks if b["score"] >= core.BLOCK_FLOOR]
    if slices:
        # 主题集中时多收几条；主题稀薄时只留最相关的
        best = max(h["score"] for h in slices)
        floor = max(core.SLICE_FLOOR, best - core.SLICE_MARGIN)
        slices = [h for h in slices if h["score"] >= floor]

    kept = blocks + slices
    if not kept:
        return ""
    kept.sort(key=lambda h: (h["kind"] != "block", -h["score"]))
    kept = kept[:core.MEMORY_TOP_K + core.BLOCK_TOP_K]

    if record and core.memory_lib is not None and os.environ.get("PZ_TEST") != "1":
        try:
            core.memory_lib().record([{"id": h["id"], "chars": h["chars"]} for h in kept])
        except Exception as e:               # noqa: BLE001
            core.ERROR.append(f"[WARN] 命中计数失败：{type(e).__name__}: {e}")

    core.log("memory_recall", query_chars=len(search_text or ""), hits=len(kept))

    blocks_kept = [h for h in kept if h["kind"] == "block"]
    slices_kept = [h for h in kept if h["kind"] == "slice"]

    parts = []
    if blocks_kept:
        parts.append("## 我记得\n" + "\n".join(f"- {h['text'].strip()}" for h in blocks_kept))
    if slices_kept:
        parts.append("## 当时的原话\n" + "\n\n".join(render_transcript(h["text"]) for h in slices_kept))
    body = "\n\n".join(parts)
    return f"<memory_momentary>\n{body}\n</memory_momentary>"


def lorebook_recall(search_text):
    """返回 <lorebook_momentary> 块，没有命中返回空串。"""
    if core.lorebook_lookup is None or not search_text:
        return ""
    try:
        core.log("lorebook_recall", query_chars=len(search_text or ""))
        hits = core.lorebook_lookup(search_text, max_items=LOREBOOK_MAX_ITEMS,
                               budget_chars=LOREBOOK_BUDGET_CHARS)
    except Exception as e:                   # noqa: BLE001
        core.ERROR.append(f"[core.ERROR] 世界书检索失败：{type(e).__name__}: {e}")
        return ""
    if not hits:
        return ""
    # 按命中标签数排序（lookup 已排序，这里保持稳定），同一 tags 归到一组
    groups = {}
    for h in hits:
        key = (h.get("tags") or ["其他"])[0]
        groups.setdefault(key, []).append(h)
    sections = []
    for key, items in groups.items():
        lines = [f"## {h['title']}\n\n{h['text'].strip()}" for h in items]
        sections.append(f"# {key}\n\n" + "\n\n".join(lines))
    return "<lorebook_momentary>\n" + "\n\n".join(sections) + "\n</lorebook_momentary>"


def skill_recall(skills_data, search_text, limit=2):
    """非常驻技能：关键词命中即召回，命中越多越靠前；只按条数截断，不切字数。"""
    if not skills_data or not search_text:
        return ""
    low = search_text.lower()
    hits = []
    for skill in skills_data:
        if skill.get("constant"):
            continue
        keys = [k for k in (skill.get("keywords") or []) if isinstance(k, str) and len(k) >= 2]
        matched = [k for k in keys if k.lower() in low]
        if not matched:
            continue
        hits.append((len(matched), max(len(k) for k in matched),
                     int(skill.get("timestamp") or 0), skill))

    if not hits:
        return ""
    hits.sort(key=lambda item: (-item[0], -item[1], -item[2]))
    body = "\n\n".join(
        f"## {skill.get('title', '')}\n\n{core.increase_heading_level(str(skill.get('content') or ''))}"
        for _, _, _, skill in hits[:limit]
    )
    return f"<skill_knowledge>\n{body}\n</skill_knowledge>"
