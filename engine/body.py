"""body 代理：主代理说「她要做什么」，它判定做不做得成、要多久、做完有什么变化。

和主代理共用同一个 key 和模型（判定是结构化输出，不需要文笔）。
realm 全在 `ConsciousnessRealm/body/`：`rule.md` 说它是谁，`judge.json` 放判定标准，
改标准不用动代码。

它是**同步**的：主代理在回合内调 `active` 工具 → 这里立刻判定 → 写计时器 → 返回回执。
到期结算和文本合并是另一条线（`engine/timers.py`），这里不参与。
"""

import json
import modelio
import paths as _paths

try:
    from trace import log
except ImportError:
    def log(*a, **k):
        pass
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

SHORT_LIMIT = 5.3        # short 槽的单件事上限（分钟），与 timers 的 SLOT_MINUTE_CAP 一致
MAX_MINUTES = 187.0      # long 槽上限，同上
METAB_KEYS = {"hp", "health", "bodyTemperature", "satiety", "hydration",
              "fatigue", "sleepiness", "sexualDesire"}
EMOTION_KEYS = {"emotion"}
# 单次判定的合理幅度，防止模型给出夸张数字
REWARD_LIMIT = {"bodyTemperature": 3, "hp": 60, "health": 40}
REWARD_LIMIT_DEFAULT = 60
EMOTION_LIMIT = 36


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
    """给 body 看的完整状态：与主代理同源，只是不省略正常项。"""
    return "\n".join(timers.render_state(ch, full=True))


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
    path = _paths.data("session", "session.jsonl")
    try:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError):
        return ""
    lines = []
    for r in rows[-40:]:
        role = r.get("role")
        if role == "user":
            text = (r.get("content") or "").strip()
            if text:
                lines.append(f"- 对方：{text.replace(chr(10), ' ')[:80]}")
            continue
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

def _body_thinking():
    """body 是否开思考：读 harness.body.thinking，默认关。"""
    import core
    try:
        return bool((core.harness.get("body") or {}).get("thinking", False))
    except Exception:                        # noqa: BLE001
        return False


SLOT_CAP = {"short": 5.3, "long": 187.0, "event": 463.0}   # 分钟


EFFORT_RATE = {"轻": 4.0, "中": 8.0, "重": 16.0}   # 每小时疲惫
EFFORT_DEFAULT = 8.0


def effort_rate(level):
    return EFFORT_RATE.get(str(level or "").strip(), EFFORT_DEFAULT)


def _body_block(ch, sleep=False):
    """按生理阶段判断还能不能动。返回拒绝理由，空串表示可以。

    这些是硬状态，不该交给模型猜：困到极限、累垮、身体很差。
    """
    if sleep:
        return ""
    metab = ch.get("metabolism") or {}
    # 困意不挡行动：困到极限由概率昏迷接管，不该逼她随便躺下。
    # 疲劳可以挡——闲着它自己会降。
    checks = (
        ("fatigue", "撑不住", "身上没力气了，动不了"),
    )
    for key, band, why in checks:
        name, _ = timers.stage_of(key, metab.get(key, 0))
        if name == band:
            return why
    return ""


def _reject_text(busy, fallback="", now=None):
    """拒绝的正文（不含「动作未受理：」前缀）。"""
    now = time.time() if now is None else float(now)
    if busy:
        entry = busy[0]
        at = float(entry.get("at") or 0)
        left = max(0.0, at - now)
        doing = str(entry.get("semantic") or entry.get("text") or "").strip().rstrip("。")
        ends = time.strftime("%H:%M", time.localtime(at or now))
        return (f"身体正忙着「{doing}」，还剩 {human_left_sec(left)}（{ends} 结束）。"
                f"想立刻改做别的就带 force；想等就以一句正文结束这一轮。")
    return fallback or "这会儿做不了。"


def _reject(busy=None, fallback="", now=None):
    """统一的拒绝回执。前缀只在这里加一次。"""
    return {"未执行": f"动作未受理：{_reject_text(busy or [], fallback, now)}",
            "ok": False, "allow": False}


