"""把一段消息切成 200~400 字的记忆条，硬上限 500。

先合成每条消息的最终文本（含动作、台词），再按整条压入；
被拒的动作不记，但同一条消息里的台词与正文照记。
"""

import json
import re

TARGET = 300
HARD = 500
KEEP_CALLS = ("active", "line")
_SPLIT = re.compile(r"(?<=[。！？…\n])")


def _speaker(role, row=None, names=None):
    names = names or {}
    if role == "user":
        return str((row or {}).get("name") or names.get("user") or "对方")
    if role == "assistant":
        return str(names.get("char") or "她")
    return ""


def _rejected(receipts, call_id):
    text = str(receipts.get(call_id) or "")
    compact = text.replace(" ", "")
    return '"ok":false' in compact or "未执行" in text or "未受理" in text


def _call_text(call, receipts):
    fn = call.get("function") or {}
    name = fn.get("name")
    if name not in KEEP_CALLS:
        return ""
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        return ""
    if name == "active":
        if _rejected(receipts, call.get("id")):
            return ""
        desc = (args.get("short") or args.get("long") or args.get("event")
                or args.get("description") or "").strip()
        return f"动作：{desc}" if desc else ""
    text = str(args.get("text") or "").strip()
    return f"台词：{text}" if text else ""


def _blocks(rows, names=None):
    """[(下标, 最终文本)]，只取 user / assistant。"""
    receipts = {r.get("tool_call_id"): r.get("content")
                for r in rows if r.get("role") == "tool"}
    out = []
    for i, row in enumerate(rows):
        if row.get("role") not in ("user", "assistant"):
            continue
        parts = []
        text = str(row.get("content") or "").strip()
        if text:
            who = _speaker(row.get("role"), row, names)
            parts.append(f"{who}：{text}" if who else text)
        for call in row.get("tool_calls") or []:
            piece = _call_text(call, receipts)
            if piece:
                parts.append(piece)
        if parts:
            out.append((i, "\n".join(parts)))
    return out


def _split_long(text, hard=HARD):
    pieces, buf = [], ""
    for part in _SPLIT.split(text):
        if not part:
            continue
        if len(buf) + len(part) <= hard:
            buf += part
            continue
        if buf:
            pieces.append(buf)
        while len(part) > hard:
            pieces.append(part[:hard])
            part = part[hard:]
        buf = part
    if buf:
        pieces.append(buf)
    return pieces


def slice_segment(rows, seg_index=0, target=TARGET, hard=HARD, names=None):
    out, buf, size, first = [], [], 0, None

    def flush(to_index):
        nonlocal buf, size, first
        if not buf:
            return
        row = rows[first]
        out.append({
            "text": "\n".join(buf),
            "seg": seg_index,
            "from": first,
            "to": to_index,
            "ts": row.get("ts"),
            "local": row.get("local") or "",
            "actions": row.get("actions") or [],
        })
        buf, size, first = [], 0, None

    for i, text in _blocks(rows, names):
        if buf and size + len(text) > hard:
            flush(i)
        if first is None:
            first = i
        buf.append(text)
        size += len(text)
        if size >= target:
            flush(i + 1)

    flush(len(rows))

    final = []
    for item in out:
        if len(item["text"]) <= hard:
            final.append(item)
        else:
            for piece in _split_long(item["text"], hard):
                final.append(dict(item, text=piece))
    for n, item in enumerate(final):
        item["id"] = f"slice-{seg_index:03d}-{n:03d}"
    return final


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        print("用法: python engine/slice.py <归档 session.jsonl>")
        raise SystemExit(1)
    rows = [json.loads(l) for l in
            open(path, encoding="utf-8").read().splitlines() if l.strip()]
    for item in slice_segment(rows):
        print(item["id"], len(item["text"]), item["text"][:60].replace("\n", " / "))
