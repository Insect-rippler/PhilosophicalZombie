"""本地服务：Qt 前端连这个，引擎不跟 UI 绑在一起。

只用标准库起 HTTP，不额外装依赖。端口默认 8765，只监听 127.0.0.1。

接口：
    POST /turn        {"text": "..."} → SSE 流式返回；不支持流时自动退回整段
    GET  /state       当前状态快照（时间、位置、生理、是否困）
    GET  /config      读配置（脱敏，api key 打码）
    POST /config      改配置（payload 下的常见项 + harness 数值）
    GET  /realm       列出 ConsciousnessRealm 下的 md
    GET  /realm/<名>  读文件原文
    PUT  /realm/<名>  写回文件原文
"""

import json
import queue
import re as _re
import paths as _paths
import time

try:
    from trace import log
except ImportError:
    def log(*a, **k):
        pass
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import body
import vclock
import core
import runlog
import modelio
import tools                      # noqa: E402
from engine import timers        # noqa: E402

PORT = 8765
TICK_SECONDS = 60          # 1 tick = 1 分钟
HOST = "127.0.0.1"
LOCK = threading.Lock()          # 一轮对话独占，避免两轮同时改 session
QUEUE_LOCK = threading.Lock()
QUEUED_TURNS = []                # 运行中收到的用户消息，等锁释放后按顺序处理
TURN_BUSY = False                # 模型请求/工具循环进行中


# ---------------- 对话 ----------------

TOOL_NARRATION = {
    "active": "她在做一件事",
}


def describe_tool(name, args, result):
    """工具调用 → (类型, 文本)。active 是旁白，其余是系统提示。"""
    if name == "active":
        try:
            data = json.loads(args) if isinstance(args, str) else {}
            desc = (data.get("short") or data.get("long") or data.get("event")
                    or data.get("description") or "")
        except json.JSONDecodeError:
            desc = ""
        return "narration", desc or "她动了一下"
    if name == "fight":
        return "tip", "战斗判定"
    if name == "remember":
        return "tip", "记下了一件事"
    if name == "plan":
        return "tip", "整理了目标"
    if name == "pause":
        return "tip", "暂停一拍"
    return "tip", name




INJECTED_TAGS = core.CONTEXT_TAGS


def strip_context_leak(text):
    """模型把自己的上下文块吐出来时，从第一个标签处截断。"""
    if not text:
        return text
    cut = min((text.find(tag) for tag in INJECTED_TAGS if tag in text), default=-1)
    if cut > 0:
        return text[:cut].rstrip()
    if cut == 0:
        return ""
    return text


PSEUDO_KEY = _re.compile(r"^(description|slot|short|long|event|seconds|end|scene|location|force)\s*[:=]", _re.I)


def strip_pseudo_call(text):
    """兜底失败时，把残留的伪调用清理掉，避免写进 session。"""
    if not text:
        return text
    out, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("<active") or s.startswith("<scene"):
            inside = ">" not in s[1:] or not s.endswith(">")
            if "</active>" in s or "</scene>" in s:
                inside = False
            continue
        if s.startswith("</active") or s.startswith("</scene"):
            inside = False
            continue
        if inside:
            continue
        if PSEUDO_KEY.match(s):
            continue
        out.append(line)
    return "\n".join(out).strip()


def tool_failed(output):
    """工具回执是不是拒绝/失败。这种不该渲染成动作。"""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("ok") is False:
        return True
    return bool(str(data.get("未执行") or "").strip())


def looks_narration(text):
    """没有工具调用，正文却像动作或参数：判定为通道泄漏。"""
    text = str(text or "")
    if not text.strip():
        return False
    if _re.search(r"(short|long|event|seconds)\s*[:=]", text):
        return True
    stripped = _re.sub(r"[「“\"][^」”\"]*[」”\"]", "", text)
    if _re.search(r"(^|[。！？\n]\s*)她", stripped):
        return True


