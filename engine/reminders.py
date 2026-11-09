"""生理提醒：条件满足就必触发，不看概率。

和事件池不同——事件池是"也许会发生"，这里是"必然会发生"：
饿了就是饿了，不需要掷骰。

用「越界触发」而不是「持续触发」：条件从假变真时提醒一次，
回落到正常再越界才会再提醒。否则每轮都在喊饿。

规则写在 world/<世界>/reminders.json，代码这边只认字段。
"""

import fsio
import json
import paths as _paths
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = _paths.data("realTime", "reminders.json")
WORLD = ROOT / "ConsciousnessRealm" / "world"

from engine import timers


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    fsio.write_json(path, data)


def world_dir():
    name = _read(WORLD / "active.json", {}).get("world") or ""
    root = WORLD / name if name else None
    return root if root and root.exists() else None


def load_rules():
    root = world_dir()
    data = _read(root / "reminders.json", {}) if root else {}
    return [r for r in (data.get("reminders") or []) if isinstance(r, dict)]


EVENTS_STATE = _paths.data("realTime", "events.json")


def _idle_hours(now):
    """距 session 最后一条消息多少小时。记录在 events.json 里。"""
    marks = _read(EVENTS_STATE, {}) or {}
    last = marks.get("lastMessageAt") or marks.get("lastUserAt")
    if not last:
        return 0.0
    return max(0.0, (now - float(last)) / 3600.0)


def _match(rule, ch, now=None):
    """条件是否成立。支持 metabolism 区间与 sleeping。"""
    need = rule.get("needs") or {}
    metab = ch.get("metabolism") or {}
    if "_idleHours" in need:
        span = need["_idleHours"]
        idle = _idle_hours(now or time.time())
        if isinstance(span, (list, tuple)):
            if not (span[0] <= idle <= span[1]):
                return False
        elif idle < float(span):
            return False
    for key, span in need.items():
        if key == "_idleHours":
            continue
        value = metab.get(key)
        if not isinstance(value, (int, float)):
            return False
        if isinstance(span, (list, tuple)) and len(span) == 2:
            if not (span[0] <= value <= span[1]):
                return False
        elif value < float(span):
            return False
    if "sleeping" in rule:
        if bool(rule["sleeping"]) != ((ch.get("state") or {}).get("active") == "sleep"):
            return False
    return True


def check(now=None):
    """跑一遍提醒。返回触发的事件 id 列表。

    每个规则记一个 armed 位：条件不成立时 arm，成立且 arm 着就触发并 disarm。
    """
    rules = load_rules()
    if not rules:
        return []
    now = time.time() if now is None else float(now)
    ch = timers.load_character()
    marks = _read(STATE, {}) or {}
    fired = []

    for rule in rules:
        rid = rule.get("id")
        if not rid:
            continue
        hit = _match(rule, ch, now)
        armed = marks.get(rid, True)
        if not hit:
            if not armed:
                marks[rid] = True          # 回到正常，重新上膛
            continue
        if not armed:
            continue
        text = str(rule.get("text") or "").strip()
        if text:
            timers.push_pending([text], now)
            fired.append(rid)
        marks[rid] = False

    _write(STATE, marks)
    return fired
