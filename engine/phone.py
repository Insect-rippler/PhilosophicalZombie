"""本机手机：聊天、动态、日记与未读状态。

手机消息和主会话分离：这里只存手机自己的内容；主 UI 只在“通信设备”里读。
"""

import json
import re
import time
from pathlib import Path

import fsio
import paths as _paths

ROOT = _paths.data("phone")
MEDIA = ROOT / "media"
APPS = {"chat": "消息", "moments": "动态", "diary": "日记"}
DEFAULT_APP = "chat"


def identity():
    """手机里的两个名字从配置和角色卡读取，换卡不用改代码。"""
    user = "用户"
    self_name = "她"
    try:
        cfg = json.loads(_paths.data("Philosophical.json").read_text(encoding="utf-8"))
        user = str((cfg.get("payload") or {}).get("user_id") or user).strip() or user
    except (OSError, json.JSONDecodeError):
        pass
    try:
        ch = json.loads(_paths.data("realTime", "character.json").read_text(encoding="utf-8"))
        self_name = str(ch.get("name") or self_name).strip() or self_name
    except (OSError, json.JSONDecodeError):
        pass
    return user, self_name


def _path(app):
    app = str(app or DEFAULT_APP).strip()
    if app not in APPS:
        raise ValueError(f"没有这个应用：{app}")
    return ROOT / f"{app}.jsonl"


def _state_path():
    return ROOT / "state.json"


def _read(app):
    path = _path(app)
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _read_state():
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data):
    fsio.write_json(_state_path(), data)


def _read_set(data, app):
    raw = (data.get("read") or {}).get(app) or []
    return {int(x) for x in raw if str(x).strip().isdigit()}


def _mark_read(app, lines):
    data = _read_state()
    read = data.setdefault("read", {})
    current = _read_set(data, app)
    current.update(int(x) for x in lines)
    read[app] = sorted(current)
    _write_state(data)


def _message_rows(app, rows):
    data = _read_state()
    read = _read_set(data, app)
    user_name, self_name = identity()
    likes = set(int(x) for x in ((data.get("likes") or {}).get(app) or [])
                if str(x).strip().isdigit())
    out = []
    for i, item in enumerate(rows, 1):
        sender = str(item.get("sender") or "").strip()
        if sender not in ("user", "self"):
            sender = "self" if str(item.get("from") or "") == self_name else "user"
        out.append({
            "line": i,
            "sender": sender,
            "from": str(item.get("from") or ""),
            "kind": str(item.get("kind") or "text"),
            "text": str(item.get("text") or ""),
            "media": str(item.get("media") or ""),
            "caption": str(item.get("caption") or ""),
            "ts": str(item.get("ts") or ""),
            "read": i in read or sender != "user",
            "liked": i in likes,
        })
    return out


def unread_count(app=DEFAULT_APP):
    if app != "chat":
        return 0
    rows = _message_rows(app, _read(app))
    return sum(1 for row in rows if row.get("sender") == "user" and not row.get("read"))


def user_unread_count(app=DEFAULT_APP):
    """给界面看的未读：只算角色发出、用户还没点开的消息。"""
    rows = _message_rows(app, _read(app))
    data = _read_state()
    last = int((data.get("ui_read") or {}).get(app) or 0)
    return sum(1 for row in rows
               if row.get("sender") == "self" and row.get("line") > last)


def mark_user_read(app=DEFAULT_APP):
    data = _read_state()
    data.setdefault("ui_read", {})[app] = len(_read(app))
    _write_state(data)
    return {"ok": True, "app": app, "userUnread": 0}


def apps_payload():
    return {
        "ok": True,
        "apps": [
            {"id": app, "name": name, "unread": unread_count(app),
             "userUnread": user_unread_count(app)}
            for app, name in APPS.items()
        ],
        "unread": unread_count("chat"),
        "userUnread": user_unread_count("chat"),
    }


def list_messages(app=DEFAULT_APP, limit=8):
    rows = _message_rows(app, _read(app))
    limit = max(1, min(int(limit or 8), 32))
    return {"ok": True, "app": app, "unread": unread_count(app),
            "lines": rows[-limit:], "has_more": len(rows) > limit}


def search_messages(app=DEFAULT_APP, query="", limit=4):
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "reason": "search 需要 query"}
    rows = _message_rows(app, _read(app))
    hits = [row for row in rows
            if query in (row.get("text") or "")
            or query in (row.get("caption") or "")]
    limit = max(1, min(int(limit or 4), 4))
    return {"ok": True, "app": app, "query": query,
            "matches": hits[-limit:], "total": len(hits)}