OBSERVE_RULES = """

# 对方对她做了什么

读最近几条对话，只看**对方（user）**这一轮做了什么、说了什么，判断落多少数值。
她自己做了什么不在这里结算。

| 对方的行为 | 数值 |
|---|---|
| 打、推、掐、烫、拉拽 | hp −12 ~ −30，health −6 ~ −14，emotion −14 ~ −20 |
| 撕毁、抢走、弄坏她在意的东西 | emotion −14 ~ −20 |
| 当众羞辱、诬蔑、骂她 | emotion −10 ~ −16 |
| 冷落、敷衍、不信她 | emotion −4 ~ −8 |
| 真的帮了忙、带了东西、替她做了事 | emotion +6 ~ +10 |
| 示爱、珍视、久别重逢 | emotion +8 ~ +14 |
| 只是聊天、客套、问候、互相客气 | **不给任何字段** |
| 夸她手艺，说得具体、听得出来是真看过 | emotion +2 ~ +4 |

**话不算数，事才算数。** 一句好听的话不该比一次实际的帮忙更重。

输出 JSON：{"hp":0,"health":0,"emotion":0,"text":"一句话描述她此刻的身体/情绪反应"}
- 只给真正要变的字段；不变就省略。
- text 是给主代理看的第三人称陈述，20 字以内。
"""


def observe():
    """看对方最近一轮做了什么，把伤害或情绪落到她的状态上。只结算一次。"""
    import core
    rows = []
    path = _paths.data("session", "session.jsonl")
    try:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError):
        return None
    last_user = next((r for r in reversed(rows) if r.get("role") == "user"), None)
    if not last_user:
        return None
    body_text = str(last_user.get("content") or "")
    stamp = f"{len(rows)}:{hash(body_text) & 0xffff:x}"
    ch = timers.load_character()
    state = ch.setdefault("state", {})
    if stamp and state.get("lastObserved") == stamp:
        return None

    rule, judge_rules = load_realm()
    system = (f"{rule}\n\n{judge_rules}\n{OBSERVE_RULES}\n\n"
              f"## 最近的言行\n\n{recent_context(8)}\n\n"
              f"## 她现在的状态\n\n{state_snapshot(ch)}")
    payload = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": "据此判断对方这一轮对她造成了什么。"}],
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 600,
    }
    data = modelio.send(payload) or {}
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not content:
        log("observe.empty")
        return None
    verdict = _parse(content) or {}
    delta = {k: float(verdict[k]) for k in ("hp", "health", "emotion")
             if isinstance(verdict.get(k), (int, float)) and not isinstance(verdict.get(k), bool)}
    if delta:
        applied = timers._apply_reward(ch, delta)
        log("observe", applied=applied, text=str(verdict.get("text") or "")[:40])
    else:
        applied = {}
    if stamp:
        state["lastObserved"] = stamp
    timers._write(timers.CHARACTER, ch)
    return applied or None


