"""模型 IO：请求、用量记录、payload 落盘。"""

import json
import time
from pathlib import Path

import core


def usage_numbers(usage):
    """从服务端返回的 usage 里取实数，不做任何估算。

    chat 接口：prompt_tokens / completion_tokens / total_tokens
              prompt_cache_hit_tokens / prompt_cache_miss_tokens
    兼容 responses 接口的 input_tokens / output_tokens / input_tokens_details。
    """
    usage = usage or {}
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
               or (usage.get("input_tokens_details") or {}).get("cached_tokens"))
    hit = int(hit or 0)
    prompt = int(prompt or 0)
    completion = int(completion or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "cache_hit": hit,
        "cache_miss": int(usage.get("prompt_cache_miss_tokens") or max(prompt - hit, 0)),
        "cache_rate": round(100 * hit / prompt, 2) if prompt else 0.0,
    }


def record_usage(usage, *, turn_id="", round_no=0, speaker=""):
    """追加一条 usage 记录到 state/usage/YYYY-MM-DD.jsonl，返回这条记录。"""
    global LAST_PROMPT_TOKENS
    n = usage_numbers(usage)
    if n["prompt"]:
        LAST_PROMPT_TOKENS = n["prompt"]
    rec = {
        "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "turn": turn_id,
        "round": round_no,
        "model": core.payload.get("model", ""),
        "speaker": speaker,
        **n,
    }
    try:
        core.USAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = core.USAGE_DIR / (__import__("time").strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        core.ERROR.append(f"[WARN] usage 落盘失败：{e}")
    return rec


def dump_payload(assembled, turn_id, round_no):
    """发送前落一份快照，用来核对上下文组装是否正确。

    debug.dump     → 完整 core.payload
    debug.dumpTail → 只展开易变的部分（检索块 + 本轮消息），system 与 session 只留摘要
    返回快照路径；两个开关都关时返回 None。
    """
    if not (DEBUG_DUMP or DEBUG_DUMP_TAIL):
        return None
    messages = assembled.get("messages") or []

    if DEBUG_DUMP and not DEBUG_DUMP_TAIL:
        data = dict(assembled)
    else:
        last = len(messages) - 1
        rows = []
        for i, m in enumerate(messages):
            content = m.get("content") or ""
            expand = i == last or (m.get("name") in BLOCK_NAMES)
            row = {"i": i, "role": m.get("role", "")}
            if m.get("name"):
                row["name"] = m["name"]
            if m.get("tool_calls"):
                row["tool_calls"] = [c.get("function", {}).get("name") for c in m["tool_calls"]]
            if m.get("tool_call_id"):
                row["tool_call_id"] = m["tool_call_id"]
            row["chars"] = len(content)
            if expand:
                row["content"] = content
            else:
                row["head"] = content[:80].replace("\n", " ")
            rows.append(row)
        data = {
            "turn": turn_id,
            "round": round_no,
            "model": assembled.get("model"),
            "tools": [t.get("function", {}).get("name") for t in assembled.get("tools") or []],
            "total_messages": len(messages),
            "total_chars": sum(len(m.get("content") or "") for m in messages),
            "messages": rows,
        }

    core.DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = core.DUMP_DIR / f"{turn_id}-r{round_no}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def send(body, timeout=(15, 600)):
    """发一轮请求，返回解析后的 JSON；失败记 error 并返回 None。"""
    import requests
    import urllib3
    urllib3.disable_warnings()
    try:
        r = requests.post(
            core.chat_completions_url,
            headers={"Authorization": f"Bearer {core.API_KEY}", "Content-Type": "application/json"},
            json=body, timeout=timeout, verify=core.VERIFY_SSL,
            proxies={"http": None, "https": None},
        )
    except Exception as e:                       # noqa: BLE001
        core.ERROR.append(f"[core.ERROR] 请求失败：{type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        core.ERROR.append(f"[core.ERROR] HTTP {r.status_code}: {r.text[:300]}")
        return None
    return r.json()
