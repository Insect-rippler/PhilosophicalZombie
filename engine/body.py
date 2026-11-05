"""body 代理：主代理说「她要做什么」，它判定做不做得成、要多久、做完有什么变化。

和主代理共用同一个 key 和模型（判定是结构化输出，不需要文笔）。
realm 全在 `ConsciousnessRealm/body/`：`rule.md` 说它是谁，`judge.json` 放判定标准，
改标准不用动代码。

它是**同步**的：主代理在回合内调 `active` 工具 → 这里立刻判定 → 写计时器 → 返回回执。
到期结算和文本合并是另一条线（`engine/timers.py`），这里不参与。
"""

import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REALM = ROOT / "ConsciousnessRealm" / "body"
RULE = REALM / "rule.md"
JUDGE = REALM / "judge.json"

from engine import timers  # noqa: E402

MAX_MINUTES = 720
METAB_KEYS = {"hp", "health", "bodyTemperature", "satiety", "hydration",
              "fatigue", "sleepiness", "sexualDesire"}
EMOTION_KEYS = {"favor", "trust", "possessiveness", "unease"}
# 单次判定的合理幅度，防止模型给出夸张数字
REWARD_LIMIT = {"bodyTemperature": 3, "hp": 60, "health": 40}
REWARD_LIMIT_DEFAULT = 60
EMOTION_LIMIT = 12


WORLD_DIR = REALM.parent / "world"
ACTIVE = WORLD_DIR / "active.json"