def judge(description, ch=None, force=False, appeal=None, declared_slot=""):
    """让模型判一次。时长参考数据表，槽位由时长推导并检查容量。

    force=True 表示主代理要打断手上正在做的事：槽位占用不再是阻碍，
    但身体条件（累了、病了）照样能拒绝。
    """
    import core
    ch = ch if isinstance(ch, dict) else timers.load_character()
    rule, judge_rules = load_realm()
    system = (f"{rule}\n\n# 判定标准\n\n{judge_rules}\n\n"

              "输出 JSON：{\"allow\":true/false, \"reason\":\"做不了时的原因（allow 为 false 时必须写清，不能为空）\", "
              "\"duration\":分钟数, \"effort\":\"轻|中|重\", "
              "\"location\":\"只有她明显转移到新地点时才填（如「奥格尼斯科/她的房间」），"
              "原地的小动作一律留空字符串\", "
              "\"reward\":{\"satiety\":0,\"emotion\":0}, \"people\":[{\"name\":\"\",\"trust\":0}], \"sleeping\":false, \"text\":\"到点后显示的一句话\"}")
    appeal_text = ""
    if isinstance(appeal, dict) and appeal:
        appeal_text = (f"## 她的申辩\n\n理由：{appeal.get('reason')}；"
                       f"她认为只需 {appeal.get('minutes')} 分钟。\n"
                       f"申辩成立就按她的数给；不成立就按你的判断，并在 reason 里说明为什么。\n\n")

    user = (
        f"## 当前状态\n\n{state_snapshot(ch)}\n\n"

        f"## 地图\n\n{map_lines(ch)}\n\n"
        f"## 可参考的活动（时长来自数据表，别脱离这个量级）\n\n{_activity_lines(ch)}\n\n"
        f"## 最近的言行\n\n{recent_context()}\n\n"
        + appeal_text
        + f"## 她要做的事\n\n{description}\n\n"
        "时长优先取活动表里的值；表里没有的按常识给，但不要离谱。"
        "槽位由代码管，你不用判。"
    )
    if force:
        user += ("\n\n## 强制打断\n主代理要求打断她手上正在做的事，改做上面这件。"
                 "按新动作判定即可；身体条件不够仍然可以拒绝。")

    body = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 600,
        "temperature": 0.2,
        "thinking": {"type": "enabled" if _body_thinking() else "disabled"},
        "response_format": {"type": "json_object"},
    }
    response = modelio.send(body)
    if not response:
        return None
    content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    log("judge.raw", chars=len(content), head=content[:200])
    data = _parse(content)
    if not isinstance(data, dict):
        return None

    allow = bool(data.get("allow", False))
    sleeping = bool(data.get("sleeping", False))
    reason = str(data.get("reason") or "").strip()[:60]
    try:
        duration = float(data.get("duration") or 0)      # 不能取整，7 秒是 0.12 分钟
    except (TypeError, ValueError):
        duration = 0
    duration = max(1 / 60, min(MAX_MINUTES, float(duration)))   # 最少 1 秒，不再有“瞬间”

    # 秒级动作不占身；其余按时长归槽，模型声明的 slot 要与估算一致
    instant = False
    declared = str(declared_slot or data.get("slot") or "").strip().lower()
    slot = declared if declared in ("short", "long") else ("short" if duration <= SHORT_LIMIT else "long")
    if not instant and declared in ("short", "long"):
        limit = SLOT_CAP.get(declared, MAX_MINUTES)
        if duration > limit:
            allow = False
            reason = (f"预计 {duration} 分钟，超过 {declared} 的 {limit} 分钟上限；"
                      f"请改用 slot={'long' if declared == 'short' else 'long'}，"
                      f"确实要强制就带 force")

    log("judge", desc=description[:60], allow=allow, minutes=duration,
        force=force, instant=instant, reason=reason[:50],
        busy=len([t for t in timers.load_timers()
                  if t.get("kind") == "action" and float(t.get("at") or 0) > time.time()]))

    if duration > 60:                        # 判超过一小时，记一笔便于排查
        try:
            import json as _json
            long_log = _paths.data("tmp", "body_long.jsonl")
            long_log.parent.mkdir(parents=True, exist_ok=True)
            with long_log.open("a", encoding="utf-8") as handle:
                handle.write(_json.dumps({"ts": time.strftime("%H:%M:%S"),
                                          "minutes": duration,
                                          "allow": allow,
                                          "description": description},
                                         ensure_ascii=False) + "\n")
        except OSError:
            pass

    effort = str(data.get("effort") or "").strip()
    location = str(data.get("location") or "").strip()

    # 地图外：没有具体位置，行路时间按 30~90 分钟抽一个数
    world = load_world()
    known = {p.get("path") for p in world.get("places") or []} if world else set()
    if location and known and all(location not in k and not location.endswith(k) for k in known):
        duration = max(duration, random.randint(30, 90))
    text = str(data.get("text") or "").strip()

    return {
        "allow": allow,
        "reason": reason,
        "duration": duration,
        "instant": instant,
        "sleeping": sleeping,
        "effort": effort,
        "slot": slot,
        "slotId": timers.slot_id_for(slot),
        "location": location,
        "reward": _clean_reward(data.get("reward")),
        "people": data.get("people") if isinstance(data.get("people"), list) else [],
        "text": text,
        "raw": data,
    }


def human_left_sec(seconds):
    total = int(round(max(0.0, float(seconds))))
    if total < 60:
        return f"{total} 秒"
    return f"{total // 60} 分 {total % 60} 秒"


NEXT_STEP = ("接下来二选一：结束这一轮（说一句话，或什么都不说）等它做完；"
             "或直接递交下一步——但手上这件事没做完时，新动作会被拒，除非带 force 强行中断。")



def _alarm_minutes(text, now=None):
    """把闹钟解析成分钟数。支持 "06:15"、"1:06:15"（1 = 明天）。"""
    raw = str(text or "").strip().replace("：", ":")
    parts = [p for p in raw.split(":") if p != ""]
    if not parts:
        return None
    day = None
    if len(parts) == 3:                 # D:HH:MM
        try:
            day = int(parts[0])
            hour, minute = int(parts[1]), int(parts[2])
        except ValueError:
            return None
    elif len(parts) == 2:               # HH:MM
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            return None
    else:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    now = time.time() if now is None else float(now)
    stamp = time.localtime(now)
    target = time.mktime((stamp.tm_year, stamp.tm_mon, stamp.tm_mday,
                          hour, minute, 0, 0, 0, -1))
    if day is None:
        if target <= now:
            target += 24 * 3600         # 没写天：取下一个该时刻
    elif day == 0:
        if target < now:
            return None                 # 今天这个点已经过了，说明写法有误
    else:
        target += 24 * 3600 * max(1, day)
    return (target - now) / 60.0