def run_with_tools(payload, emit, stream_id=None):
    """跑一轮（含工具）。返回 (是否已经产出结果, 最终文本)。

    第一轮也走流式；一旦出现 tool_calls，就执行工具、把结果写回 session，
    重新组装后再来一轮，直到没有工具调用为止。
    """
    body = dict(payload)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}

    import requests
    import urllib3
    urllib3.disable_warnings()

    last_text = ""
    spoken_line = ""
    pending_text = ""
    last_reject = {}
    pause_called = False
    no_speech_retries = 0
    active_seen = False
    accepted_slot = ""

    streamed = False

    def on_delta(piece):
        nonlocal streamed
        streamed = True
        emit("delta", piece)

    def emit_pending():
        if not pending_text.strip() or streamed:
            return
        # 出口再兜一层：参数写成文字的，绝不进正文
        clean, _ = salvage_text_call(pending_text)
        emit("delta", strip_pseudo_call(clean))
    echo = EchoStrip()
    registry = tools.load_registry()        # 一轮只读一次

    run_with_tools.hit_cap = False
    rounds = max(1, int(core.LOOP_MAX))       # 上限统一读 harness.loop.maxRounds
    echo = EchoStrip()
    for _round in range(rounds):
        active_seen = False
        active_background = False
        line_final = False
        line_default = False
        other_wake = False                   # 每条 message 重新计数，不是每轮 loop 累计
        if _round == rounds - 1:              # 最后一轮关思考，免得只吐 reasoning
            body["thinking"] = {"type": "disabled"}

        reply = None
        _real_t0 = vclock.real_now()
        streamed = False
        for _attempt in range(3):
            reply = modelio.stream(body, on_delta=on_delta)
            if reply:
                break
            time.sleep(1.2 * (_attempt + 1))
        _real_elapsed = vclock.real_now() - _real_t0
        vclock.advance_seconds(_real_elapsed)
        core.record_latency(_real_elapsed)
        if not reply:
            log("request.failed", round=_round + 1)
            return False, ""

        message = ((reply.get("choices") or [{}])[0].get("message") or {})
        content = str(message.get("content") or "")
        calls = message.get("tool_calls") or []
        reasoning = str(message.get("reasoning_content") or "")
        reasoning_parts = [reasoning] if reasoning else []
        usage = reply.get("usage")

        # 通道泄漏：没有工具调用，正文却写成动作/参数 → 重新请求一次
        if not calls and looks_narration(content):
            nudge = {"role": "user", "name": "retry",
                     "content": "（上一条回复把动作写进了正文，没有走工具通道。"
                                "请重新回复：动作走 active，台词走 line，"
                                "正文只写她说出口的话。）"}
            retry_body = dict(body, messages=list(body["messages"]) + [nudge])
            retry_body["stream"] = False
            retry_body.pop("stream_options", None)
            again = modelio.send(retry_body)
            if again:
                msg2 = ((again.get("choices") or [{}])[0].get("message") or {})
                text2 = str(msg2.get("content") or "")
                calls2 = msg2.get("tool_calls") or []
                if calls2 or not looks_narration(text2):
                    message, content, calls = msg2, text2, calls2
                    reasoning = str(msg2.get("reasoning_content") or "")
                    reasoning_parts = [reasoning] if reasoning else []
                    usage = again.get("usage")
                    log("retry.narration", round=_round + 1, ok=bool(calls2))

        if usage:
            try:
                rec = modelio.record_usage(usage, round_no=_round + 1)
                emit("usage", {"round": _round + 1,
                               "prompt": rec["prompt"],
                               "completion": rec["completion"],
                               "cache": rec["cache_rate"],
                               "avg": average_cache(usage_window()),
                               "ratio": round(100 * rec["prompt"]
                                              / max(1, core.COMPRESSION_THRESHOLD), 2),
                               "window": usage_window()})
            except Exception:                # noqa: BLE001
                pass

        visible = echo.feed(content)
        buffered = [visible] if visible.strip() else []
        last_text = content
        echo.remember(last_text)
        pending_text = "".join(buffered)
        cleaned = strip_context_leak(pending_text)
        if cleaned != pending_text:
            log("leak.stripped", before=len(pending_text), after=len(cleaned))
            pending_text = cleaned

        if usage:
            try:
                rec = modelio.record_usage(usage, round_no=_round + 1)
                emit("usage", {"round": _round + 1,
                               "prompt": rec["prompt"],
                               "completion": rec["completion"],
                               "cache": rec["cache_rate"],
                               "avg": average_cache(usage_window()),
                               "ratio": round(100 * rec["prompt"]
                                              / max(1, core.COMPRESSION_THRESHOLD), 2),
                               "window": usage_window()})
            except Exception:                # noqa: BLE001
                pass

        last_text = content
        echo.remember(last_text)

        # 模型把 active 的参数写成了正文：把它当一次调用捡回来
        if not calls:
            cleaned_text, salvaged = salvage_text_call(pending_text)
            if salvaged:
                if not any(str(salvaged.get(k) or "").strip()
                           for k in ("short", "long", "event")):
                    legacy = str(salvaged.get("description") or "").strip()
                    if legacy:
                        salvaged["short"] = legacy
            if salvaged and any(str(salvaged.get(k) or "").strip()
                                for k in ("short", "long", "event")):
                pending_text = cleaned_text
                buffered = [cleaned_text]
                args = {k: v for k, v in salvaged.items()}
                args.pop("description", None)
                args.pop("slot", None)
                if args.get("force") in ("true", "True", "1"):
                    args["force"] = True
                if args.get("sleep") in ("true", "True", "1"):
                    args["sleep"] = True
                if not str(args.get("seconds") or "").strip():
                    args["seconds"] = "10"
                calls = [{"id": "salvaged-1", "type": "function",
                          "function": {"name": "active",
                                       "arguments": json.dumps(args, ensure_ascii=False)}}]
                log("tool.salvaged", keys=list(args))

        if not calls:
            emit_pending()
            if not pending_text.strip() and spoken_line:
                return True, ""
            run_with_tools.reasoning = "".join(reasoning_parts)
            clean_text, _ = salvage_text_call(pending_text)
            clean_text = strip_pseudo_call(clean_text)
            if clean_text.strip():
                row = {"role": "assistant", "content": clean_text}
                if reasoning_parts:
                    row["reasoning_content"] = "".join(reasoning_parts)
                core.append_session([row])
            return True, clean_text


        # 固定执行顺序：active(scene→动作) → line → 其它工具
        _order = {"active": 0, "line": 1}
        calls = [c for _, c in sorted(
            enumerate(calls),
            key=lambda pair: (_order.get(
                ((pair[1].get("function") or {}).get("name") or ""), 2), pair[0]))]

        assistant = {"role": "assistant",
                 "content": strip_pseudo_call(strip_context_leak(content))}
        if reasoning_parts:
            assistant["reasoning_content"] = "".join(reasoning_parts)
        assistant["tool_calls"] = calls
        results = []

        def record_tool(row):
            results.append(row)
            core.append_session([row])

        core.append_session([assistant])
        for call in calls:
            name = (call.get("function") or {}).get("name") or ""
            name, args = name, (call.get("function") or {}).get("arguments") or "{}"
            runlog.log("tool.begin", name=name,
                       chars=len(str((call.get("function") or {}).get("arguments") or "")))
            if name == "active":
                if active_seen:
                    record_tool({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": json.dumps(
                                     {"未执行": "未受理：一条 message 只受理第一个 active；这个已丢弃。",
                                      "ok": False}, ensure_ascii=False)})
                    continue
                active_seen = True

            output = tools.execute_tool_call(call, registry)
            runlog.log("tool.done", name=name, out=len(str(output)))
            if name == "pause":
                if tool_failed(output):
                    other_wake = True
            if name == "active" and tool_failed(output):
                try:
                    last_reject = {"args": json.loads(args), "call_id": call.get("id")}
                except (json.JSONDecodeError, TypeError):
                    pass
            # 被代码拒绝的动作不画，但「话」照说——她张嘴了，只是事情没做成
            if tool_failed(output):
                if name == "sleep":
                    other_wake = True
                try:
                    attempted = json.loads(args)
                except (TypeError, ValueError):
                    attempted = {}
                spoken = str(attempted.get("line") or "").strip()
                if spoken:
                    spoken_line = (spoken_line + spoken) if spoken_line else spoken
                    emit("tool", {"kind": "line", "name": name, "text": spoken})
                record_tool({"role": "tool", "tool_call_id": call.get("id", ""),
                                "content": output})
                continue

            scene = None
            if name == "active":
                try:
                    scene = json.loads(args).get("scene")
                except (json.JSONDecodeError, AttributeError):
                    scene = None
            if isinstance(scene, str) and scene.strip():      # 1 环境
                emit("tool", {"kind": "scene", "name": "scene", "text": scene.strip()})
            kind, label = describe_tool(name, args, output)
            if name == "active":
                emit("tool", {"kind": "action", "name": name, "text": label})   # 2 动作
            elif name not in ("line", "pause"):
                emit("tool", {"kind": kind, "name": name, "text": label})
            if name == "line":
                try:
                    la = json.loads(args) if isinstance(args, str) else {}
                except json.JSONDecodeError:
                    la = {}
                said2 = str(la.get("text") or "").strip()
                if said2:
                    spoken_line = (spoken_line + said2) if spoken_line else said2
                    emit("tool", {"kind": "line", "name": "line", "text": said2})
                    if la.get("final") is True or str(la.get("final")).lower() in ("true", "1"):
                        line_final = True
                    else:
                        line_default = True
                else:
                    line_default = True
            if name == "active":
                if not tool_failed(output):
                    try:
                        _a = json.loads(args) if isinstance(args, str) else {}
                    except json.JSONDecodeError:
                        _a = {}
                    accepted_slot = next((k for k in ("short", "long", "event")
                                          if str(_a.get(k) or "").strip()), "")
                    active_background = (accepted_slot == "short")
                    if accepted_slot != "short":
                        other_wake = True
                else:
                    other_wake = True
            if name not in ("active", "line", "pause", "sleep"):
                other_wake = True
            record_tool({"role": "tool", "tool_call_id": call.get("id", ""),
                            "content": output})

        # 工具执行后立即推送视图：short 的计时器要马上出现在前端，
        # 不能等本轮结束才从“静默”切到动作倒计时。
        publish_view()
        emit_pending()                     # 先动作，后台词
        # 用户对话 loop 里，只有动作、没有说出口的话，不算回应。
        # 最多补问两次，避免模型把答案永远留在 reasoning 里。
        if (core.USER_LOOP and not spoken_line and not pending_text.strip()
                and no_speech_retries < 2):
            no_speech_retries += 1
            log("reply.missing_speech", round=_round + 1)
            payload = core.context_assemble(record=False)
            body = dict(payload)
            body["stream"] = False
            body.pop("stream_options", None)
            body["messages"] = list(body.get("messages") or []) + [{
                "role": "user", "name": "retry",
                "content": "（对方最后一条还没有得到回应。先正面回应他这句话；"
                           "把说出口的话写在正文或 line 里，不要只放在思考里，"
                           "也不要只做一个动作。）"
            }]
            continue
        # 每个工具各自决定“这一拍之后还要不要下一次唤醒”：
        # active(short)=否，line(final)=否，pause=否；其它工具=是。
        # 这些标记按 OR 合并：只要有一个工具要下一次唤醒，就继续；
        # 全部都不需要，才结束本轮。
        if not (line_default or other_wake):
            break
        payload = core.context_assemble(record=False)     # 工具轮不重复记召回
        body = dict(payload)
        body["stream"] = False
        body.pop("stream_options", None)


    else:
        run_with_tools.hit_cap = True

    if not last_text and not spoken_line and not calls:   # 真的什么都没产出，才补救
        log("fallback.plain", round=rounds)
        try:
            plain = dict(body)
            plain["stream"] = False
            plain["tools"] = []
            plain["tool_choice"] = "none"
            plain["thinking"] = {"type": "disabled"}
            plain.pop("stream_options", None)
            plain["messages"] = [
                m for m in plain.get("messages") or []
                if not (m.get("role") == "assistant" and m.get("tool_calls"))
            ] + [{"role": "system",
                  "content": "只输出角色这一轮说出口的话。不要旁白、不要括号、不要解释。"}]
            response = modelio.send(plain)
            if response:
                text = ((response.get("choices") or [{}])[0]
                        .get("message") or {}).get("content") or ""
                if text:
                    emit("delta", text)
                    return True, text
        except Exception:                    # noqa: BLE001
            pass
    return True, pending_text


