"""随机事件：每个 tick 掷一次 1%，触发后生成一条事件 plan。

旧的地点事件池已经废弃；现在事件不靠事件表，而是按当前状态现场生成。
事件写进 plan/agenda.json，带 kind=event 和 event 字段，生命周期由这里的
settle_plans() 结算：完成发奖励，超时发惩罚。
"""

import json
import random
import time
from pathlib import Path

import paths as _paths
import runlog
import timers

STATE = _paths.data("realTime", "events.json")
AGENDA = _paths.realm("plan", "agenda.json")
CHANCE = 0.01
COOLDOWN = 1800
MIN_LIMIT = 900
MAX_LIMIT = 86400


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_name(Path(path).name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def note_message(now=None):
    """记一条消息的时间，供静默时长用。"""
    seen = _read(STATE, {})
    seen = seen if isinstance(seen, dict) else {}
    seen["lastMessageAt"] = time.time() if now is None else float(now)
    _write(STATE, seen)


def mark_user(now=None):
    seen = _read(STATE, {})
    seen = seen if isinstance(seen, dict) else {}
    seen["lastUserAt"] = time.time() if now is None else float(now)
    _write(STATE, seen)


def _parse_json(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _effect(value):
    if not isinstance(value, dict):
        return {"metabolism": {}, "items": []}
    metab = value.get("metabolism") if isinstance(value.get("metabolism"), dict) else {}
    out = {k: v for k, v in metab.items()
           if isinstance(v, (int, float)) and not isinstance(v, bool)}
    emo = value.get("emotion")
    if isinstance(emo, (int, float)) and not isinstance(emo, bool):
        out["emotion"] = float(emo)
    items = [str(x)[:60] for x in (value.get("items") or []) if str(x).strip()][:3]
    return {"metabolism": out, "items": items}


def _apply_effect(ch, effect):
    flat = {key: float(value) for key, value in (effect.get("metabolism") or {}).items()}
    timers._apply_reward(ch, flat)
    for item in effect.get("items") or []:
        ch.setdefault("items", []).append(item)
    return flat


def _open_event():
    data = _read(AGENDA, {})
    for goal in data.get("goal") or []:
        if not isinstance(goal, dict) or goal.get("kind") != "event":
            continue
        event = goal.get("event") or {}
        if goal.get("status") == "active" and event.get("state") == "open":
            return goal
    return None


def _context():
    import body
    import environment
    import worldmap
    ch = timers.load_character()
    local = str(ch.get("local") or "")
    risk = worldmap.risk_of(local)
    env = environment.snapshot()
    state = "\n".join(timers.render_state(ch))
    recent = body.recent_context(10)
    plans = []
    events = []
    data = _read(AGENDA, {})
    for goal in data.get("goal") or []:
        if not isinstance(goal, dict):
            continue
        if goal.get("status") in ("active", "paused"):
            plans.append(f"[{goal.get('kind') or 'self'}] {goal.get('objective')}")
        if goal.get("kind") == "event":
            events.append(str(goal.get("objective") or "").strip())
    return {
        "state": state,
        "recent": recent,
        "local": local,
        "risk": risk,
        "weather": env.get("condition"),
        "temperature": env.get("temperatureC"),
        "season": env.get("season"),
        "plans": plans,
        "events": events[-8:],
    }


def _generate():
    import core
    import modelio
    ctx = _context()
    weather = f"{ctx['weather']}，{ctx['temperature']}°C，{ctx['season']}"
    system = (
        "你是世界事件生成器。根据角色当前状态、最近经历、地点、天气与风险，"
        "生成一个会打断她日常的事件。\n"
        "只输出 JSON：{\"title\":\"一句标题\", \"scene\":\"场景描述\", "
        "\"opening\":\"她遭遇时看到/听到的第一幕\", \"condition\":\"完成条件\", "
        "\"limitSeconds\":900~86400, "
        "\"reward\":{\"metabolism\":{\"fatigue\":0},\"emotion\":0,\"items\":[\"剧情物品\"]}, "
        "\"penalty\":{\"metabolism\":{\"fatigue\":0},\"emotion\":0}}。\n"
        "奖励惩罚只用变化量；不要写数值上限、不要写下一步指令；事件必须现在就能发生。\n"
        "最近 24 小时已经出现过的事件，绝对不要复用其触发方式、核心意象、道具或解决方式；"
        "不要只换地点、换措辞。必须换一种完全不同的触发和冲突。"
    )
    user = (
        f"## 当前状态\n{ctx['state']}\n\n"
        f"## 最近言行\n{ctx['recent'] or '（无）'}\n\n"
        f"## 地点与风险\n{ctx['local']}（风险 {ctx['risk']}）\n\n"
        f"## 环境\n{weather}\n\n"
        "## 已有计划（不要重复）\n" + ("\n".join(ctx["plans"]) or "（无）") +
        "\n\n## 最近事件（这些类型、意象、道具都不要重复）\n" +
        ("\n".join(f"- {x}" for x in ctx["events"]) or "（无）")
    )
    body = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 900,
        "temperature": 0.8,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    response = modelio.send(body)
    content = ((response or {}).get("choices") or [{}])[0].get("message", {}).get("content")
    return _parse_json(content)


def _build_goal(data):
    title = str(data.get("title") or "").strip()[:60]
    scene = str(data.get("scene") or "").strip()[:300]
    opening = str(data.get("opening") or "").strip()[:300]
    condition = str(data.get("condition") or "").strip()[:200]
    if not title or not condition:
        return None
    try:
        limit = int(data.get("limitSeconds") or 0)
    except (TypeError, ValueError):
        limit = 0
    limit = max(MIN_LIMIT, min(MAX_LIMIT, limit or 3600))
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    deadline = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now + limit))
    gid = f"ev-{int(now) % 10**8:08d}"
    return {
        "id": gid,
        "kind": "event",
        "objective": title,
        "status": "active",
        "createdAt": stamp,
        "updatedAt": stamp,
        "steps": [],
        "event": {
            "scene": scene,
            "opening": opening,
            "condition": condition,
            "limitSeconds": limit,
            "deadlineAt": deadline,
            "reward": _effect(data.get("reward")),
            "penalty": _effect(data.get("penalty")),
            "state": "open",
        },
    }