def commit(description, slot="short", *, ch=None, force=False, appeal=None, sleep=None,
           line=None, location=None, emotion_break=False):
    """判定 + 建计时器，返回给主代理的回执。

    slot 参数只是主代理的建议，实际用哪个槽位由 body 按时长决定。
    """
    snap = timers.snapshot()             # 结算 + 时间 + 角色，一次取齐
    if emotion_break:
        ch_break = snap["ch"]
        ch_break.setdefault("state", {})["emotionBreakPending"] = True
        timers._write(timers.CHARACTER, ch_break)
    ch = ch if isinstance(ch, dict) else snap["ch"]
    now = snap["now"]
    known = str(slot or "").strip().lower() in ("short", "long", "event")
    target_slot = timers.slot_id_for(slot) if known else ""
    busy = [t for t in timers.blocking_entries(now)
            if known and (t.get("slotId") or timers.slot_id_for(t.get("slot"))) == target_slot]
    if busy and not force:
        if isinstance(appeal, dict) and appeal.get("reason"):
            target = busy[0]
            second = judge(str(target.get("text") or description), ch=ch, appeal=appeal)
            want = float((second or {}).get("duration") or 0)
            if second and want > 0:
                new_at = now + want * 60
                if new_at < float(target["at"]):
                    entries = timers.load_timers()
                    for item in entries:
                        if item.get("id") == target.get("id"):
                            item["at"] = new_at
                    timers.save_timers(entries)
                    return {"已调整": (f"已采纳申辩：{target.get('semantic') or ''} "
                                      f"改为约 {human_left_sec(want * 60)}后完成。"),
                            "ok": True, "allow": True}
        return _reject(busy, now=now)

    if (ch.get("state") or {}).get("active") == "sleep" and not sleep:
        return {"未执行": "动作未受理：她正在睡，叫醒之前做不了别的事。",
                "ok": False, "allow": False}

    blocked = _body_block(ch, sleep=bool(sleep))
    if blocked:
        return {"未执行": f"动作未受理：{blocked}。", "ok": False, "allow": False}

    verdict = judge(description, ch=ch, force=force, appeal=appeal,
                    declared_slot=slot)
    if verdict is not None:
        chosen = str(slot or "").strip().lower()
        if chosen not in ("short", "long", "event"):
            minutes = float(verdict.get("duration") or 0)
            chosen = "short" if minutes <= 5 else ("long" if minutes <= 180 else "event")
            log("slot.auto", minutes=round(minutes, 1), slot=chosen)
        verdict["slot"] = chosen
        verdict["slotId"] = timers.slot_id_for(verdict["slot"])
        cap = SLOT_CAP.get(verdict["slot"], 180.0)
        raw = float(verdict.get("duration") or 0)
        verdict["duration"] = min(raw, cap)
        if raw > cap:
            log("duration.capped", desc=description[:40], slot=verdict["slot"],
                was=round(raw, 1), cap=cap)
            verdict["capped_from"] = round(raw, 1)
    if verdict is not None and sleep:
        alarm = _alarm_minutes(sleep)
        if alarm:
            verdict["sleeping"] = True
            verdict["duration"] = min(alarm, 1440.0)
            log("alarm.set", at=str(sleep), minutes=round(alarm), desc=description[:32])
        elif str(sleep).strip().lower() in ("true", "1", "yes"):
            verdict["sleeping"] = True            # 只说“要睡”，时长交给 body
    if verdict is None:
        return {"ok": False, "reason": "判定失败：body 没有返回可解析的结果", "degraded": True}

    for who in (verdict.get("people") or []):
        if not isinstance(who, dict):
            continue
        name = str(who.get("name") or "").strip()
        if not name:
            continue
        trust = who.get("trust") if isinstance(who.get("trust"), (int, float)) else 0
        timers.touch_relation(ch, name, trust=float(trust))
        log("relation", name=name[:16], trust=round(float(trust), 1))
    if verdict.get("people"):
        timers._write(timers.CHARACTER, ch)

    if not verdict["allow"]:
        return _reject(None, verdict["reason"] or "她现在的状态做不了这件事")

    if not verdict["text"]:
        verdict["text"] = f"{description}（完成）"

    # 疲劳按这件事的时长给：活动才累，闲着不累
    reward = dict(verdict.get("reward") or {})
    reward.pop("sleepiness", None)      # 困意只由生理推进，模型不能直接加
    if verdict.get("sleeping"):
        # 睡眠期间生理由 advance 按小时推进，这里不要再叠奖励
        for key in ("fatigue", "sleepiness"):
            reward.pop(key, None)
    else:
        hours = float(verdict["duration"]) / 60.0
        reward["fatigue"] = round(hours * effort_rate(verdict.get("effort")), 2)
    verdict["reward"] = reward

    log("commit", desc=description[:60], minutes=verdict["duration"],
        slot=verdict["slot"], effort=verdict.get("effort"),
        sleeping=verdict.get("sleeping"))

    # 瞬时动作被判成几十上百分钏：记下来，供事后核查
    instant_words = ("走进", "推门", "坐下", "拉开", "拿起", "合上", "转身", "站住", "停步")
    if float(verdict["duration"]) >= 30 and any(w in description for w in instant_words):
        log("suspect_duration", desc=description[:60], minutes=verdict["duration"])

    if verdict.get("sleeping"):
        if (ch.get("state") or {}).get("active") == "sleep":
            return {"line": str(line or "").strip() or None,
                    "已执行": "她已经在睡了。", "ok": True}
        # 二次确认：第一次只告知后果，五分钟内再递一次才真的睡下
        now = time.time()
        state = ch.setdefault("state", {})
        asked = state.get("sleepAskedAt")
        if asked and now - float(asked) <= 300 and not force:
            state.pop("sleepAskedAt", None)
            timers._write(timers.CHARACTER, ch)
            timers.set_sleeping(True)
            return {"line": str(line or "").strip() or None,
                    "已执行": "动作已受理：她睡下了。醒来的时间由生理决定；要闹钟就另外设。",
                    "ok": True}
        if force:
            state.pop("sleepAskedAt", None)
            timers._write(timers.CHARACTER, ch)
            timers.set_sleeping(True)
            return {"line": str(line or "").strip() or None,
                    "已执行": "动作已受理：她睡下了。醒来的时间由生理决定；要闹钟就另外设。",
                    "ok": True}

        sleepy = float((ch.get("metabolism") or {}).get("sleepiness") or 0)
        health = float((ch.get("metabolism") or {}).get("health") or 100)
        expect_h = max(0.5, (sleepy - timers.SLEEP_REST_TO) / 7.0) * (1.0 + (100.0 - health) / 200.0)
        wake_at = time.strftime("%H:%M", time.localtime(now + expect_h * 3600))
        state["sleepAskedAt"] = now
        timers._write(timers.CHARACTER, ch)
        return {"line": str(line or "").strip() or None,
                "未执行": (f"要睡下吗？按现在的困意，大约 {wake_at} 就会自己醒，"
                          f"夜里可能还要醒几回。确定的话，五分钟内再递一次。"),
                "ok": False, "allow": False}
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
            location=(location or verdict["location"]),
            silent=verdict["duration"] <= 0,
            source="body",
        )
        if verdict.get("sleeping") and entry:
            entries = timers.load_timers()          # 标记为闹钟
            for item in entries:
                if item.get("id") == entry.get("id"):
                    item["alarm"] = True
            timers.save_timers(entries)
    except timers.SlotFull as exc:           # 闸门已查过，走到这里说明并发出错
        log("slot.race", desc=description[:40], error=str(exc)[:80])
        return {"ok": False, "allow": False,
                "未执行": _reject_text(timers.blocking_entries(), str(exc))
                .replace("动作未受理：", "", 1)}

    ends = time.strftime("%H:%M", time.localtime(float(entry["at"])))
    receipt = {
        "line": str(line or "").strip() or None,
        "已执行": (f"动作已受理：{description}，预计 {human_left_sec(verdict['duration'] * 60)}"
                   f"后完成（{ends}）。{NEXT_STEP}"
                   + (f"（原估 {verdict['capped_from']:.0f} 分钟，已按 {verdict['slot']} 上限压缩）"
                      if verdict.get("capped_from") else "")),
        "ok": True,
    }
    if cancelled:
        receipt["cancelled"] = [t.get("semantic") or t.get("text") for t in cancelled]
    return receipt
