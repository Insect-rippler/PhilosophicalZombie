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

SHORT_LIMIT = 1          # short 槽的单件事上限（分钟）
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


def recent_context(limit=6):
    """最近几条言行，给 body 判断「这一步属于哪件事」。"""
    path = Path(__file__).resolve().parents[1] / "state" / "session" / "session.jsonl"
    try:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError):
        return ""
    lines = []
    for r in rows[-40:]:
        role = r.get("role")
        if role == "assistant":
            for c in (r.get("tool_calls") or []):
                f = c.get("function") or {}
                if f.get("name") != "active":
                    continue
                try:
                    a = json.loads(f.get("arguments") or "{}")
                except json.JSONDecodeError:
                    a = {}
                desc = str(a.get("description") or "").strip()
                if desc:
                    lines.append(f"- 做：{desc}")
            text = (r.get("content") or "").strip()
            if text:
                lines.append(f"- 说：{text.replace(chr(10), ' ')[:80]}")
    return "\n".join(lines[-limit:])

def judge(description, ch=None, force=False, appeal=None):
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
    appeal_text = ""
    if isinstance(appeal, dict) and appeal:
        appeal_text = (f"## 她的申辩\n\n理由：{appeal.get('reason')}；"
                       f"她认为只需 {appeal.get('minutes')} 分钟。\n"
                       f"申辩成立就按她的数给；不成立就按你的判断，并在 reason 里说明为什么。\n\n")

    user = (
        f"## 当前状态\n\n{state_snapshot(ch)}\n\n"
        f"## 槽位占用\n\n{slot_status(ch)}\n\n"
        f"## 地图\n\n{map_lines(ch)}\n\n"
        f"## 可参考的活动（时长来自数据表，别脱离这个量级）\n\n{_activity_lines(ch)}\n\n"
        f"## 最近的言行\n\n{recent_context()}\n\n"
        + appeal_text
        + f"## 她要做的事\n\n{description}\n\n"
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
        duration = float(data.get("duration") or 0)      # 不能取整，7 秒是 0.12 分钟
    except (TypeError, ValueError):
        duration = 0
    duration = max(1 / 60, min(MAX_MINUTES, float(duration)))   # 最少 1 秒，不再有“瞬间”

    # 秒级动作不占身；其余按时长归槽，模型声明的 slot 要与估算一致
    instant = False
    declared = str(data.get("slot") or "").strip().lower()
    slot = declared if declared in ("short", "long") else ("short" if duration <= SHORT_LIMIT else "long")
    if not instant and declared in ("short", "long"):
        limit = SHORT_LIMIT if declared == "short" else MAX_MINUTES
        if duration > limit:
            allow = False
            reason = (f"预计 {duration} 分钟，超过 {declared} 的 {limit} 分钟上限；"
                      f"请改用 slot={'long' if declared == 'short' else 'long'}，"
                      f"确实要强制就带 force")

    slot_id = timers.slot_id_for()

    location = str(data.get("location") or "").strip()

    # 地图外：没有具体位置，行路时间按 30~90 分钟抽一个数
    world = load_world()
    known = {p.get("path") for p in world.get("places") or []} if world else set()
    if location and known and all(location not in k and not location.endswith(k) for k in known):
        duration = max(duration, random.randint(30, 90))
    text = str(data.get("text") or "").strip()

    # 事件槽联动：正在做一件大事时，另起炉灶要先说清代价
    if allow and not force and slot != "event":
        running = [t for t in timers.load_timers()
                   if t.get("slotId") == "event" and float(t.get("at") or 0) > time.time()]
        if running and duration >= 10:
            doing = str(running[0].get("semantic") or running[0].get("text") or "").lstrip("她")
            left = max(1, int((float(running[0]["at"]) - time.time()) // 3600))
            allow = False
            reason = (f"她正忙着：{doing}，还要约 {left} 小时。"
                      f"现在做这个会打断那件事，已经做的那段就作废了。"
                      f"确实要改主意，就带 force 重递")

    if allow and not force and not instant:
        ok, why = timers.can_take(slot_id)
        if not ok:
            busy = [t for t in timers.load_timers()
                    if t.get("kind") == "action" and float(t.get("at") or 0) > time.time()]
            doing = str(busy[0].get("semantic") or "").lstrip("她") if busy else ""
            left_sec = 0.0
            elapsed_sec = 0.0
            if busy:
                left_sec = max(0.0, float(busy[0].get("at") or 0) - time.time())
                started = float(busy[0].get("createdAt") or busy[0].get("at") or 0)
                if started:
                    elapsed_sec = max(0.0, time.time() - started)
            allow = False
            if doing:
                reason = (
                    f"动作未受理：她手上的事还没完（{doing}），"
                    f"已进行 {human_left_sec(elapsed_sec)}，还剩 {human_left_sec(left_sec)}。"
                    f"当前正执行的动作结束前，非 force 模式下不会接受任何（short/long）类型动作；"
                    f"若剩余时间小于 3s 且角色有必要执行新动作则可尝试递交新动作，"
                    f"否则以一句话收尾，或以 force 模式强制结束当前动作以执行有必要的新动作。"
                )
            else:
                reason = f"动作未受理：槽位被占着（{why}）。等它结束，或用 force 替换"

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


def human_left_sec(seconds):
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(round(seconds))} 秒"
    return f"{int(seconds // 60)} 分 {int(round(seconds % 60))} 秒"


NEXT_STEP = ("接下来可以做两件事之一：结束这一轮（一句话收尾，或什么都不说），"
             "或者递交下一步动作。")


def human_left(minutes):
    total = int(round(float(minutes) * 60))
    if total < 60:
        return f"{total} 秒"
    return f"{total // 60} 分 {total % 60} 秒"

def commit(description, slot="short", *, ch=None, force=False, appeal=None):
    """判定 + 建计时器，返回给主代理的回执。

    slot 参数只是主代理的建议，实际用哪个槽位由 body 按时长决定。
    """
    ch = ch if isinstance(ch, dict) else timers.load_character()
    verdict = judge(description, ch=ch, force=force, appeal=appeal)
    if verdict is None:
        return {"ok": False, "reason": "判定失败：body 没有返回可解析的结果", "degraded": True}

    if not verdict["allow"]:
        if isinstance(appeal, dict) and appeal.get("reason"):
            busy = [t for t in timers.load_timers()
                    if t.get("kind") == "action" and float(t.get("at") or 0) > time.time()]
            if busy:
                target = busy[0]
                second = judge(str(target.get("text") or description), ch=ch,
                               appeal=appeal)
                want = float((second or {}).get("duration") or 0)
                if second and want > 0:
                    new_at = time.time() + want * 60
                    if new_at < float(target["at"]):
                        entries = timers.load_timers()
                        for e in entries:
                            if e is target or e.get("id") == target.get("id"):
                                e["at"] = new_at
                        timers.save_timers(entries)
                        return {"已调整": (f"已采纳申辩：{target.get('semantic') or ''} "
                                          f"改为约 {human_left_sec(want * 60)}后完成。"),
                                "ok": True, "allow": True}
        return {"未执行": f"递交未受理：{verdict['reason'] or '身体条件不允许'}",
                "ok": False, "allow": False,
                "reason": verdict["reason"] or "身体条件不允许"}

    if not verdict["text"]:
        verdict["text"] = f"{description}（完成）"

    # 疲劳按这件事的时长给：活动才累，闲着不累
    reward = dict(verdict.get("reward") or {})
    reward["fatigue"] = round(float(verdict["duration"]) * 0.05, 2)   # 只按时长，不采纳模型自估
    verdict["reward"] = reward

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
        "已执行": (f"动作已受理：{description}，预计 {human_left_sec(verdict['duration'] * 60)}"
                   f"后完成（{ends}）。{NEXT_STEP}"),
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
