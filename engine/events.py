"""事件钩子：地点事件在切换场景时判一次，全局事件按时间槽从对应池里抽。

判定链：选池 -> 过 requires -> 抽 target（带条件权重）-> 掷骰。
没中什么都不发生，这是常态。

条件：sleeping / busy / at / places / hours / metabolism / sinceUserHours
权重：{"base": 8, "if": [{"when": {"sinceUserHours": 4}, "value": 50}]}
"""

import fsio
import json
import paths as _paths
import os
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = _paths.data("realTime", "events.json")
WORLD = ROOT / "ConsciousnessRealm" / "world"

from engine import timers


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    fsio.write_json(path, data)


def active_world():
    name = _read(WORLD / "active.json", {}).get("world") or ""
    root = WORLD / name if name else None
    return root if root and root.exists() else None


def load_data():
    root = active_world()
    return _read(root / "events.json", {}) if root else {}


def note_message(now=None):
    """记一条消息的时间（不分角色），供静默时长用。"""
    seen = _read(STATE, {})
    seen = seen if isinstance(seen, dict) else {}
    seen["lastMessageAt"] = time.time() if now is None else float(now)
    _write(STATE, seen)




def mark_user(now=None):
    """真人开口时调用：记下最近一次对话时间，供事件条件用。"""
    now = time.time() if now is None else float(now)
    seen = _read(STATE, {}) or {}
    seen["lastUserAt"] = now
    _write(STATE, seen)


def in_hours(hour, span):
    try:
        start, end = int(span[0]), int(span[1])
    except (TypeError, ValueError, IndexError):
        return True
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def current_place_id(ch):
    """当前地点标识：优先用世界包里的 id，其次按环境表的键名匹配。"""
    root = active_world()
    local = str(ch.get("local") or "")
    if not local:
        return ""
    if root:
        places = _read(root / "places.json", {}).get("places") or []
        hit = next((p for p in places if p.get("path") == local), None)
        if hit:
            return hit.get("id")
    table = timers.places() or {}
    for name in table:
        if name in local:
            return name
    return local.rsplit("/", 1)[-1]


def build_ctx(ch, now, seen):
    last = seen.get("lastUserAt")
    hours = (now - float(last)) / 3600 if last else None
    running = [t for t in timers.load_timers() if float(t.get("at") or 0) > now]
    return {
        "at": current_place_id(ch),
        "sleeping": (ch.get("state") or {}).get("active") == "sleep",
        "busy": bool(running),
        "metabolism": ch.get("metabolism") or {},
        "sinceUserHours": hours,
    }


def when_ok(when, ctx):
    if "sleeping" in when and bool(when["sleeping"]) != ctx["sleeping"]:
        return False
    if "busy" in when and bool(when["busy"]) != ctx["busy"]:
        return False
    if "at" in when and str(when["at"]) != ctx["at"]:
        return False
    if "sinceUserHours" in when:
        limit = when["sinceUserHours"]
        have = ctx.get("sinceUserHours")
        if have is None:
            return False
        if isinstance(limit, (list, tuple)) and len(limit) == 2:
            if not (limit[0] <= have <= limit[1]):
                return False
        elif have < float(limit):
            return False
    for key, span in (when.get("metabolism") or {}).items():
        value = (ctx.get("metabolism") or {}).get(key)
        if isinstance(value, (int, float)) and isinstance(span, (list, tuple)) and len(span) == 2:
            if not (span[0] <= value <= span[1]):
                return False
    return True


def requires_ok(event, ctx, hour, fired=None):
    fired = fired or {}
    if event.get("once") and event.get("id") in fired:
        return False
    need = event.get("after") or []
    if isinstance(need, str):
        need = [need]
    if any(x not in fired for x in need):      # 前置没触发过，后续不出现
        return False
    if not in_hours(hour, event.get("hours") or [0, 24]):
        return False
    places = event.get("places") or []
    if places and ctx["at"] not in places:     # 地点限制对全局事件同样有效
        return False
    return when_ok(event.get("requires") or {}, ctx)


def weight_of(spec, ctx):
    if isinstance(spec, (int, float)):
        return float(spec)
    total = float((spec or {}).get("base") or 0)
    for rule in (spec or {}).get("if") or []:
        if when_ok(rule.get("when") or {}, ctx):
            return float(rule.get("value") or total)
    return total