# ---------------- 状态 / 配置 / 文件 ----------------

def session_history(limit=40, with_total=False):
    """回读 session，给前端重建对话。工具调用折成旁白或提示行。"""
    if not core.SESSION_PATH.exists():
        return []
    rows = []
    raw_items = []
    for raw in core.SESSION_PATH.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        raw_items.append(item)

    results = {it.get("tool_call_id"): str(it.get("content") or "")
               for it in raw_items if it.get("role") == "tool"}

    for item in raw_items:
        role = item.get("role")
        if role == "system":
            continue
        content = item.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content
                              if isinstance(p, dict) and p.get("type") == "text")
        text = str(content or "").strip()
        if role == "tool":
            continue
        if role == "assistant" and item.get("tool_calls"):
            for call in item["tool_calls"]:
                name = (call.get("function") or {}).get("name") or ""
                if name == "active":
                    try:
                        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    receipt = results.get(call.get("id") or "", "")
                    if "未执行" in receipt or '"ok": false' in receipt:
                        continue
                    scene = str(args.get("scene") or "").strip()
                    desc = next((str(args.get(k) or "").strip()
                                 for k in ("short", "long", "event")
                                 if str(args.get(k) or "").strip()), "")
                    said = ""
                    if scene:
                        rows.append({"role": "scene", "text": scene,
                                     "ts": item.get("ts") or ""})
                    if desc:
                        rows.append({"role": "narration", "text": desc,
                                     "ts": item.get("ts") or ""})
                    if said:
                        rows.append({"role": "line", "text": said,
                                     "ts": item.get("ts") or ""})
                elif name == "line":
                    try:
                        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    said = str(args.get("text") or "").strip()
                    if said:
                        rows.append({"role": "line", "text": said,
                                     "ts": item.get("ts") or ""})
                elif name == "pause":
                    continue
                elif name:
                    rows.append({"role": "tip", "text": name})
            if not text:
                continue
        if not text:
            continue
        rows.append({"role": role, "name": item.get("name") or "",
                     "text": text, "ts": item.get("ts") or ""})
    if with_total:
        return rows[-limit:], len(rows)
    return rows[-limit:]


