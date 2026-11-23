"""按固定字段切段：只读归档 session，不碰正在用的。

边界：
  1 跨天        ts 的日期变了
  2 换地点      local 变了
  3 睡眠        sleep 从 false 变 true，或从 true 变 false
  4 新事件      actions 里出现新的 event 项
  5 字符        累计超过 limit，整条消息入/丢

返回 [{"from", "to", "reason", "from_ts", "to_ts"}]，to 不含。
"""

import json
from pathlib import Path


def _date(ts):
    return str(ts or "")[:10]


def _events(row):
    out = set()
    for item in row.get("actions") or []:
        text = str(item or "")
        if text.startswith("event:"):
            out.add(text)
    return out


def segment(rows, limit=6000):
    if not rows:
        return []
    segs, start, chars = [], 0, 0
    prev = None

    def close(end, reason):
        if end > start:
            segs.append({
                "from": start, "to": end, "reason": reason,
                "from_ts": rows[start].get("ts"),
                "to_ts": rows[end - 1].get("ts"),
            })

    for i, row in enumerate(rows):
        reason = ""
        if prev is not None:
            if _date(row.get("ts")) != _date(prev.get("ts")):
                reason = "day"
            elif row.get("local") and prev.get("local") and row["local"] != prev["local"]:
                reason = "local"
            elif bool(row.get("sleep")) != bool(prev.get("sleep")):
                reason = "sleep"
            elif _events(row) - _events(prev):
                reason = "event"
        size = len(str(row.get("content") or ""))
        if reason:
            close(i, reason)
            start, chars = i, size
        elif chars + size > limit and i > start:
            close(i, "chars")
            start, chars = i, size
        else:
            chars += size
        prev = row
    close(len(rows), "end")
    return segs


def archived_segments(path, limit=6000):
    rows = [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return segment(rows, limit=limit)


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else ""
    if not src:
        print("用法: python engine/segment.py <归档 session.jsonl>")
        raise SystemExit(1)
    for seg in archived_segments(src):
        print(json.dumps(seg, ensure_ascii=False))