def pick_target(group, ctx, rng):
    items = ((load_data().get("targets") or {}).get(group)) or []
    pool = [(it, weight_of(it.get("weight"), ctx)) for it in items]
    pool = [(it, w) for it, w in pool if w > 0]
    if not pool:
        return None
    point = rng.uniform(0, sum(w for _, w in pool))
    upto = 0.0
    for item, w in pool:
        upto += w
        if point <= upto:
            return item
    return pool[-1][0]


def pick_one(eligible, ratio, rng):
    """加权抽一件事。抽不中就返回 None——空权重固定为 ratio 倍事件权重和。"""
    if not eligible:
        return None
    total = sum(float(e.get("weight") or 1) for e in eligible)
    nothing = max(0.0, float(ratio)) * total
    point = rng.uniform(0, total + nothing)
    if point > total:
        return None
    upto = 0.0
    for event in eligible:
        upto += float(event.get("weight") or 1)
        if point <= upto:
            return event
    return None


def event_text(event, ctx):
    """事件文本。variants 里第一条条件成立的优先，否则用默认 text。"""
    for variant in event.get("variants") or []:
        if when_ok(variant.get("when") or {}, ctx):
            return str(variant.get("text") or "")
    return str(event.get("text") or "")


def roll_event(event, ctx, rng, now):
    text = event_text(event, ctx)
    group = event.get("targets")
    if group:
        target = pick_target(group, ctx, rng)
        if not target:
            return None
        text = text.replace("{target}", str(target.get("name") or ""))
    if text:
        timers.push_pending([text], now)
    return event.get("id")


def _reset_chain(event, fired_log):
    """后置事件触发后，把前置的完成状态清掉——链条可以再来一轮。

    默认清 after 列表；事件写了 reset 就按 reset 清（可以指定额外要恢复的 id）。
    自身也清掉，否则下一次前置触发了、后置却已经被 once 锁死。
    """
    event = event or {}
    targets = event.get("reset")
    if targets is None:
        targets = event.get("after") or []
    if isinstance(targets, str):
        targets = [targets]
    for ident in list(targets) + [event.get("id")]:
        fired_log.pop(ident, None)


def check(now=None, rng=None):
    now = time.time() if now is None else float(now)
    rng = rng or random
    data = load_data()
    events = {e["id"]: e for e in (data.get("events") or []) if e.get("id")}
    if not events:
        return {"fired": [], "woke": False}

    ch = timers.load_character()
    seen = _read(STATE, {})
    fired_log = seen.get("fired") if isinstance(seen.get("fired"), dict) else {}
    seen = seen if isinstance(seen, dict) else {}
    ctx = build_ctx(ch, now, seen)
    hour = time.localtime(now).tm_hour
    fired = []

    if ctx["at"] and ctx["at"] != seen.get("place"):
        pool = [e for e in events.values()
                if e.get("scope") == "location"
                and requires_ok(e, ctx, hour, fired_log)]
        hit = pick_one(pool, data.get("locationNothingRatio", 9), rng)
        if hit:
            got = roll_event(hit, ctx, rng, now)
            if got:
                fired.append(got)
                fired_log[got] = now
                _reset_chain(events[got], fired_log)
        seen["place"] = ctx["at"]

    if fired:                       # 这次切换场景已经有戏了，全局这回不抽
        seen["fired"] = fired_log
        _write(STATE, seen)
        return {"fired": fired, "woke": any(events[e].get("wake") for e in fired if e in events),
                "at": ctx["at"]}

    for name, pool in (data.get("pools") or {}).items():
        if not in_hours(hour, pool.get("hours") or [0, 24]):
            continue
        every = int(pool.get("every") or 120)
        # 每个池独立记槽：时段重叠时（凌晨同时属于 night 和 dawn）两个池都会判
        slot = f"{time.strftime('%Y-%m-%d', time.localtime(now))}-{int(now // (every * 60))}"
        key = f"slot:{name}"
        if slot == seen.get(key):
            continue
        seen[key] = slot
        eligible = [events[eid] for eid in (pool.get("events") or [])
                    if eid in events and events[eid].get("scope") == "global"
                    and requires_ok(events[eid], ctx, hour, fired_log)]
        hit = pick_one(eligible, pool.get("nothingRatio", 9), rng)
        if hit:
            got = roll_event(hit, ctx, rng, now)
            if got:
                fired.append(got)
                fired_log[got] = now
                _reset_chain(events[got], fired_log)

    seen["fired"] = fired_log
    _write(STATE, seen)
    woke = any(events[e].get("wake") for e in fired if e in events)
    return {"fired": fired, "woke": woke, "at": ctx["at"]}