def usage_window():
    """平均命中的统计窗口，读 harness.usage.avgWindow，限制在 50~300。"""
    usage_cfg = core.harness.get("usage") or {}
    try:
        value = int(usage_cfg.get("avgWindow") or usage_cfg.get("maxTurn") or 100)
    except (TypeError, ValueError):
        value = 100
    return max(50, min(300, value))


def average_cache(window):
    """最近 window 条请求的平均缓存命中率。"""
    files = sorted((_paths.data("usage")).glob("*.jsonl"))
    rows = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows = rows[-window:]
    if not rows:
        return 0.0
    return round(sum(float(r.get("cache_rate") or 0) for r in rows) / len(rows), 1)


def last_usage():
    """最近一条 usage 记录 + 平均命中，供界面启动时显示。"""
    files = sorted((_paths.data("usage")).glob("*.jsonl"))
    row = None
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
    if not row:
        return {}
    return {"prompt": row.get("prompt", 0), "completion": row.get("completion", 0),
            "cache": row.get("cache_rate", 0), "avg": average_cache(usage_window()),
            "ratio": round(100 * float(row.get("prompt", 0))
                           / max(1, core.COMPRESSION_THRESHOLD), 2)}


# ---------------- 脏输出兜底 ----------------

import re as _re

TAG_ACTION = _re.compile(r"<\s*(active|scene)([^>]*)>", _re.S)
ATTR_DESC = _re.compile(r'description\s*=\s*["\']([^"\']*)["\']', _re.S)
DSML_CALL = _re.compile(r'invoke\s+name\s*=\s*["\'](active|scene)["\']', _re.S)
DSML_PARAM = _re.compile(r'parameter\s+name\s*=\s*["\']description["\'][^>]*>([^<]*)', _re.S)
NOISE = _re.compile(r"<[^>]*DSML[^>]*>|</\s*(active|scene)\s*>|<\s*\|\|?[^>]*>", _re.S)


# 模型有时不调用工具，而是把参数写成正文：
#   description="她推开门" slot="short" line="进来吧。"
# 这里把它们捡回来，当成一次真正的 active 调用。
TEXT_CALL = _re.compile(
    r'(description|slot|short|long|event|seconds|end|scene|force|sleep|location)\s*=\s*'
    r'(?:"([^"\n]*)"|\'([^\'\n]*)\')', _re.S)


SLOT_LINE = _re.compile(r"^\s*(short|long|event)\s*$", _re.M)
COLON_CALL = _re.compile(
    r'^\s*(description|slot|short|long|event|seconds|end|scene|location)\s*[:：]\s*["“]?(.+?)["”]?\s*$', _re.M)
TAG_CALL = _re.compile(r"<\s*active\b([^>]*)>", _re.S)
ATTR = _re.compile(r'(\w+)\s*=\s*["“]([^"”]*)["”]')


def salvage_text_call(text):
    """从正文里捡出被写成文字的 active 参数。

    支持三种写法：
        key="value" 或 key='value'
        key: value（一行一个）
        <active description="…" slot="short" …>
    返回 (清理后的正文, 参数字典或 None)。至少要有 description 才认。
    """
    if not text:
        return text, None
    found = {}
    for key, dq, sq in TEXT_CALL.findall(text):
        found[key] = dq or sq

    if not found:
        for match in TAG_CALL.finditer(text):
            for key, value in ATTR.findall(match.group(1)):
                found[key] = value
        if found:
            text = TAG_CALL.sub("", text)

    if not found:
        for match in COLON_CALL.finditer(text):
            found[match.group(1)] = match.group(2).strip().strip("\"'“”")
        if found:
            text = COLON_CALL.sub("", text)

    if not found:
        return text, None

    text = SLOT_LINE.sub("", text)
    text = _re.sub(r"<\s*/?\s*(active|scene)[^>]*>", "", text)
    cleaned = TEXT_CALL.sub("", text)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    cleaned = "\n".join(l for l in cleaned.splitlines() if l.strip())
    return cleaned.strip(), found


class Salvager:
    """把模型写成文本的工具调用捡回来，剩下的标记丢掉。

    逐段喂 delta：正常文本立即返回，疑似标签先扣住，闭合后再判定。
    返回 (可展示文本, 事件列表)。
    """

    def __init__(self):
        self.hold = ""

    def feed(self, piece):
        text, events = "", []
        self.hold += piece
        while self.hold:
            start = self.hold.find("<")
            if start < 0:
                text += self.hold
                self.hold = ""
                break
            text += self.hold[:start]
            rest = self.hold[start:]
            end = rest.find(">")
            if end < 0:
                if len(rest) > 400:            # 不像标签，当普通文字放出去
                    text += rest
                    self.hold = ""
                else:
                    self.hold = rest
                break
            chunk = rest[:end + 1]
            self.hold = rest[end + 1:]
            events.extend(self._inspect(chunk, text_holder := []))
            # _inspect 只处理标签本身；description 可能在后面的正文里

        # 兜底：DSML 形式的调用。
        # 命中的片段要立刻从 hold 里切掉，否则下一次 feed 会把同一段再发一遍。
        for match in list(DSML_CALL.finditer(self.hold)):
            events.append({"kind": "narration", "name": match.group(1), "text": ""})
        if events:
            last = max((m.end() for m in DSML_CALL.finditer(self.hold)), default=0)
            self.hold = self.hold[last:]
        return text, events

    def _inspect(self, chunk, _unused):
        found = []
        tag = TAG_ACTION.match(chunk)
        if tag:
            name = tag.group(1)
            attrs = tag.group(2) or ""
            desc = ATTR_DESC.search(attrs)
            if not desc:
                # 只有 <active> 外壳、参数写在后面的行里：
                # 不发旁白，留给 salvage_text_call 当真实调用处理
                return found
            found.append({"kind": "narration", "name": name,
                          "text": desc.group(1)})
            return found
        return found

    def flush(self):
        """回合结束，把还扣着的东西处理掉。"""
        rest, self.hold = self.hold, ""
        text, events = "", []
        if not rest:
            return text, events
        for match in DSML_CALL.finditer(rest):
            desc = DSML_PARAM.search(rest)
            if not desc:
                continue
            events.append({"kind": "narration", "name": match.group(1),
                           "text": desc.group(1).strip()})
        text = NOISE.sub("", rest)
        text = TAG_ACTION.sub("", text).strip()
        if events and text:                    # 描述常跟在标签后面
            events[-1]["text"] = (events[-1]["text"] + " " + text).strip()
            text = ""
        return text, events


