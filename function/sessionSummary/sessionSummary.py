"""会话压缩：把旧轮次归档、切片入库，session 只留「提要 + 最近 N 轮」。

压缩和切片是同一次读写：旧轮次进 history 归档的同时进记忆库，
否则被压缩掉的内容就再也检索不到了。

调用方式（工具协议）：stdin 收 {"content": "模型写的会话摘要"}，stdout 打 JSON。
命令行调试：python sessionSummary.py --dry-run <<< '{"content": "摘要"}'
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "state" / "Philosophical.json"
SESSION = ROOT / "state" / "session" / "session.jsonl"
HISTORY_DIR = ROOT / "state" / "session" / "history"

SUMMARY_PREFIX = "[INFO] session 历史提要："
MAX_CHUNK_CHARS = 600
MIN_CHUNK_CHARS = 20


def text_of(content):
    """content 可能是字符串或内容块数组，取出纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(
            f"[Error] {what}不存在：{path}。不得编造内容，不得假装读取成功。")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"[Error] {what}解析失败：{path}：{e}") from e


def load_session(path):
    path = Path(path)
    if not path.exists():
        return []
    messages = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            messages.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return messages


def group_by_user(messages):
    """按 user 消息切轮：每组从一条 user 开始，到下一个 user 之前。"""
    groups, current = [], []
    for msg in messages:
        if msg.get("role") == "user" and current:
            groups.append(current)
            current = []
        current.append(msg)
    if current:
        groups.append(current)
    return groups


def chunk_group(group, agent_name, user_name):
    """一组消息 → 若干段可入库的切片，按行拼到上限为止。"""
    lines = []
    for msg in group:
        role, text = msg.get("role"), (text_of(msg.get("content")) or "").strip()
        if role == "user":
            lines += [f"{user_name}：{l.strip()}" for l in text.splitlines() if l.strip()]
        elif role == "assistant":
            if text:
                lines.append(f"{agent_name}：{text}")
            elif msg.get("tool_calls"):
                names = [c.get("function", {}).get("name", "") for c in msg["tool_calls"]]
                lines.append(f"（调用工具：{'、'.join(n for n in names if n)}）")
        elif role == "tool":
            brief = text.replace("\n", " ")[:40]
            if brief:
                lines.append(f"（工具返回：{brief}）")

    chunks, buf = [], ""
    for line in lines:
        if buf and len(buf) + len(line) + 1 > MAX_CHUNK_CHARS:
            chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def write_atomic(path, lines):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, path)


def compress(summary, *, cfg_path=CFG, session_path=SESSION, history_dir=HISTORY_DIR,
             agent_name=None, user_name=None, dry_run=False):
    """执行一次压缩，返回统计。dry_run 时只计算，不写 session、不写历史、不入库。"""
    cfg = load_json(cfg_path, "配置文件")
    session_cfg = (cfg.get("harness") or {}).get("session") or {}
    payload = cfg.get("payload") or {}
    retained_n = max(1, int(session_cfg.get("retainedTrun", 8)))

    agent_name = agent_name or (cfg.get("harness") or {}).get("name") or "我"
    user_name = user_name or payload.get("user_id") or "对方"

    messages = load_session(session_path)
    # 上一轮压缩留下的提要：丢掉，本次用新提要替换
    if messages and messages[0].get("role") == "system" \
            and text_of(messages[0].get("content")).startswith(SUMMARY_PREFIX):
        messages = messages[1:]

    groups = group_by_user(messages)
    if len(groups) <= retained_n:
        return {"ok": True, "archived": 0, "slices": 0, "retained": len(groups),
                "note": "轮次未超过保留数，未压缩"}

    retained = groups[-retained_n:]
    archived = groups[:-retained_n]
    archived_messages = [m for g in archived for m in g]

    # 1) 切片：归档内容同一遍扫下来
    texts, seen = [], set()
    for group in archived:
        for chunk in chunk_group(group, agent_name, user_name):
            digest = hashlib.sha1(chunk.strip().encode("utf-8")).hexdigest()
            if digest not in seen:
                seen.add(digest)
                texts.append(chunk)

    added = 0
    if texts and not dry_run:
        sys.path.insert(0, str(ROOT / "model"))
        import memory_store
        existing = {hashlib.sha1(it["text"].strip().encode("utf-8")).hexdigest()
                    for it in memory_store.lib().items if it.get("kind") == "slice"}
        fresh = [t for t in texts
                 if hashlib.sha1(t.strip().encode("utf-8")).hexdigest() not in existing]
        if fresh:
            added = len(memory_store.add(fresh, kind="slice"))

    # 2) 归档原文
    month = time.strftime("%Y-%m")
    history_path = Path(history_dir) / f"{month}.jsonl"
    if not dry_run:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "a", encoding="utf-8") as f:
            for m in archived_messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # 3) 重写 session：提要 + 保留轮次
    head = {"role": "system", "content": f"{SUMMARY_PREFIX}{summary}"}
    remaining = retained[0] + [m for g in retained[1:] for m in g]
    if not dry_run:
        lines = [json.dumps(head, ensure_ascii=False)]
        lines += [json.dumps(m, ensure_ascii=False) for m in remaining]
        write_atomic(session_path, lines)

    return {
        "ok": True,
        "archived": len(archived),
        "archived_messages": len(archived_messages),
        "slices": added if not dry_run else len(texts),
        "retained": len(retained),
        "history": str(history_path),
        "dry_run": dry_run,
    }


def main():
    raw = sys.stdin.read().strip()
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"[ERROR] 参数解析失败：{e}") from e
    if not isinstance(args, dict):
        raise ValueError("[ERROR] 参数应为 JSON 对象。")
    summary = str(args.get("content") or "").strip()
    if not summary:
        raise ValueError("[ERROR] 缺少 content：需要一段会话摘要。")

    result = compress(summary, dry_run="--dry-run" in sys.argv)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
