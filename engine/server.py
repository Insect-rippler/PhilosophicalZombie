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
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import core                      # noqa: E402
from engine import timers        # noqa: E402

PORT = 8765
HOST = "127.0.0.1"
LOCK = threading.Lock()          # 一轮对话独占，避免两轮同时改 session


# ---------------- 对话 ----------------

TOOL_NARRATION = {
    "active": "她在做一件事",
}


def describe_tool(name, args, result):
    """工具调用 → (类型, 文本)。active 是旁白，其余是系统提示。"""
    if name == "active":
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


def stream_turn(text, emit):
    """先追加 user，再流式请求。逐块 yield 文本；失败时退回整段。"""
    payload = core.context_assemble(text)
    body = dict(payload)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    body.pop("tools", None) if False else None

    import requests
    import urllib3
    urllib3.disable_warnings()

    # 先跑工具循环：有工具调用的轮次不走纯流式
    streamed, reply = run_with_tools(payload, emit)
    if streamed:
        yield reply if reply else ""
        return

    got_any = False
    try:
        with requests.post(
            core.chat_completions_url,
            headers={"Authorization": f"Bearer {core.API_KEY}",
                     "Content-Type": "application/json"},
            json=body, stream=True, timeout=(15, 600),
            verify=core.VERIFY_SSL, proxies={"http": None, "https": None},
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
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
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    got_any = True
                    yield delta
    except Exception as e:                   # noqa: BLE001
        if not got_any:
            # 流式不可用 → 整段兜底
            response = core.send(core.payload | {
                "stream": False,
                "messages": payload["messages"],
                "tools": payload["tools"],
            })
            text_reply = ""
            if response:
                text_reply = ((response.get("choices") or [{}])[0]
                              .get("message") or {}).get("content") or ""
            if text_reply:
                yield text_reply
            else:
                yield f"（出错了：{type(e).__name__}）"


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
    rounds = max(1, int(core.LOOP_MAX))       # 上限统一读 harness.loop.maxRounds
    for _round in range(rounds):
        if _round == rounds - 1:              # 最后一轮关思考，免得只吐 reasoning
            body["thinking"] = {"type": "disabled"}
        calls, content_parts, usage = [], [], None
        try:
            with requests.post(
                core.chat_completions_url,
                headers={"Authorization": f"Bearer {core.API_KEY}",
                         "Content-Type": "application/json"},
                json=body, stream=True, timeout=(15, 600),
                verify=core.VERIFY_SSL, proxies={"http": None, "https": None},
            ) as response:
                if response.status_code != 200:
                    return False, ""
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
                        piece = delta.get("content")
                        if piece:
                            content_parts.append(piece)
                            emit("delta", piece)
        except Exception:                    # noqa: BLE001
            return False, ""

        if usage:
            try:
                rec = core.record_usage(usage, round_no=_round + 1)
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
        if not calls:
            return True, last_text


        assistant = {"role": "assistant", "content": "".join(content_parts)}
        assistant["tool_calls"] = calls
        results = []
        for call in calls:
            name = (call.get("function") or {}).get("name") or ""
            name, args = name, (call.get("function") or {}).get("arguments") or "{}"
            output = core.execute_tool_call(call, core.load_registry())
            kind, label = describe_tool(name, args, output)
            emit("tool", {"kind": kind, "name": name, "text": label})
            results.append({"role": "tool", "tool_call_id": call.get("id", ""),
                            "content": output})
        core.append_session([assistant] + results)
        payload = core.context_assemble()
        body = dict(payload)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}


    if not last_text:                        # 还是没有正文：再来一次，纯文本
        try:
            plain = dict(body)
            plain["stream"] = False
            plain["tools"] = []
            plain["tool_choice"] = "none"
            plain["thinking"] = {"type": "disabled"}
            plain.pop("stream_options", None)
            response = core.send(plain)
            if response:
                text = ((response.get("choices") or [{}])[0]
                        .get("message") or {}).get("content") or ""
                if text:
                    emit("delta", text)
                    return True, text
        except Exception:                    # noqa: BLE001
            pass
    if not last_text:                        # 一轮下来没有正文：补一次纯文本请求
        try:
            plain = dict(body)
            plain["stream"] = False
            plain["tools"] = []
            plain["tool_choice"] = "none"
            plain["thinking"] = {"type": "disabled"}
            plain.pop("stream_options", None)
            response = core.send(plain)
            if response:
                text = ((response.get("choices") or [{}])[0]
                        .get("message") or {}).get("content") or ""
                if text:
                    emit("delta", text)
                    return True, text
        except Exception:                    # noqa: BLE001
            pass
    return True, last_text


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
                if name == "active":
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
        value = int((core.harness.get("usage") or {}).get("avgWindow", 100))
    except (TypeError, ValueError):
        value = 100
    return max(50, min(300, value))


def average_cache(window):
    """最近 window 条请求的平均缓存命中率。"""
    files = sorted((core.ROOT / "state" / "usage").glob("*.jsonl"))
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
    files = sorted((core.ROOT / "state" / "usage").glob("*.jsonl"))
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


def state_snapshot():
    ch = timers.load_character()
    marks = {}
    try:
        marks = json.loads((core.ROOT / "state" / "realTime" / "events.json")
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


def mask_config():
    cfg = core.loadConfig()
    payload = dict(cfg.get("payload") or {})
    if payload.get("api"):
        payload["api"] = "***"
    return {"payload": payload, "harness": cfg.get("harness") or {}}


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
                core.append_session([{"role": "assistant", "content": reply}])
            emit("done", reply)
        self.close_connection = True

    def _save_config(self, data):
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


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"服务已启动：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("停止")


if __name__ == "__main__":
    main()