class EchoStrip:
    """模型有时把上一轮的台词当前缀重写一遍；把它剥掉再输出。"""

    def __init__(self):
        self.prev = ""
        self.buf = ""

    def feed(self, piece):
        self.buf += piece
        if self.prev:
            if len(self.buf) <= len(self.prev) and self.prev.startswith(self.buf):
                return ""                      # 还在复述上一轮，压住不发
            if self.buf.startswith(self.prev):
                rest, self.buf, self.prev = self.buf[len(self.prev):], "", ""
                return rest
        out, self.buf, self.prev = self.buf, "", ""
        return out

    def close(self):
        """一轮结束：返回结尾残留，并把这一轮的话记为上一轮。"""
        rest, self.buf = self.buf, ""
        return rest

    def remember(self, text):
        self.prev = (text or "").strip()

def skill_text():
    """把 skill.json 渲染成可读文本。"""
    target = core.REALM_ROOT / "skill" / "skill.json"
    if not target.exists():
        return {"text": "", "count": 0}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"text": "", "count": 0}

    lines = []
    items = data.get("skills") if isinstance(data, dict) else data
    for index, item in enumerate(items or [], 1):
        if not isinstance(item, dict):
            continue
        title = item.get("title") or f"技能 {index}"
        lines.append(f"## {title}")
        keywords = item.get("keywords") or []
        if keywords:
            lines.append("关键词：" + "、".join(str(k) for k in keywords))
        for key, value in item.items():
            if key in ("title", "keywords"):
                continue
            if isinstance(value, list):
                lines.append(f"- {key}：")
                for entry in value:
                    lines.append(f"  - {entry}")
            else:
                lines.append(f"- {key}：{value}")
        lines.append("")
    return {"text": "\n".join(lines), "count": len(items or []),
            "items": items or []}


def idle_settings():
    try:
        cfg = core.loadConfig() or {}
    except Exception:                        # noqa: BLE001
        cfg = {}
    node = ((cfg.get("harness") or {}).get("idle")) or {}
    try:
        minutes = float(node.get("afterMinutes", 30))       # 以 tick 计，即分钟
    except (TypeError, ValueError):
        minutes = 30.0
    return bool(node.get("enabled", True)), max(1.0, minutes)


def ensure_activity_mark():
    """events.json 里没有活动时间时补一个，否则静默唤醒永远不触发。"""
    path = _paths.data("realTime", "events.json")
    try:
        marks = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        marks = {}
    if marks.get("lastMessageAt"):
        return
    marks["lastMessageAt"] = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marks, ensure_ascii=False), encoding="utf-8")
        print("[boot] 活动时间已初始化", flush=True)
    except OSError:
        pass


def last_activity_at():
    try:
        marks = json.loads((_paths.data("realTime", "events.json"))
                           .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    return float(marks.get("lastMessageAt") or 0)


def sleeping():
    """角色是否正睡着。睡着时静默不计时，其它触发照常。"""
    ch = timers.load_character()
    return (ch.get("state") or {}).get("active") == "sleep"


def should_wake(now=None):
    """醒不醒：睡着时只由生理/闹钟醒，动作回执和静默都不叫醒。"""
    import time as _t
    now = _t.time() if now is None else float(now)
    if sleeping():
        return False
    if timers.peek_pending():                 # 醒着：计时器到期 / 事件触发
        return True
    enabled, minutes = idle_settings()
    last = last_activity_at()
    quiet = bool(enabled and last and now - last >= minutes * TICK_SECONDS)
    return quiet and not sleeping()           # 静默到期且醒着


_MAINTAIN_LOCK = threading.Lock()


def kick_maintenance():
    """睡够了就把维护丢到后台跑：压缩 → 升格 → 写回。"""
    if not _MAINTAIN_LOCK.acquire(blocking=False):
        return False
    try:
        import maintain
    except Exception:                      # noqa: BLE001
        _MAINTAIN_LOCK.release()
        return False
    if not maintain.pending_mark():
        _MAINTAIN_LOCK.release()
        return False

    def job():
        try:
            # 维护会整体重写 session，必须和 /turn 排队，不能并行
            with LOCK:
                result = maintain.run()
                try:
                    import backup
                    backup.snapshot("maintain")
                except Exception:            # noqa: BLE001
                    pass
            try:
                _drain_queued_turns()
            except Exception:                # noqa: BLE001
                pass
            log("maintain.done", ok=result.get("ok"), reason=str(result.get("reason") or "")[:60])
        except Exception as exc:           # noqa: BLE001
            log("maintain.failed", error=f"{type(exc).__name__}: {exc}"[:120])
        finally:
            _MAINTAIN_LOCK.release()

    threading.Thread(target=job, daemon=True).start()
    return True


# ---------------- 手动任务 ----------------

JOBS = {}                      # name -> {running, started, result}
JOBS_LOCK = threading.Lock()

MANUAL = {
    "backup": "备份一次",
    "reindex": "重算向量库",
    "repair": "修复会话",
    "maintain": "跑一次维护",
}


def _run_job(name):
    import time as _t
    import traceback

    started = _t.time()
    try:
        if name == "backup":
            import backup
            count = backup.snapshot("manual")
            try:
                backup.prune()
            except Exception:                # noqa: BLE001
                pass
            result = {"ok": True, "detail": f"{count} 个文件"}
        elif name == "reindex":
            import sys as _sys
            _sys.path.insert(0, str(ROOT / "model"))
            import memory_store
            store = memory_store.lib()
            got = store.sync_all()
            result = {"ok": True,
                      "detail": f"新增 {got.get('added', 0)}，更新 {got.get('updated', 0)}，"
                                f"删除 {got.get('removed', 0)}"}
        elif name == "repair":
            fixed = core.repair_session()
            result = {"ok": True, "detail": f"修了 {fixed} 行"}
        elif name == "maintain":
            import maintain
            with LOCK:
                result = maintain.run()
            try:
                _drain_queued_turns()
            except Exception:                # noqa: BLE001
                pass
        else:
            result = {"ok": False, "detail": "未知任务"}
    except Exception as exc:                 # noqa: BLE001
        traceback.print_exc()
        result = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}
    result["ms"] = int((_t.time() - started) * 1000)
    with JOBS_LOCK:
        JOBS[name] = {"running": False, "started": started, "result": result}