def read_messages(app=DEFAULT_APP, line=0, around=2, mark=True):
    rows = _message_rows(app, _read(app))
    if not rows:
        return {"ok": True, "app": app, "lines": [], "unread": unread_count(app)}
    try:
        line = int(line or 0)
    except (TypeError, ValueError):
        line = 0
    around = max(0, min(int(around or 2), 8))
    if line <= 0:
        line = len(rows)
    line = max(1, min(line, len(rows)))
    start = max(0, line - 1 - around)
    end = min(len(rows), line + around)
    chunk = rows[start:end]
    if mark:
        marked = [row["line"] for row in chunk
                  if row.get("sender") == "user"]
        _mark_read(app, marked)
        for row in chunk:
            if row.get("line") in marked:
                row["read"] = True
    return {"ok": True, "app": app, "line": line, "around": around,
            "lines": chunk, "unread": unread_count(app)}


def append_incoming(text="", kind="text", media="", caption=""):
    text = str(text or "").strip()
    caption = str(caption or "").strip()
    if not text and not media:
        return {"ok": False, "reason": "消息需要 text 或 media"}
    user_name, _ = identity()
    row = {
        "sender": "user",
        "from": user_name,
        "kind": str(kind or "text"),
        "text": text,
        "media": str(media or ""),
        "caption": caption,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    fsio.append_line(_path("chat"), json.dumps(row, ensure_ascii=False))
    return {"ok": True, "app": "chat", "line": len(_read("chat")),
            "unread": unread_count("chat")}


def send(text="", kind="text", media="", caption=""):
    text = str(text or "").strip()
    caption = str(caption or "").strip()
    if not text and not media:
        return {"ok": False, "reason": "消息需要 text 或 media"}
    safe_media = _safe_media(media)
    if media and not safe_media:
        return {"ok": False, "reason": "media 只能使用手机媒体库里的素材"}
    user_name, self_name = identity()
    row = {
        "sender": "self",
        "from": self_name,
        "kind": str(kind or "text"),
        "text": text,
        "media": safe_media,
        "caption": caption,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    fsio.append_line(_path("chat"), json.dumps(row, ensure_ascii=False))
    return {"ok": True, "app": "chat", "to": user_name,
            "line": len(_read("chat"))}


def publish(app="moments", text="", media="", caption="", kind="text"):
    if app not in ("moments", "diary"):
        return {"ok": False, "reason": "publish 只支持 moments 或 diary"}
    text = str(text or "").strip()
    caption = str(caption or "").strip()
    if not text and not media:
        return {"ok": False, "reason": "动态/日记需要 text 或 media"}
    safe_media = _safe_media(media)
    if media and not safe_media:
        return {"ok": False, "reason": "media 只能使用手机媒体库里的素材"}
    _, self_name = identity()
    row = {
        "sender": "self",
        "from": self_name,
        "kind": str(kind or (kind_or_text(media) if media else "text")),
        "text": text,
        "media": safe_media,
        "caption": caption,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    fsio.append_line(_path(app), json.dumps(row, ensure_ascii=False))
    return {"ok": True, "app": app, "line": len(_read(app))}


def kind_or_text(media):
    ext = Path(str(media or "")).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return "image"
    if ext in (".wav", ".mp3", ".ogg", ".m4a"):
        return "voice"
    return "text"


def _safe_media(value):
    value = str(value or "").strip()
    if not value:
        return ""
    p = Path(value)
    if not p.is_absolute():
        p = MEDIA / p
    try:
        p = p.resolve()
        p.relative_to(MEDIA.resolve())
    except (OSError, ValueError):
        return ""
    return str(p)


def toggle_like(app="moments", line=0):
    app = str(app or "moments").strip()
    if app not in ("moments", "diary"):
        return {"ok": False, "reason": "只能给动态或日记点赞"}
    rows = _read(app)
    try:
        line = int(line)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "line 不正确"}
    if line < 1 or line > len(rows):
        return {"ok": False, "reason": "没有这一条"}
    data = _read_state()
    likes = data.setdefault("likes", {})
    current = {int(x) for x in (likes.get(app) or []) if str(x).strip().isdigit()}
    if line in current:
        current.remove(line)
        liked = False
    else:
        current.add(line)
        liked = True
    likes[app] = sorted(current)
    _write_state(data)
    return {"ok": True, "app": app, "line": line, "liked": liked}


def ui_payload(app=DEFAULT_APP, limit=32):
    app = str(app or DEFAULT_APP)
    if app not in APPS:
        app = DEFAULT_APP
    payload = apps_payload()
    payload["app"] = app
    payload["messages"] = list_messages(app, limit)["lines"]
    payload["userUnread"] = user_unread_count(app)
    payload["userName"], payload["selfName"] = identity()
    return payload
