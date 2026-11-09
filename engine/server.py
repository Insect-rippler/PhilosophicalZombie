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
import core
import modelio
import tools                      # noqa: E402
from engine import timers        # noqa: E402

PORT = 8765
TICK_SECONDS = 60          # 1 tick = 1 分钟
HOST = "127.0.0.1"
LOCK = threading.Lock()          # 一轮对话独占，避免两轮同时改 session


# ---------------- 对话 ----------------

TOOL_NARRATION = {
    "active": "她在做一件事",
}


def describe_tool(name, args, result):
    """工具调用 → (类型, 文本)。active 是旁白，其余是系统提示。"""
    if name in ("active", "scene"):
        try:
            desc = json.loads(args).get("description") if isinstance(args, str) else ""
        except json.JSONDecodeError:
            desc = ""
        return "narration", desc or "她动了一下"
    if name == "fight":
        return "tip", "战斗判定"
    if name == "remember":
        return "tip", "记下了一件事"
    if name == "plan":
        return "tip", "整理了目标"
    return "tip", name




INJECTED_TAGS = ("<memory_momentary>", "<lorebook_momentary>", "<skill_knowledge>",
                 "<state>", "<plan>", "<user>", "<assistant>")


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


def run_with_tools(payload, emit):
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

    def emit_pending():
        if pending_text.strip():
            emit("delta", pending_text)
    echo = EchoStrip()
    rounds = max(1, int(core.LOOP_MAX))       # 上限统一读 harness.loop.maxRounds
    for _round in range(rounds):
        if _round == rounds - 1:              # 最后一轮关思考，免得只吐 reasoning
            body["thinking"] = {"type": "disabled"}
        calls, content_parts, usage = [], [], None
        reasoning_parts = []
        salvage = Salvager()
        failure = ""
        for _attempt in range(3):
            calls, content_parts, usage = [], [], None
            reasoning_parts = []
            salvage = Salvager()
            buffered = []
            try:
                with requests.post(
                    core.chat_completions_url,
                    headers={"Authorization": f"Bearer {core.API_KEY}",
                             "Content-Type": "application/json"},
                    json=body, stream=True, timeout=(15, 600),
                    verify=core.VERIFY_SSL, proxies={"http": None, "https": None},
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"status {response.status_code}")
                    for raw in response.iter_lines(decode_unicode=False):
                        if not raw:
                            continue
                        line = raw.decode("utf-8", "replace")
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("tool_calls"):
                                for call in delta["tool_calls"]:
                                    idx = call.get("index", len(calls))
                                    while len(calls) <= idx:
                                        calls.append({"id": "", "type": "function",
                                                      "function": {"name": "", "arguments": ""}})
                                    slot = calls[idx]
                                    if call.get("id"):
                                        slot["id"] = call["id"]
                                    fn = call.get("function") or {}
                                    slot["function"]["name"] += fn.get("name") or ""
                                    slot["function"]["arguments"] += fn.get("arguments") or ""
                            thought = delta.get("reasoning_content")
                            if thought:
                                reasoning_parts.append(thought)
                            piece = delta.get("content")
                            if piece:
                                content_parts.append(piece)
                                visible, events = salvage.feed(piece)
                                visible = echo.feed(visible)
                                if visible:
                                    buffered.append(visible)      # 先攒着，动作展示完再放
                                for event in events:
                                    emit("tool", event)
                failure = ""
                break
            except Exception as exc:             # noqa: BLE001
                failure = f"{type(exc).__name__}: {exc}"
                log("request.retry", round=_round + 1, attempt=_attempt + 1,
                    error=failure[:120])
                if _attempt < 2:
                    time.sleep(1.2 * (_attempt + 1))
        if failure:
            log("request.failed", round=_round + 1, error=failure[:160])
            return False, ""

        rest = echo.close()
        if rest.strip():
            buffered.append(rest)
        tail, tail_events = salvage.flush()
        if tail.strip():
            buffered.append(tail)
        for event in tail_events:
            emit("tool", event)
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

        last_text = "".join(content_parts)
        echo.remember(last_text)
        if not calls:
            emit_pending()
            if not pending_text.strip() and spoken_line:
                return True, ""
            run_with_tools.reasoning = "".join(reasoning_parts)
            return True, pending_text


        assistant = {"role": "assistant", "content": "".join(content_parts)}
        if reasoning_parts:
            assistant["reasoning_content"] = "".join(reasoning_parts)
        assistant["tool_calls"] = calls
        results = []
        for call in calls:
            name = (call.get("function") or {}).get("name") or ""
            name, args = name, (call.get("function") or {}).get("arguments") or "{}"
            output = tools.execute_tool_call(call, tools.load_registry())
            scene = said = None
            if name == "active":
                try:
                    parsed = json.loads(output)
                except (json.JSONDecodeError, AttributeError):
                    parsed = {}
                said = parsed.get("line")
                try:
                    scene = json.loads(args).get("scene")
                except (json.JSONDecodeError, AttributeError):
                    scene = None
            if isinstance(scene, str) and scene.strip():      # 1 环境
                emit("tool", {"kind": "narration", "name": "scene", "text": scene.strip()})
            kind, label = describe_tool(name, args, output)
            emit("tool", {"kind": kind, "name": name, "text": label})   # 2 动作
            if isinstance(said, str) and said.strip():        # 3 动作时说的话
                said = said.strip()
                spoken_line = (spoken_line + said) if spoken_line else said
                emit("delta", said)
            results.append({"role": "tool", "tool_call_id": call.get("id", ""),
                            "content": output})

        emit_pending()                     # 先动作，后台词
        core.append_session([assistant] + results)
        payload = core.context_assemble()
        body = dict(payload)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}


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
                  "content": "只输出角色这一轮说出口的一句话。不要旁白、不要括号、不要解释。"}]
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