def start_job(name):
    if name not in MANUAL:
        return {"ok": False, "detail": "未知任务"}
    with JOBS_LOCK:
        if JOBS.get(name, {}).get("running"):
            return {"ok": False, "detail": "已经在跑"}
        JOBS[name] = {"running": True, "started": time.time(), "result": None}
    threading.Thread(target=_run_job, args=(name,), daemon=True).start()
    return {"ok": True, "started": True}


def idle_tick():
    """醒来走一轮。调用者负责判断该不该醒。睡着时不醒。"""
    import time as _tt
    global TURN_BUSY
    TURN_BUSY = True
    publish_view()
    _t0 = _tt.time()
    runlog.log("turn.begin", kind="idle")
    kick_maintenance()
    timers.tick_lock()
    core.USER_LOOP = False
    payload = core.context_assemble()
    stream_id = f"idle-{int(_tt.time() * 1000)}"
    chunks = []
    def idle_emit(kind, value):
        if kind == "delta":
            chunks.append(value)
            publish_stream(stream_id, str(value))
        elif kind == "tool":
            publish_tool(value)
        elif kind == "error":
            publish_error(str(value))
    try:
        _, final_text = run_with_tools(payload, idle_emit, stream_id=stream_id)
        reply = (final_text or "").strip()
        publish_stream_done(stream_id, reply)
    except Exception as exc:                 # noqa: BLE001
        publish_error(f"{type(exc).__name__}: {exc}")
        reply = ""
    runlog.log("turn.end", kind="idle", ms=int((_tt.time()-_t0)*1000),
               reply=len(reply), chunks=len(chunks))
    TURN_BUSY = False
    publish_view()
    return 1 if reply else 0


def _run_user_turn(text):
    """跑一轮用户消息；调用方必须已持有 LOCK。"""
    stream_id = f"turn-{int(time.time() * 1000)}"
    def emit(kind, value):
        if kind == "delta":
            publish_stream(stream_id, str(value))
        elif kind == "tool":
            publish_tool(value)
        elif kind == "error":
            publish_error(str(value))
    try:
        try:
            body.observe()
        except Exception as exc:              # noqa: BLE001
            log("observe.failed", error=f"{type(exc).__name__}: {exc}"[:80])
        core.USER_LOOP = True
        payload = core.context_assemble()
        _, reply = run_with_tools(payload, emit, stream_id=stream_id)
        publish_stream_done(stream_id, str(reply or ""))
    except Exception as exc:                  # noqa: BLE001
        import traceback
        traceback.print_exc()
        publish_error(f"{type(exc).__name__}: {exc}")
    finally:
        global TURN_BUSY
        core.USER_LOOP = False
        TURN_BUSY = False
        publish_view()
        try:
            LOCK.release()
        except RuntimeError:
            pass
        try:
            _drain_queued_turns()
        except Exception:                     # noqa: BLE001
            pass


def _drain_queued_turns():
    """当前轮结束后，把缓存池里的下一条用户消息接上。"""
    global TURN_BUSY
    with QUEUE_LOCK:
        if not QUEUED_TURNS:
            return
    if not LOCK.acquire(blocking=False):
        return
    with QUEUE_LOCK:
        if not QUEUED_TURNS:
            LOCK.release()
            return
        text = QUEUED_TURNS.pop(0)
    try:
        TURN_BUSY = True
        core.append_session([{"role": "user",
                              "name": core.payload.get("user_id", ""),
                              "content": text}])
        try:
            import events as _events
            _events.mark_user()
        except Exception:                     # noqa: BLE001
            pass
        publish_view()
        threading.Thread(target=_run_user_turn, args=(text,), daemon=True).start()
    except Exception:                         # noqa: BLE001
        TURN_BUSY = False
        try:
            LOCK.release()
        except RuntimeError:
            pass