def _append_goal(goal):
    data = _read(AGENDA, {})
    data.setdefault("goal", []).append(goal)
    _write(AGENDA, data)
    timers.push_pending([f"【事件】{goal['objective']}。{goal['event']['opening']}"])


def maybe_trigger(now=None):
    now = time.time() if now is None else float(now)
    ch = timers.load_character()
    if (ch.get("state") or {}).get("active") == "sleep":
        return False
    if _open_event() is not None:
        return False
    marks = _read(STATE, {})
    last = float(marks.get("lastEventAt") or 0)
    if now - last < COOLDOWN:
        return False
    if random.random() >= CHANCE:
        return False
    marks["lastEventAt"] = now
    _write(STATE, marks)
    data = _generate()
    if not isinstance(data, dict):
        runlog.log("event.failed", reason="没有可解析的事件 JSON")
        return False
    goal = _build_goal(data)
    if goal is None:
        runlog.log("event.failed", reason="事件字段不全")
        return False
    _append_goal(goal)
    runlog.log("event.created", id=goal["id"], title=goal["objective"],
               limit=goal["event"]["limitSeconds"])
    return True


def _settle_plans(now=None):
    now = time.time() if now is None else float(now)
    data = _read(AGENDA, {})
    changed = False
    ch = None
    for goal in data.get("goal") or []:
        if not isinstance(goal, dict) or goal.get("kind") != "event":
            continue
        event = goal.setdefault("event", {})
        state = event.get("state")
        if state == "open":
            try:
                deadline = time.mktime(time.strptime(str(event.get("deadlineAt")),
                                                     "%Y-%m-%d %H:%M:%S"))
            except (TypeError, ValueError):
                deadline = 0
            if deadline and now >= deadline:
                if ch is None:
                    ch = timers.load_character()
                _apply_effect(ch, event.get("penalty") or {})
                event["state"] = "failed"
                event["resolvedAt"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                goal["status"] = "failed"
                goal["updatedAt"] = event["resolvedAt"]
                timers.push_pending([f"【事件失败】{goal.get('objective')}。时间到了，惩罚已经落下。"])
                changed = True
        elif state == "done":
            if ch is None:
                ch = timers.load_character()
            _apply_effect(ch, event.get("reward") or {})
            event["state"] = "rewarded"
            event["resolvedAt"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            timers.push_pending([f"【事件完成】{goal.get('objective')}。奖励已经落下。"])
            changed = True
        elif state == "abandoned":
            if ch is None:
                ch = timers.load_character()
            _apply_effect(ch, event.get("penalty") or {})
            event["state"] = "failed"
            event["resolvedAt"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            timers.push_pending([f"【事件失败】{goal.get('objective')}。她放弃了，惩罚已经落下。"])
            changed = True
    if changed:
        _write(AGENDA, data)
        if ch is not None:
            timers._write(timers.CHARACTER, ch)


def check(now=None):
    """每个 tick 调一次：先结算到期事件，再掷一次新事件。"""
    _settle_plans(now)
    return maybe_trigger(now)