def load_world():
    """当前世界的地图与活动表。换世界只改 active.json。"""
    try:
        name = json.loads(ACTIVE.read_text(encoding="utf-8")).get("world") or ""
    except (OSError, json.JSONDecodeError):
        name = ""
    root = WORLD_DIR / name if name else None
    if not root or not root.exists():
        return {"name": "", "places": [], "activities": []}
    try:
        places = json.loads((root / "places.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        places = {}
    try:
        acts = json.loads((root / "activities.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        acts = {}
    return {"name": places.get("name") or name,
            "places": places.get("places") or [],
            "activities": acts.get("activities") or []}


def map_lines(ch):
    """当前位置和能走的地方，带步行分钟数。"""
    world = load_world()
    if not world["places"]:
        return "（地图为空）"
    local = str(ch.get("local") or "")
    here = next((p for p in world["places"] if local and local.endswith(p.get("path", ""))), None)
    if not here:
        return "（她不在已知地点）" + "；".join(p.get("path", "") for p in world["places"])
    by_id = {p["id"]: p for p in world["places"]}
    out = [f"当前位置：{here.get('path')}"]
    for link in here.get("links") or []:
        target = by_id.get(link.get("to"))
        if target:
            out.append(f"- {target.get('path')}：步行 {link.get('minutes')} 分钟")
    return "\n".join(out)


def load_activities():
    return [a for a in load_world()["activities"] if isinstance(a, dict)]


def in_hours(hour, span):
    try:
        start, end = int(span[0]), int(span[1])
    except (TypeError, ValueError, IndexError):
        return True
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end           # 跨夜


def available_activities(ch, limit=6):
    """按现在几点、她在哪筛出可参考的活动。"""
    hour = time.localtime().tm_hour
    local = str(ch.get("local") or "")
    rows = [a for a in load_activities() if in_hours(hour, a.get("hours") or [0, 24])]
    rows.sort(key=lambda a: 0 if any(local.startswith(p) for p in (a.get("place") or [])) else 1)
    return rows[:limit]


def slot_status(ch):
    """给模型看的槽位占用情况。"""
    now = time.time()
    lines = []
    for sid, label in (("active.short", "短动作"), ("active.long", "长动作")):
        running = [t for t in timers.load_timers()
                   if t.get("kind") == "action"
                   and (t.get("slotId") or timers.slot_id_for(t.get("slot"))) == sid
                   and float(t.get("at") or 0) > now]
        if running:
            t = running[0]
            left = max(0, (float(t.get("at") or 0) - now) / 60)
            lines.append(f"{label}：占用中（正{('在' + str(t.get('semantic') or '')) if t.get('semantic') else '在做别的事'}，还有 {left:.0f} 分钟）")
        else:
            lines.append(f"{label}：空闲")
    return "\n".join(lines)


def load_realm():
    rule = RULE.read_text(encoding="utf-8") if RULE.exists() else ""
    sections = []
    if JUDGE.exists():
        try:
            data = json.loads(JUDGE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        for sec in (data.get("sections") or {}).values():
            if isinstance(sec, dict) and sec.get("content"):
                sections.append(str(sec["content"]).strip())
    return rule.strip(), "\n\n".join(sections)


def state_snapshot(ch=None):
    """给 body 看的当前状态。这里给数字，它需要精确判断。"""
    ch = ch if isinstance(ch, dict) else timers.load_character()
    metab = ch.get("metabolism") or {}
    emo = ch.get("emotion") or {}
    state = ch.get("state") or {}
    lines = [
        f"时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"位置：{ch.get('local') or '未知'}",
        f"睡眠状态：{'睡着' if state.get('active') == 'sleep' else '醒着'}",
        "生理：" + "、".join(f"{k}={v}" for k, v in metab.items() if isinstance(v, (int, float))),
        "情绪：" + "、".join(f"{k}={v}" for k, v in emo.items() if isinstance(v, (int, float))),
    ]
    recent = [t for t in timers.load_timers() if t.get("kind") == "action"]
    if recent:
        lines.append("正在进行的计时器：" + "；".join(
            f"{t.get('semantic', '')}（{max(0, (float(t.get('at') or 0) - time.time()) / 60):.0f} 分钟后完成）"
            for t in recent[:3]))
    return "\n".join(lines)


def _parse(text):
    if not text:
        return None
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _clean_reward(raw):
    out = {}
    for key, value in (raw or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if key in METAB_KEYS:
            limit = REWARD_LIMIT.get(key, REWARD_LIMIT_DEFAULT)
            out[key] = round(max(-limit, min(limit, float(value))), 1)
        elif key in EMOTION_KEYS:
            out[key] = round(max(-EMOTION_LIMIT, min(EMOTION_LIMIT, float(value))), 1)
    return out


def _activity_lines(ch):
    rows = available_activities(ch)
    if not rows:
        return "（此刻没有可参考的活动）"
    out = []
    for a in rows:
        place = "、".join(a.get("place") or [])
        out.append(f"- {a.get('title', a.get('key'))}：约 {a.get('duration')} 分钟（{place}）")
    return "\n".join(out)


def judge(description, ch=None, force=False):
    """让模型判一次。时长参考数据表，槽位由时长推导并检查容量。

    force=True 表示主代理要打断手上正在做的事：槽位占用不再是阻碍，
    但身体条件（累了、病了）照样能拒绝。
    """
    import core
    ch = ch if isinstance(ch, dict) else timers.load_character()
    rule, judge_rules = load_realm()
    system = (f"{rule}\n\n# 判定标准\n\n{judge_rules}\n\n"
              "输出 JSON：{\"allow\":true/false, \"reason\":\"做不了时的原因\", "
              "\"duration\":分钟数, \"location\":\"做完之后的位置，没动就省略\", "
              "\"reward\":{\"satiety\":0}, \"text\":\"到点后显示的一句话\"}")
    user = (
        f"## 当前状态\n\n{state_snapshot(ch)}\n\n"
        f"## 槽位占用\n\n{slot_status(ch)}\n\n"
        f"## 地图\n\n{map_lines(ch)}\n\n"
        f"## 可参考的活动（时长来自数据表，别脱离这个量级）\n\n{_activity_lines(ch)}\n\n"
        f"## 她要做的事\n\n{description}\n\n"
        "时长优先取活动表里的值；表里没有的按常识给，但不要离谱。"
        "槽位已满时，只有在一分钟内能做完的事才允许继续，其余一律拒绝。"
    )
    if force:
        user += ("\n\n## 强制打断\n主代理要求打断她手上正在做的事，改做上面这件。"
                 "槽位占用不算阻碍，按新动作判定即可；身体条件不够仍然可以拒绝。")

    body = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 600,
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    response = core.send(body)
    if not response:
        return None
    content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _parse(content)
    if not isinstance(data, dict):
        return None

    allow = bool(data.get("allow", False))
    reason = str(data.get("reason") or "").strip()[:60]
    try:
        duration = int(float(data.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    duration = max(0, min(MAX_MINUTES, duration))

    # 几秒内做完的事不占身：直接生效，不建计时器
    # 地图外：没有具体位置，行路时间按 30~90 分钟抽一个数
    world = load_world()
    known = {p.get("path") for p in world.get("places") or []} if world else set()
    if location and known and all(location not in k and not location.endswith(k) for k in known):
        duration = max(duration, random.randint(30, 90))

    instant = duration * 60 <= timers.INSTANT_SECONDS
    slot = "instant" if instant else "body"
    slot_id = timers.slot_id_for()

    location = str(data.get("location") or "").strip()
    text = str(data.get("text") or "").strip()

    if allow and not force and not instant:
        ok, why = timers.can_take(slot_id)
        if not ok:
            busy = [t for t in timers.load_timers()
                    if t.get("kind") == "action" and float(t.get("at") or 0) > time.time()]
            doing = str(busy[0].get("semantic") or "").lstrip("她") if busy else ""
            allow = False
            reason = f"她手上的事还没完（{doing}），做不了这个" if doing else why

    return {
        "allow": allow,
        "reason": reason,
        "duration": duration,
        "instant": instant,
        "slot": slot,
        "slotId": slot_id,
        "location": location,
        "reward": _clean_reward(data.get("reward")),
        "text": text,
        "raw": data,
    }


def commit(description, slot="short", *, ch=None, force=False):
    """判定 + 建计时器，返回给主代理的回执。

    slot 参数只是主代理的建议，实际用哪个槽位由 body 按时长决定。
    """
    ch = ch if isinstance(ch, dict) else timers.load_character()
    verdict = judge(description, ch=ch, force=force)
    if verdict is None:
        return {"ok": False, "reason": "判定失败：body 没有返回可解析的结果", "degraded": True}

    if not verdict["allow"]:
        return {"ok": False, "allow": False,
                "reason": verdict["reason"] or "身体条件不允许"}

    if not verdict["text"]:
        verdict["text"] = f"{description}（完成）"

    if verdict.get("instant"):
        # 秒级动作：立刻落位置/数值，不进槽、不排队
        applied = timers.apply_instant(
            verdict["text"] or description,
            reward=verdict["reward"],
            location=verdict["location"],
        )
        return {"ok": True, "allow": True, "instant": True, "slot": "none",
                "applied": applied.get("applied") or {},
                "location": applied.get("location") or None,
                "text": verdict["text"]}

    cancelled = []
    if force:                       # 打断：先把槽位里没做完的那件撤掉
        cancelled = timers.cancel(verdict["slotId"])

    try:
        entry = timers.add(
            kind="action",
            text=verdict["text"],
            minutes=verdict["duration"],
            semantic=description[:30],
            slot=verdict["slot"],
            reward=verdict["reward"],
            location=verdict["location"],
            silent=verdict["duration"] <= 0,
            source="body",
        )
    except timers.SlotFull as e:
        return {"ok": False, "allow": False, "reason": str(e)}

    ends = time.strftime("%H:%M", time.localtime(float(entry["at"])))
    return {
        "ok": True,
        "allow": True,
        "slot": verdict["slot"],
        "duration": verdict["duration"],
        "endsAt": ends,
        "location": verdict["location"] or None,
        "text": verdict["text"],
        "timer": entry["id"],
        "cancelled": [t.get("semantic") or t.get("text") for t in cancelled],
    }