def state_snapshot():
    ch = timers.load_character()
    marks = {}
    try:
        marks = json.loads((_paths.data("realTime", "events.json"))
                           .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        marks = {}
    return {
        "lastMessageAt": marks.get("lastMessageAt") or 0,
        "local": ch.get("local") or "",
        "active": (ch.get("state") or {}).get("active") or "wake",
        "metabolism": ch.get("metabolism") or {},
        "timers": [{"at": t.get("at"), "text": t.get("text"), "kind": t.get("kind")}
                   for t in timers.load_timers()],
        "pending": timers.peek_pending(),
    }


SECRET_KEYS = {"api", "key", "token", "secret"}
SKIP_KEYS = {"messages", "tools"}


def mask_tree(node):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in SECRET_KEYS and isinstance(value, str) and value:
                out[key] = "***"
            else:
                out[key] = mask_tree(value)
        return out
    return node


def mask_config():
    return mask_tree(core.loadConfig())


def merge_config(target, patch):
    """深合并；api 之类的掩码值不覆盖。"""
    for key, value in patch.items():
        if key in SECRET_KEYS and value == "***":
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_config(target[key], value)
        else:
            target[key] = value


REALM_FILES = ("PERSONA.md", "CHARACTER.md", "AGENT.md",
               "MEMORY.md", "LOREBOOK.md", "USER.md")


VIEW_LOCK = threading.Lock()
VIEW_REV = 0
VIEW_CACHE = None
VIEW_SUBSCRIBERS = []
SUB_LOCK = threading.Lock()


def _timer_view():
    """选当前最近到期的计时器：动作 / 闹钟 / 静默。"""
    now = time.time()
    ch = timers.load_character()
    sleeping = (ch.get("state") or {}).get("active") == "sleep"
    cands = []
    for t in timers.load_timers():
        try:
            at = float(t.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if not at:
            continue
        is_alarm = bool(t.get("alarm"))
        slot = str(t.get("slotId") or t.get("slot") or "").strip()
        kind = "alarm" if is_alarm else ("silence" if slot == "silence" else (slot or "action"))
        label = str(t.get("text") or "").strip()
        if is_alarm:
            label = label or "闹钟"
        elif not label:
            label = "动作" if not slot else slot
        prio = 0 if is_alarm else (1 if slot in ("short", "long", "event", "action") else 2)
        if at > now - 1:
            cands.append((at, prio, 0, {
                "id": f"{kind}:{t.get('id') or int(at * 1000)}",
                "kind": kind, "label": label, "endsAt": at,
                "remainingMs": max(0, int(round((at - now) * 1000))),
                "graceMs": 5000,   # 单条 message：两次模型最多约 4 秒 + 工具回执约 1 秒
            }))
    if not sleeping and last_activity_at():
        enabled, minutes = idle_settings()
        if enabled:
            at = float(last_activity_at()) + minutes * 60
            if at > now - 1:
                cands.append((at, 2, 1, {
                    "id": "silence", "kind": "silence", "label": "静默",
                    "endsAt": at,
                    "remainingMs": max(0, int(round((at - now) * 1000))),
                    "graceMs": 5000,   # 单条 message：两次模型最多约 4 秒 + 工具回执约 1 秒
                }))
    if not cands:
        return None
    return sorted(cands, key=lambda x: (x[0], x[1], x[2]))[0][3]


def _usage_text():
    try:
        data = last_usage() or {}
    except Exception:                        # noqa: BLE001
        return "usage —"
    if not data:
        return "usage —"

    def num(value):
        value = int(value or 0)
        if value >= 1_000_000:
            return f"{value / 1_000_000:.3f}M"
        if value >= 10_000:
            return f"{value / 1000:.1f}k"
        return str(value)

    return (f"输入 {num(data.get('prompt'))}   输出 {num(data.get('completion'))}   "
            f"平均命中 {data.get('avg', 0):.0f}%   单次命中 {data.get('cache', 0):.0f}%   "
            f"已用上下文 {data.get('ratio', 0):.1f}%")


def _view_messages():
    try:
        rows = session_history(400)
    except Exception:                        # noqa: BLE001
        rows = []
    out = []
    tips = None
    for idx, row in enumerate(rows):
        role = str(row.get("role") or "")
        text = str(row.get("text") or "").strip()
        ts = str(row.get("ts") or "")
        if not text and role != "tip":
            continue
        if role == "tip":
            if tips is None:
                tips = {"id": f"tip-{idx}", "kind": "tip", "items": [], "ts": ts}
                out.append(tips)
            tips["items"].append({"name": row.get("name") or "tool", "text": text})
            continue
        tips = None
        if role in ("narration", "scene", "action", "line", "user", "assistant"):
            kind = role
        else:
            kind = str(row.get("role") or "narration")
        out.append({"id": f"m{idx}", "kind": kind, "text": text, "ts": ts})
    return out


def build_view():
    """主会话的完整只读视图；前端只渲染这个对象。"""
    try:
        cfg = core.loadConfig()
        agent_name = str(((cfg.get("harness") or {}).get("name") or "")).strip()
        user_name = str(((cfg.get("payload") or {}).get("user_id") or "")).strip()
    except Exception:                        # noqa: BLE001
        agent_name = user_name = ""
    ch = timers.load_character()
    state = ch.get("state") or {}
    metab = ch.get("metabolism") or {}
    status = "睡着" if state.get("active") == "sleep" else "醒着"
    return {
        "rev": VIEW_REV,
        "serverNow": time.time(),
        "busy": bool(TURN_BUSY),
        "agentName": agent_name,
        "userName": user_name,
        "messages": _view_messages(),
        "timer": _timer_view(),
        "state": {
            "location": str(ch.get("local") or "未知"),
            "status": status,
            "metabolismText": "　".join(
                f"{name} {float(metab.get(key, 0)):.0f}"
                for name, key in (("饱食", "satiety"), ("水分", "hydration"),
                                  ("疲劳", "fatigue"), ("困意", "sleepiness"))
            ),
        },
        "usage": {"text": _usage_text()},
        "plan": [],
    }


def publish_view():
    """重算并推给所有 /events 订阅者。"""
    global VIEW_REV, VIEW_CACHE
    with VIEW_LOCK:
        VIEW_REV += 1
        VIEW_CACHE = build_view()
        view = VIEW_CACHE
    with SUB_LOCK:
        dead = []
        for q in VIEW_SUBSCRIBERS:
            try:
                q.put_nowait(("view", view))
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                VIEW_SUBSCRIBERS.remove(q)
            except ValueError:
                pass
    return view


def publish_event(kind, payload):
    """把单个流事件发给所有订阅者。"""
    with SUB_LOCK:
        dead = []
        for q in VIEW_SUBSCRIBERS:
            try:
                q.put_nowait((kind, payload))
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                VIEW_SUBSCRIBERS.remove(q)
            except ValueError:
                pass


def publish_stream(stream_id, delta):
    publish_event("stream", {"id": stream_id, "delta": delta})


def publish_stream_done(stream_id, text):
    publish_event("done", {"id": stream_id, "text": text})


# session 每次追加后同步刷新 view，计时器随消息一起更新。
core.session_hooks = publish_view


def publish_tool(payload):
    publish_event("tool", payload)


def publish_error(message):
    publish_event("error", {"message": message})


def current_view():
    global VIEW_CACHE
    with VIEW_LOCK:
        if VIEW_CACHE is None:
            VIEW_CACHE = build_view()
        return VIEW_CACHE


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/history":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                limit = int((query.get("limit") or ["40"])[0])
            except ValueError:
                limit = 40
            rows, total = session_history(limit, with_total=True)
            return self._json({"messages": rows, "total": total})
        if path == "/session":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                since = int((query.get("since") or [""])[0] or 0) or None
                tail = int((query.get("tail") or ["200"])[0])
            except ValueError:
                since, tail = None, 200
            rev, rows = core.read_snapshot(tail=tail, since=since)
            if rows is None:
                self.send_response(204)
                self.end_headers()
                return
            return self._json({"rev": rev, "rows": rows})
        if path == "/skill":
            return self._json(skill_text())
        if path == "/usage":
            return self._json(last_usage())
        if path == "/action":
            with JOBS_LOCK:
                data = {name: {"running": bool(job.get("running")),
                               "result": job.get("result")}
                        for name, job in JOBS.items()}
            return self._json({"available": MANUAL, "jobs": data})
        if path == "/view":
            return self._json(build_view())
        if path == "/events":
            return self._events()
        if path == "/state":
            return self._json(state_snapshot())
        if path == "/config":
            return self._json(mask_config())
        if path == "/realm":
            return self._json({"files": [f for f in REALM_FILES
                                         if (core.REALM_ROOT / f).exists()]})
        if path.startswith("/realm/"):
            name = urllib.parse.unquote(path[len("/realm/"):])
            if name not in REALM_FILES:
                return self._json({"error": "不允许的文件"}, 403)
            target = core.REALM_ROOT / name
            if not target.exists():
                return self._json({"error": "没有这个文件"}, 404)
            text = target.read_text(encoding="utf-8")
            return self._json({"name": name, "text": text,
                               "rendered": core.increase_heading_level(text)})
        if path == "/":
            return self._json({"ok": True, "service": "philosophical-zombie"})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/sync":
            return self._sync(self._body())
        if path == "/turn":
            return self._turn(self._body())
        if path == "/action":
            data = self._body()
            name = str((data or {}).get("action") or "").strip()
            return self._json(start_job(name))
        if path == "/config":
            return self._save_config(self._body())
        if path == "/skill":
            return self._save_skill(self._body())
        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/realm/"):
            name = urllib.parse.unquote(path[len("/realm/"):])
            if name not in REALM_FILES:
                return self._json({"error": "不允许的文件"}, 403)
            data = self._body()
            text = str(data.get("text") or "")
            (core.REALM_ROOT / name).write_text(text, encoding="utf-8")
            return self._json({"ok": True, "chars": len(text)})
        self._json({"error": "not found"}, 404)

    def _events(self):
        """SSE：先给完整 view，之后只推后端重算的 view。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue(maxsize=512)
        with SUB_LOCK:
            VIEW_SUBSCRIBERS.append(q)
        try:
            first = build_view()
            self._sse("view", first)
            while True:
                try:
                    kind, payload = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse(kind, payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with SUB_LOCK:
                try:
                    VIEW_SUBSCRIBERS.remove(q)
                except ValueError:
                    pass
            self.close_connection = True

    def _sse(self, kind, value):
        chunk = f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def _sync(self, data):
        with LOCK:
            try:
                timers.advance()
                timers.settle()
            except Exception:                # noqa: BLE001
                pass
            view = publish_view()
        try:
            _drain_queued_turns()
        except Exception:                    # noqa: BLE001
            pass
        return self._json({"rev": view.get("rev"), "view": view})

    def _turn(self, data):
        text = str(data.get("text") or "").strip()
        if not text:
            return self._json({"error": "text 不能为空"}, 400)
        if not LOCK.acquire(blocking=False):
            with QUEUE_LOCK:
                QUEUED_TURNS.append(text)
                pending = len(QUEUED_TURNS)
            return self._json({"ok": True, "accepted": True, "queued": True,
                               "pending": pending}, 202)
        try:
            global TURN_BUSY
            TURN_BUSY = True
            core.append_session([{"role": "user",
                                  "name": core.payload.get("user_id", ""),
                                  "content": text}])
            try:
                import events as _events
                _events.mark_user()
            except Exception:                 # noqa: BLE001
                pass
            publish_view()
            threading.Thread(target=_run_user_turn, args=(text,),
                             daemon=True).start()
        except Exception as exc:              # noqa: BLE001
            TURN_BUSY = False
            LOCK.release()
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json({"ok": True, "accepted": True}, 202)

    def _run_turn_locked(self, text):
        _run_user_turn(text)

    def _save_skill(self, data):
        items = data.get("items")
        if not isinstance(items, list):
            return self._json({"error": "items 必须是数组"}, 400)
        target = core.REALM_ROOT / "skill" / "skill.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"skills": items}, ensure_ascii=False, indent=4) + "\n",
                          encoding="utf-8")
        self._json({"ok": True, "count": len(items)})

    def _save_config(self, data):
        payload = data.get("config") if isinstance(data.get("config"), dict) else None
        if payload:
            cfg = core.loadConfig()
            merge_config(cfg, payload)
            core.CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n",
                                     encoding="utf-8")
            return self._json({"ok": True})
        return self._save_config_flat(data)

    def _save_config_flat(self, data):
        cfg_path = core.CFG_PATH
        cfg = core.loadConfig()
        payload = cfg.setdefault("payload", {})
        for key, value in (data.get("payload") or {}).items():
            if key == "api" and value == "***":
                continue
            if key in payload:
                payload[key] = value
        harness = cfg.setdefault("harness", {})
        for key, value in (data.get("harness") or {}).items():
            if isinstance(harness.get(key), dict) and isinstance(value, dict):
                harness[key].update(value)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n",
                            encoding="utf-8")
        self._json({"ok": True})


def idle_loop():
    import time as _time
    global TURN_BUSY
    while True:
        _time.sleep(TICK_SECONDS)
        if not LOCK.acquire(blocking=False):
            continue
        try:
            # 计时 / 生理 / 事件：只追加
            timers.advance()
            timers.settle()
            for hook in (getattr(core, "reminder_hooks", None),
                         getattr(core, "event_hooks", None)):
                if hook is not None:
                    try:
                        hook.check()
                    except Exception:            # noqa: BLE001
                        pass

            try:
                kick_maintenance()
            except Exception:               # noqa: BLE001
                pass
            if should_wake():
                print("[wake]", "静默", flush=True)
                idle_tick()
            publish_view()
        except Exception:                        # noqa: BLE001
            import traceback
            traceback.print_exc()
        finally:
            if TURN_BUSY:
                TURN_BUSY = False
                try:
                    publish_view()
                except Exception:            # noqa: BLE001
                    pass
            LOCK.release()
            try:
                _drain_queued_turns()
            except Exception:                # noqa: BLE001
                pass


def main():
    import threading
    try:
        import backup
        backup.snapshot("boot")
    except Exception as exc:                  # noqa: BLE001
        print("[boot] 备份失败:", exc, flush=True)
    try:
        fixed = core.repair_session()
        if fixed:
            print(f"[boot] 修复会话：{fixed} 行", flush=True)
    except Exception as exc:                  # noqa: BLE001
        print("[boot] 会话修复失败:", exc, flush=True)
    ensure_activity_mark()
    threading.Thread(target=idle_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"服务已启动：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("停止")


if __name__ == "__main__":
    main()