def session_history(limit=40):
    """回读 session，给前端重建对话。工具调用折成旁白或提示行。"""
    if not core.SESSION_PATH.exists():
        return []
    rows = []
    for raw in core.SESSION_PATH.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
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
                if name in ("active", "scene"):
                    try:
                        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    desc = str(args.get("description") or "").strip()
                    if desc:
                        rows.append({"role": "narration", "text": desc,
                                     "ts": item.get("ts") or ""})
                elif name:
                    rows.append({"role": "tip", "text": name})
            if not text:
                continue
        if not text:
            continue
        rows.append({"role": role, "name": item.get("name") or "",
                     "text": text, "ts": item.get("ts") or ""})
    return rows[-limit:]


def usage_window():
    """平均命中的统计窗口，读 harness.usage.avgWindow，限制在 50~300。"""
    try:
        value = int((core.harness.get("usage") or {}).get("maxTurn", 100))
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

        # 兜底：DSML 形式的调用
        for match in list(DSML_CALL.finditer(self.hold)):
            events.append({"kind": "narration", "name": match.group(1), "text": ""})
        return text, events

    def _inspect(self, chunk, _unused):
        found = []
        tag = TAG_ACTION.match(chunk)
        if tag:
            name = tag.group(1)
            attrs = tag.group(2) or ""
            desc = ATTR_DESC.search(attrs)
            found.append({"kind": "narration", "name": name,
                          "text": desc.group(1) if desc else ""})
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
            events.append({"kind": "narration", "name": match.group(1),
                           "text": desc.group(1).strip() if desc else ""})
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
    node = (core.harness or {}).get("idle") or {}
    try:
        minutes = float(node.get("afterMinutes", 30))       # 以 tick 计，即分钟
    except (TypeError, ValueError):
        minutes = 30.0
    return bool(node.get("enabled", True)), max(1.0, minutes)


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
    """醒不醒：计时/事件到了必醒；静默只在醒着时算。"""
    import time as _t
    now = _t.time() if now is None else float(now)
    if timers.peek_pending():                 # 计时器到期 / 事件触发
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
            result = maintain.run()
            log("maintain.done", ok=result.get("ok"), reason=str(result.get("reason") or "")[:60])
        except Exception as exc:           # noqa: BLE001
            log("maintain.failed", error=f"{type(exc).__name__}: {exc}"[:120])
        finally:
            _MAINTAIN_LOCK.release()

    threading.Thread(target=job, daemon=True).start()
    return True


def idle_tick():
    """醒来走一轮。调用者负责判断该不该醒。睡着时不醒。"""
    kick_maintenance()
    try:
        import impression
        impression.maybe_update()
    except Exception:      # noqa: BLE001
        pass
    timers.tick_lock()
    try:
        body.observe()            # 先把对方这一轮做的事结算掉
    except Exception as exc:      # noqa: BLE001
        log("observe.failed", error=f"{type(exc).__name__}: {exc}"[:80])
    payload = core.context_assemble()
    chunks = []
    _, final_text = run_with_tools(
        payload, lambda kind, value: chunks.append(value) if kind == "delta" else None)
    reply = (final_text or "").strip()
    if reply:
        record = {"role": "assistant", "content": reply}
        thought = getattr(run_with_tools, "reasoning", "") or ""
        if thought:
            record["reasoning_content"] = thought
        core.append_session([record])
    return 1 if reply else 0


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
            return self._json({"messages": session_history(limit)})
        if path == "/skill":
            return self._json(skill_text())
        if path == "/usage":
            return self._json(last_usage())
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
        if path == "/turn":
            return self._turn(self._body())
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

    def _turn(self, data):
        text = str(data.get("text") or "").strip()
        if not text:
            return self._json({"error": "text 不能为空"}, 400)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(kind, value):
            chunk = f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()

        print(f"[turn] 收到：{text[:30]}", flush=True)
        with LOCK:
            core.append_session([{"role": "user",
                                  "name": core.payload.get("user_id", ""),
                                  "content": text}])
            try:
                payload = core.context_assemble()
                ok, reply_raw = run_with_tools(payload, emit)
                reply = (reply_raw or "").strip()
            except Exception as e:           # noqa: BLE001
                import traceback
                traceback.print_exc()
                emit("error", f"{type(e).__name__}: {e}")
                reply = ""
            if reply:
                done_msg = {"role": "assistant", "content": reply}
                reasoning = getattr(run_with_tools, "reasoning", "")
                if reasoning:
                    done_msg["reasoning_content"] = reasoning
                core.append_session([done_msg])
            emit("done", reply)
        self.close_connection = True

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

            if should_wake():
                print("[wake]", "事件/计时" if pending else "静默", flush=True)
                idle_tick()
        except Exception:                        # noqa: BLE001
            import traceback
            traceback.print_exc()
        finally:
            LOCK.release()


def main():
    import threading
    threading.Thread(target=idle_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"服务已启动：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("停止")


if __name__ == "__main__":
    main()

