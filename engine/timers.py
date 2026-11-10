"""计时器：body 建，代码跑。到点只做三件事——落数值、落位置、把到期文本倒进缓存池。

两个阶段是分开的：

    第一部分（同步，在回合内）
        主代理提交行动 → body 判定 → body 写好计时器（含到期文本）→ 返回回执
    第二部分（异步，纯代码）
        计时器自己跑 → 到点把文本写进 state 缓存池 → 主代理下一轮唤醒时取走

第二部分完全不碰模型：到期文本是 body 建计时器时就写好的，
所以这里只做搬运和合并，不生成一个字的自然语言。

计时器条目（body 写入）：

    {"id": "t-0007", "at": 1762345678.0, "kind": "action" | "event",
     "semantic": "她在厨房烧水煮面",        # 给日志看的一句话
     "text": "面煮好了，锅还在灶上。",       # 到期后进上下文的文本（body 写）
     "slot": "short" | "long", "chance": 1000,
     "reward": {"satiety": 26}, "location": "罗德岛/厨房",
     "state": {"active": "sleep"}, "silent": false, "source": "body"}
"""

import fsio
import json
import paths as _paths
import os

try:
    from trace import log
except ImportError:
    def log(*a, **k):
        pass
import os
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = _paths.data("realTime")
TIMERS = STATE_DIR / "timers.json"
CHARACTER = STATE_DIR / "character.json"
PENDING = STATE_DIR / "pending.json"

# 生理指标与取值范围
METABOLISM_RANGE_DEFAULT = {
    "satiety": (0.0, 100.0),
    "hydration": (0.0, 100.0),
    "fatigue": (0.0, 200.0),
    "sleepiness": (0.0, 100.0),
    "hp": (0.0, 100.0),
    "health": (0.0, 100.0),
    "bodyTemperature": (34.0, 42.0),
}


def metabolism_range():
    """生理取值范围：优先读配置，缺的用默认。"""
    out = dict(METABOLISM_RANGE_DEFAULT)
    for key, pair in (harness_metabolism().get("ranges") or {}).items():
        if key == "emotion":
            continue                     # 情绪走单独的锁与范围
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            out[key] = (float(pair[0]), float(pair[1]))
    return out
EMOTION_RANGE = {"emotion": (0.0, 240.0)}
EMOTION_CENTER = 120.0
EMOTION_LOCK_LOW = 30.0        # 再往下就进「低落」，先锁住
EMOTION_LOCK_HIGH = 200.0      # 再往上就进「昂扬」，先锁住
EMOTION_LOCK_COOLDOWN = 6      # 越界后静默的轮数
EMOTION_DRIFT_BASE = 1.5       # 每小时回归基准（点）
EMOTION_DRIFT = {"低落": 1.0, "戒备": 1.5, "正常": 0.4,
                 "高涨": 1.5, "昂扬": 1.0}


# ---------------- 读写 ----------------

def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    fsio.write_json(path, data)


def load_timers():
    data = _read(TIMERS, [])
    return data if isinstance(data, list) else []


def save_timers(timers):
    _write(TIMERS, timers)


def default_character():
    return {
        "metabolism": {"hp": 100.0, "health": 100.0, "bodyTemperature": 36.5,
                       "satiety": 80.0, "hydration": 70.0, "fatigue": 10.0,
                       "sleepiness": 10.0, "sexualDesire": 10.0},
        "emotion": {"emotion": 120.0},
        "state": {"active": "wake", "since": time.strftime("%Y-%m-%d %H:%M")},
        "local": "",
        "lastUpdate": time.time(),
    }


def load_character():
    ch = _read(CHARACTER, None)
    if not isinstance(ch, dict):
        ch = default_character()
        _write(CHARACTER, ch)
    for key, value in default_character().items():
        ch.setdefault(key, value)
    return ch


def save_character(ch):
    # 时间基准由 advance 负责，这里不能覆盖，否则生理推进会隔轮才生效
    _write(CHARACTER, ch)


def cancel(slot_id=None, now=None):
    """撤掉指定槽位上还没到期的计时器（被打断的动作）。

    被打断就不给奖励——只有等到期结算的条目才拿得到，
    所以这里直接删掉，不结算、不留文本。
    """
    now = time.time() if now is None else float(now)
    timers = load_timers()
    keep, removed = [], []
    for entry in timers:
        sid = entry.get("slotId") or (slot_id_for(entry.get("slot"))
                                      if entry.get("kind") == "action" else None)
        if sid == slot_id and float(entry.get("at") or 0) > now:
            removed.append(entry)
        else:
            keep.append(entry)
    if removed:
        save_timers(keep)
    return removed


# ---------------- 槽位（容量由数据决定） ----------------

WORLD_DIR = ROOT / "ConsciousnessRealm" / "world"
SLOTS_PATH = WORLD_DIR / "slots.json"

DEFAULT_SLOTS = [
    {"id": "active", "kind": "action", "capacity": 1, "label": "身体"},
    {"id": "event.random", "kind": "event", "capacity": 1, "label": "随机事件"},
]


class SlotFull(RuntimeError):
    pass


def load_slots():
    data = _read(SLOTS_PATH, None)
    slots = (data or {}).get("slots") if isinstance(data, dict) else None
    if not isinstance(slots, list) or not slots:
        slots = DEFAULT_SLOTS
    return {s["id"]: s for s in slots if isinstance(s, dict) and s.get("id")}


INSTANT_SECONDS = 30        # 这个时长以内、且不移动的动作，不进槽、不建计时器


def slot_id_for(slot=None):
    """short / long / event 各占一个独立槽位。"""
    name = str(slot or "").strip()
    return name if name in ("short", "long", "event") else "short"


def occupancy(now=None):
    """各槽位当前占用的数量。只算还没到期的计时器。"""
    now = time.time() if now is None else float(now)
    counts = {}
    for entry in load_timers():
        sid = entry.get("slotId")
        if not sid and entry.get("kind") == "action":
            sid = slot_id_for(entry.get("slot"))
        if not sid:
            continue
        if float(entry.get("at") or 0) > now + 3:      # 只剩 3 秒内的不算占用
            counts[sid] = counts.get(sid, 0) + 1
    return counts


def blocking_entries(now=None, grace=None):
    """占着槽位、还没到点的条目。没有宽限：该占多久就占多久。"""
    """占着槽位、还没到点的条目。与 occupancy 用同一套判定。"""
    now = time.time() if now is None else float(now)
    grace = GRACE_SECONDS if grace is None else float(grace)
    out = []
    for entry in load_timers():
        sid = entry.get("slotId")
        if not sid and entry.get("kind") == "action":
            sid = slot_id_for(entry.get("slot"))
        if not sid:
            continue
        if float(entry.get("at") or 0) > now:
            out.append(entry)
    return out


def can_take(slot_id, now=None):
    """这个槽位还能不能放下一条。返回 (ok, 原因)。"""
    slots = load_slots()
    spec = slots.get(slot_id)
    if not spec:
        return False, f"未知槽位：{slot_id}"
    used = occupancy(now).get(slot_id, 0)
    capacity = int(spec.get("capacity") or 1)
    if used >= capacity:
        label = spec.get("label") or slot_id
        busy = blocking_entries(now)
        doing = str((busy[0].get("semantic") or busy[0].get("text") or "")).strip() if busy else ""
        return False, f"{label}槽位已满（{used}/{capacity}）" + (f"：{doing}" if doing else "")
    return True, ""


# ---------------- 建计时器（body 调用） ----------------

def add(*, kind, text, at=None, minutes=0, semantic="", slot="short", chance=1000,
        reward=None, location="", state=None, silent=False, source="body"):
    """建一条计时器。at 是绝对时间戳；给 minutes 时按 now+minutes 算。

    text 是到期后进上下文的那句话，由 body 在建的时候一并写好——
    这样到期时纯代码搬运即可，不需要再调一次模型。
    """
    if kind in ("action", "event"):
        cap = SLOT_MINUTE_CAP.get(str(slot).strip())
        if cap is not None and float(minutes or 0) > cap:
            log("timer.capped", slot=slot, was=round(float(minutes), 1), cap=cap,
                text=str(text)[:26])
            minutes = cap
    slot_id = slot_id_for(slot) if kind == "action" else None
    if slot_id:
        ok, why = can_take(slot_id)
        if not ok:
            raise SlotFull(why)

    timers = load_timers()
    if at is None:
        at = time.time() + max(0.0, float(minutes)) * 60
    entry = {
        "id": f"t-{int(time.time() * 1000) % 10**9:09d}-{len(timers):03d}",
        "at": float(at),
        "kind": kind,
        "semantic": semantic or text[:20],
        "text": text,
        "slot": (slot_id or "short") if kind == "action" else str(slot or ""),
        "slotId": slot_id,
        "chance": int(chance),
        "silent": bool(silent),
        "source": source,
        "createdAt": time.time(),
    }
    if reward:
        entry["reward"] = dict(reward)
    if location:
        entry["location"] = str(location)
    timers.append(entry)
    save_timers(timers)
    return entry




# ---------------- 结算（纯代码） ----------------

def _clamp(value, bounds):
    lo, hi = bounds
    return max(lo, min(hi, value))


def _apply_reward(ch, delta):
    """把奖励落到数值上。返回实际生效的项。"""
    applied = {}
    metab = ch.setdefault("metabolism", {})
    emo = ch.setdefault("emotion", {})
    for key, value in (delta or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if key in metabolism_range():
            metab[key] = round(_clamp(float(metab.get(key, 0)) + float(value),
                                      metabolism_range()[key]), 3)
            applied[key] = float(value)
        elif key in EMOTION_RANGE:
            cur = float(emo.get(key, EMOTION_CENTER))
            want = cur + float(value)
            st = ch.setdefault("state", {})
            lock = st.get("emotionLock") or {}
            cool = int(lock.get("cool") or 0)
            side = None
            if cur >= EMOTION_LOCK_LOW and want < EMOTION_LOCK_LOW:
                side = "low"
            elif cur <= EMOTION_LOCK_HIGH and want > EMOTION_LOCK_HIGH:
                side = "high"
            if st.pop("emotionBreakPending", False):
                st["emotionLock"] = {"side": "none", "cool": EMOTION_LOCK_COOLDOWN}
            elif side and cool == 0 and lock.get("side") == side:
                want = EMOTION_LOCK_LOW if side == "low" else EMOTION_LOCK_HIGH
            elif side and cool == 0:
                # 只在「从界内跨出界」且未处于冷却时锁住并提示
                want = EMOTION_LOCK_LOW if side == "low" else EMOTION_LOCK_HIGH
                st["emotionLock"] = {"side": side, "cool": 0}
                applied["_locked"] = side
            emo[key] = round(_clamp(want, EMOTION_RANGE[key]), 3)
            applied[key] = round(float(value), 3)
    return applied


def settle(now=None, *, rng=None):
    """结算到期的计时器。纯代码：改数值/位置/状态，把到期文本倒进缓存池。

    返回 {"fired": [...], "lines": [...], "woke": bool}
    woke=True 表示有事件到期（需要叫醒主代理），动作类到期不唤醒。
    """
    now = time.time() if now is None else float(now)
    rng = rng or random
    timers = load_timers()
    if not timers:
        return {"fired": [], "lines": [], "woke": False}

    ch = load_character()
    fired, lines, woke = [], [], False
    keep = []
    for entry in timers:
        if not isinstance(entry, dict):
            continue
        at = float(entry.get("at") or 0)
        if at > now:
            keep.append(entry)
            continue
        chance = int(entry.get("chance") or 1000)
        if chance < 1000 and rng.randint(1, 1000) > chance:
            fired.append({"id": entry.get("id"), "missed": True})
            continue                                  # 概率没中，直接作废

        if entry.get("alarm"):                    # 闹钟：睡着就叫醒，醒着只提一句
            state_now = ch.setdefault("state", {})
            if state_now.get("active") == "sleep":
                state_now["active"] = "wake"      # 直接改 ch，随 settle 一起落盘
                state_now.pop("sleepStartedAt", None)
                entry["text"] = "闹钟到点了，她醒了过来。"
            else:
                entry["text"] = "闹钟到点了。"
                entry["silent"] = False

        if entry.get("reward"):
            entry["applied"] = _apply_reward(ch, entry["reward"])
        if entry.get("location"):
            ch["local"] = entry["location"]

        if not entry.get("silent") and str(entry.get("text") or "").strip():
            lines.append(str(entry["text"]).strip())
        if str(entry.get("kind")) == "event":
            woke = True
        fired.append({"id": entry.get("id"), "kind": entry.get("kind")})

    if fired:
        save_timers(keep)
        save_character(ch)
        if lines:
            push_pending(lines, now)
    return {"fired": fired, "lines": lines, "woke": woke}


# ---------------- 生理节律（按真实时间结算） ----------------

# 每小时变化：运动/睡眠不同。负数是减少，正数是增加。
RATES = {
    "awake": {"satiety": -4.0, "hydration": -4.0, "fatigue": +0.5, "sleepiness": +0.5},
    "sleep": {"satiety": -1.5, "hydration": -1.5, "fatigue": -7.0, "sleepiness": -7.0},
}
MAX_GAP_HOURS = 12
SLOT_MINUTE_CAP = {"short": 5.3, "long": 187.0, "event": 463.0}
GRACE_SECONDS = 0        # 已取消宽限，保留常量只为兼容旧调用
MAINTAIN_AFTER_HOURS = 3      # 睡着超过这么久，就该跑一次维护
MAINTENANCE = _paths.data("realTime", "maintenance.json")          # 一次结算最多补 12 小时，再长就当睡过去了


# 一天里的目标困意：白天低，入夜后抬起来，凌晨最高
# 困意的时间基础值：两个谐波拟合出来的平滑曲线
# 峰值 02~03 点，清晨陡降，正午最低，傍晚回升
_BASE = (52.192, 36.222, 1.614, 3.255, 7.057)     # a0, cos1, sin1, cos2, sin2
TARGET_GAIN = 0.6      # 实际值向基准靠拢的强度
SLEEP_SLOW_CAP = 1.25  # 睡眠恢复最多被拖慢到这个倍数


SLEEP_PHASE_SHIFT = 0.7        # 曲线整体后移（小时）：让晚上的困意来得晚一点


def sleepiness_base(hour):
    """按一天中的时刻（小时，可为小数）给出困意基准值。"""
    import math
    hour = (hour - SLEEP_PHASE_SHIFT) % 24
    w = 2 * math.pi / 24.0
    a0, c1, s1, c2, s2 = _BASE
    return (a0 + c1 * math.cos(w * hour) + s1 * math.sin(w * hour)
            + c2 * math.cos(2 * w * hour) + s2 * math.sin(2 * w * hour))






SLEEP_FORCE_CHANCE = 0.01      # 困到极限后每 tick 昏迷的概率
SLEEP_WAKE_CHANCE = 0.08
SLEEP_REST_TO = 10.0             # 困意掉到这个值，算睡透
SLEEP_RAMP = 0.85                # 睡到预期时长的这个比例，才开始掷骰子       # 睡够之后每 tick 醒来的概率
SLEEP_FORCE_AT = 90            # 困意进入 [90,100] 段才可能昏迷
SLEEPY_BAND = 60               # 进入犯困段
WAKE_BAND = 30                 # 醒来段：困意低于此值才计算苏醒概率
SLEEP_WAKE_BELOW = 20          # 困意降到这个值以下才可能醒
COLLAPSE_FATIGUE = 170         # 疲劳到这个值会累垮
COLLAPSE_HEALTH = 20           # 健康掉到这个值会倒下


def _sleep_window(now):
    """18:00 ~ 次日 07:00 这一段的起止。"""
    tm = time.localtime(now)
    start_hour = 18
    base = time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, start_hour, 0, 0, 0, 0, -1))
    if tm.tm_hour < 7:
        base -= 86400                     # 凌晨属于前一晚
    return base, base + 13 * 3600


def note_sleep_start(ch, now):
    """数这一段里睡了几回；超过三次就记一笔「没安排好作息」。"""
    start, end = _sleep_window(now)
    state = ch.setdefault("state", {})
    stamps = [float(x) for x in (state.get("sleepStarts") or [])
              if isinstance(x, (int, float)) and start <= float(x) <= end]
    stamps.append(float(now))
    state["sleepStarts"] = stamps[-12:]
    if len(stamps) > 3:
        tm = time.localtime(now)
        until = time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday + (1 if tm.tm_hour >= 18 else 0),
                             18, 0, 0, 0, 0, -1))
        state["poorSleep"] = {"until": until, "count": len(stamps)}
        log("sleep.poor", count=len(stamps), until=time.strftime("%m-%d %H:%M", time.localtime(until)))
    return len(stamps)


def poor_sleep_active(ch, now=None):
    """今天是不是还在「没睡够」的惩罚期。"""
    now = time.time() if now is None else float(now)
    info = (ch.get("state") or {}).get("poorSleep")
    if not isinstance(info, dict):
        return False
    return now < float(info.get("until") or 0)


def set_sleeping(on, now=None):
    """切换睡眠状态：醒来时清掉入睡时间。"""
    now = time.time() if now is None else float(now)
    ch = load_character()
    state = ch.setdefault("state", {})
    if on:
        state["active"] = "sleep"
        note_sleep_start(ch, now)
        state["sleepStartSleepiness"] = float(
            (ch.get("metabolism") or {}).get("sleepiness", 0))
        state["sleepStartedAt"] = float(now)
        log("sleep.on", at=round(now, 1))
    else:
        state["active"] = "wake"
        slept = 0.0
        if state.get("sleepStartedAt"):
            slept = round((now - float(state["sleepStartedAt"])) / 3600, 2)
        state.pop("sleepStartedAt", None)
        log("sleep.off", slept_hours=slept, at=round(now, 1))
    _write(CHARACTER, ch)
    return slept if not on else 0.0


def harness_metabolism():
    """读生理子系统自己的配置：ConsciousnessRealm/body/metabolism.json。"""
    import json as _json
    from pathlib import Path as _Path
    path = _Path(_paths.ROOT) / "ConsciousnessRealm" / "body" / "metabolism.json"
    try:
        return _json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def coupling_multipliers(metab):
    """状态之间互相影响：缺水更累、太累更困、身体差更没力气。"""
    table = []
    try:
        table = (harness_metabolism() or {}).get("coupling") or []
    except Exception:                        # noqa: BLE001
        table = []
    mult = {"fatigue": 1.0, "sleepiness": 1.0}
    try:
        cont = (harness_metabolism() or {}).get("continuous") or {}
    except Exception:                        # noqa: BLE001
        cont = {}
    # 连续关系：健康越差越容易累，疲劳越高越容易困
    health = float(metab.get("health", 100))
    fatigue = float(metab.get("fatigue", 0))
    mult["fatigue"] *= 1 + max(0.0, (100 - health) / 100) * float(cont.get("healthToFatigue", 0))
    mult["sleepiness"] *= 1 + max(0.0, fatigue / 180.0) * float(cont.get("fatigueToSleepiness", 0))
    for rule in table:
        key = rule.get("when")
        value = float(metab.get(key, 0))
        hit = (rule.get("lt") is not None and value < float(rule["lt"])) or \
              (rule.get("gt") is not None and value > float(rule["gt"]))
        if not hit:
            continue
        for target in ("fatigue", "sleepiness"):
            if rule.get(target):
                mult[target] *= float(rule[target])
    return mult


def advance(now=None):
    """按距上次结算的真实时间推进生理。返回 (小时数, 变化摘要)。"""
    now = time.time() if now is None else float(now)
    ch = load_character()
    last = float(ch.get("lastUpdate") or now)
    if last > now + 300:                 # 时钟被回拨或换了环境，重置基准
        ch["lastUpdate"] = now
        _write(CHARACTER, ch)
        return 0.0, {}
    hours = (now - last) / 3600.0
    if hours <= 0.01:
        return 0.0, {}
    hours = min(hours, MAX_GAP_HOURS)
    log("advance", hours=round(hours, 3), last=round(last, 1), now=round(now, 1),
        sleeping=(ch.get("state") or {}).get("active") == "sleep",
        before={k: ch.get("metabolism", {}).get(k) for k in ("sleepiness", "fatigue")})

    metab = ch.setdefault("metabolism", {})
    coupling = coupling_multipliers(metab)
    sleeping = (ch.get("state") or {}).get("active") == "sleep"
    rates = dict(RATES["sleep" if sleeping else "awake"])   # 昼夜差异由 sleepiness_base 承担
    # 睡得越累恢复越快：疲惫 60 时 -9/h，100 时 -11/h，200 时 -16/h
    if sleeping:
        rates["fatigue"] = -(6.0 + float(metab.get("fatigue", 0)) / 20.0)

    # 清醒时乘耦合；睡着时按固定速率恢复（睡眠质量由时长承担，不在这里再缩放）
    if not sleeping:
        for key in ("fatigue", "sleepiness"):
            rates[key] = rates.get(key, 0) * coupling[key]

    if not sleeping:
        # 动态调节：离目标越远，涨得越快/越慢，把节奏拉回一天该有的样子
        lt = time.localtime(now)
        target = sleepiness_base(lt.tm_hour + lt.tm_min / 60.0)
        drift = (target - float(metab.get("sleepiness", 0))) * TARGET_GAIN
        rates["sleepiness"] += drift
        log("circadian", target=round(target), drift=round(drift, 2),
            sleepiness=round(float(metab.get("sleepiness", 0))))
    if coupling["fatigue"] != 1.0 or coupling["sleepiness"] != 1.0:
        log("coupling", fatigue=round(coupling["fatigue"], 2),
            sleepiness=round(coupling["sleepiness"], 2),
            metab={k: metab.get(k) for k in ("hydration", "satiety", "fatigue", "health")})
    emo = ch.setdefault("emotion", {})
    value = float(emo.get("emotion", EMOTION_CENTER))   # 0 是合法值，不能用 or
    emo_name, _ = stage_of("emotion", value)
    coef = EMOTION_DRIFT.get(emo_name)
    if coef:
        step = EMOTION_DRIFT_BASE * coef * hours
        if value >= EMOTION_CENTER:
            step = -step
        value = max(0.0, min(240.0, value + step))
        emo["emotion"] = round(value, 3)
        log("emotion.drift", band=emo_name, coef=coef,
            value=round(value, 1), step=round(step, 2))

    coefs = phys_coefficients(ch)
    if poor_sleep_active(ch):
        coefs["fatigue"] *= 1.3           # 夜里睡碎了，第二天更容易累
    log("phys.coef", **{k: round(v, 3) for k, v in coefs.items()})
    for key in ("satiety", "hydration", "fatigue", "sleepiness"):
        if key in rates:
            rates[key] *= coefs[key]

    hp_delta = health_delta(ch) * hours if (not sleeping) else 0.0

    applied = {}
    for key, rate in rates.items():
        if key not in metabolism_range():
            continue
        before = float(metab.get(key, 0))
        after = _clamp(before + rate * hours, metabolism_range()[key])
        if round(after, 1) != round(before, 1):
            applied[key] = round(after - before, 1)
        metab[key] = round(after, 3)

    if hp_delta:
        before = float(metab.get("health", 100))
        after = max(0.0, min(100.0, before + hp_delta))
        if round(after, 3) != round(before, 3):
            applied["health"] = round(after - before, 3)
        metab["health"] = round(after, 3)

    # 体温缓慢回到 36.5
    temp = float(metab.get("bodyTemperature", 36.5))
    metab["bodyTemperature"] = round(temp + (36.5 - temp) * min(1.0, hours / 3), 2)
    log("advance.done", applied=applied,
        after={k: metab.get(k) for k in ("sleepiness", "fatigue")})

    state = ch.setdefault("state", {})

    # 概率判定：困到极限会撑不住睡过去；睡够了会自己醒
    sleepiness_now = float(metab.get("sleepiness", 0))
    fatigue_now = float(metab.get("fatigue", 0))
    health_now = float(metab.get("health", 100))
    force_at = band_start("sleepiness", "头昏眼花", SLEEP_FORCE_AT)
    collapse_at = band_start("fatigue", "撑不住", COLLAPSE_FATIGUE)
    health_at = band_end("health", "很差", COLLAPSE_HEALTH)
    wake_band = SLEEP_WAKE_BELOW                # 醒来阈值：睡到几乎不困才算睡够

    collapse = None
    if sleepiness_now >= force_at:
        collapse = "困到极限"
    elif fatigue_now >= collapse_at:
        collapse = "累垮了"
    elif health_now < health_at:
        collapse = "身体撑不住"
    if not sleeping and collapse and random.random() < SLEEP_FORCE_CHANCE:
        log("sleep.force", reason=collapse, sleepiness=sleepiness_now,
            fatigue=fatigue_now, health=health_now)
        sleeping = True
        metab["_forceSleep"] = 1
        where = str(ch.get("local") or "原地")
        reason_text = {"困到极限": "撑不住了，就地闭了眼",
                       "累垮了": "再也撑不住，腿一软坐倒下去",
                       "身体撑不住": "眼前一黑，倒了下去"}.get(collapse, "倒了下去")
        push_pending([f"她{reason_text}（{where}），之后的事她不记得。"], now)
    elif sleeping:
        slept_h = ((now - float(state.get("sleepStartedAt") or now)) / 3600)
        # 困意越低越睡得浅：这是身体自己醒，不是闹钟
        rest = max(0.0, (wake_band - sleepiness_now) / max(1.0, wake_band))
        # 预期睡多久：入睡时的困意掉到「睡透」所需的时间（按 -7/h）
        start_s = float(state.get("sleepStartSleepiness") or sleepiness_now)
        health_now = float(metab.get("health", 100))
        expect_h = max(0.5, (start_s - SLEEP_REST_TO) / 7.0) * (1.0 + (100.0 - health_now) / 200.0)
        ratio = slept_h / expect_h
        damp = 0.0 if ratio <= SLEEP_RAMP else min(1.0, (ratio - SLEEP_RAMP) / (1.0 - SLEEP_RAMP))
        effects = stage_effects(ch)
        need = max(0.0, -(effects.get("satiety", 0) + effects.get("hydration", 0)
                          + effects.get("fatigue", 0)))
        wake_p = SLEEP_WAKE_CHANCE * (1.0 + rest) * damp * (1.0 + 0.15 * need)

        if (sleepiness_now <= wake_band or slept_h >= expect_h) and random.random() < wake_p:
            log("sleep.wake", sleepiness=sleepiness_now, slept=round(slept_h, 2))
            metab["_forceWake"] = 1

    if metab.pop("_forceSleep", None):
        state["active"] = "sleep"
        state.setdefault("sleepStartedAt", now)
    if sleeping and not state.get("sleepStartedAt"):
        state["sleepStartedAt"] = last      # 记下这一觉的起点
    if state.get("active") != "sleep":
        state.pop("sleepStartedAt", None)
        since = state.get("groggySince")
        if since and now - float(since) > 3 * 3600:
            state.pop("groggy", None)
            state.pop("groggySince", None)

    if sleeping and state.get("active") == "sleep" and metab.pop("_forceWake", None):
        state["active"] = "wake"
        state["since"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        applied["active"] = "wake"
        applied["_woke"] = True
        mark = _read(MAINTENANCE, {}) or {}
        started = float(state.get("sleepStartedAt") or 0)
        # 睡没睡够看生理：困意和疲惫都低就算睡透了，与维护有没有跑无关
        rested = (float(metab.get("sleepiness", 0)) <= 25
                  and float(metab.get("fatigue", 0)) <= 30)
        if not rested:
            state["groggy"] = True
            state["groggySince"] = now
            applied["_groggy"] = True
        else:
            state.pop("groggy", None)
            state.pop("groggySince", None)
        state.pop("sleepStartedAt", None)
        sleeping = False
    elif not sleeping and float(metab.get("sleepiness", 0)) >= 92:
        applied["_sleepy"] = "困"
    if sleeping:
        applied["_sleeping"] = True
    else:
        applied.pop("_sleeping", None)

    # 睡着够久了 → 该跑维护（压缩 + 升格），跑没跑过用文件记着
    if sleeping:
        started = float(state.get("sleepStartedAt") or now)
        slept = (now - started) / 3600.0
        if slept >= MAINTAIN_AFTER_HOURS:
            mark = _read(MAINTENANCE, {}) or {}
            if not mark.get("doneFor") or mark.get("doneFor") != started:
                applied["_maintain"] = round(slept, 1)
                _write(MAINTENANCE, {"due": True, "sleepStartedAt": started,
                                     "at": now, "slept": round(slept, 2)})

    ch["lastUpdate"] = now
    save_character(ch)
    return round(hours, 2), applied


# ---------------- 状态渲染（常驻内容） ----------------

# (起始小时, 名称)：13 点就是下午，不是一个区间刚好在某处断开
DAY_PARTS = ((0, "深夜"), (5, "清晨"), (8, "上午"), (11, "中午"),
             (13, "下午"), (17, "傍晚"), (19, "夜里"))



# 时段后面带一句行为含义：她照着这个说话，不用自己估
PART_HINT = {
    "深夜": "天全黑，街上没人",
    "清晨": "天刚亮，人少",
    "上午": "天亮了，街上有人来往",
    "中午": "日头最高，铺子开着",
    "下午": "日头偏西，还亮着",
    "傍晚": "天还亮着，街上还有人",
    "夜里": "天黑了，人往回走",
}


def clock_text(now=None):
    """返回 "21:07（夜里，天黑了，人往回走）" 这种带行为含义的时间串。"""
    now = time.time() if now is None else float(now)
    tm = time.localtime(now)
    hour = tm.tm_hour
    part = "深夜"
    for edge, label in DAY_PARTS:
        if hour >= edge:
            part = label
    hint = PART_HINT.get(part, "")
    return f"{time.strftime('%H:%M', tm)}（{part}{'，' + hint if hint else ''}）"


def stage_tables():
    return (harness_metabolism() or {}).get("stages") or {}


def band_start(key, name, fallback):
    """某一段的起点：上一段的 to，找不到就用 fallback。"""
    prev = 0.0
    for band in stage_tables().get(key) or []:
        if band.get("name") == name:
            return float(prev)
        prev = float(band.get("to", 100))
    return float(fallback)


def band_end(key, name, fallback):
    """某一段的终点（含），即该段的 to。"""
    for band in stage_tables().get(key) or []:
        if band.get("name") == name:
            return float(band.get("to", 100))
    return float(fallback)


def stage_of(key, value):
    """返回 (阶段名, 给模型看的一句话)。"""
    bands = stage_tables().get(key) or []
    for index, band in enumerate(bands):
        edge = float(band.get("to", 100))
        last = index == len(bands) - 1
        if float(value) < edge or (last and float(value) <= edge):
            return band.get("name") or "", band.get("say") or ""
    return "", ""


# ---------------- 生理耦合：阶段影响与权重 ----------------
STAGE_EFFECT = {
    "饿坏": -3, "有些饿": -2, "正常": 0, "吃饱": 1,
    "渴坏": -3, "有些渴": -2, "充足": 1,
    "精神": 1, "有些累": -1, "很累": -2, "撑不住": -3,
    "清醒": 1, "犯困": -1, "头昏眼花": -2,
    "很差": -2, "不佳": -1, "良好": 1,
}
# 来源 -> 被影响项 的权重
WEIGHTS = {
    "satiety": {"satiety": 0.05, "fatigue": 0.05},
    "hydration": {"hydration": 0.05, "fatigue": 0.05},
    "fatigue": {"fatigue": 0.05, "sleepiness": 0.10, "satiety": 0.05, "hydration": 0.05},
    "sleepiness": {"sleepiness": 0.05, "fatigue": 0.05},
    "health": {"satiety": 0.03, "hydration": 0.03, "fatigue": 0.03, "sleepiness": 0.03},
}
HEALTH_WEIGHTS = {"satiety": 0.20, "hydration": 0.20, "fatigue": 0.20,
                  "sleepiness": 0.10, "health": 0.10}
COEF_LOW, COEF_HIGH = 0.8, 1.2


def stage_effects(ch):
    """五项的状态影响值。"""
    metab = ch.get("metabolism") or {}
    out = {}
    for key in ("satiety", "hydration", "fatigue", "sleepiness", "health"):
        value = metab.get(key)
        if not isinstance(value, (int, float)):
            continue
        name, _ = stage_of(key, value)
        out[key] = float(STAGE_EFFECT.get(name, 0))
    return out


def phys_coefficients(ch):
    """每项自己的系数：别人对它的影响加权。"""
    effects = stage_effects(ch)
    coefs = {}
    for target in ("satiety", "hydration", "fatigue", "sleepiness"):
        total = 0.0
        for source, weights in WEIGHTS.items():
            w = weights.get(target)
            if w:
                total += w * effects.get(source, 0)
        coefs[target] = max(COEF_LOW, min(COEF_HIGH, 1.0 - total))
    return coefs


def health_delta(ch):
    """健康每小时的变化：负面项累加（有上限），没有负面项时才由正面项恢复。"""
    effects = stage_effects(ch)
    neg = sum(w * effects.get(src, 0) for src, w in HEALTH_WEIGHTS.items()
              if effects.get(src, 0) < 0)
    pos = sum(w * effects.get(src, 0) for src, w in HEALTH_WEIGHTS.items()
              if effects.get(src, 0) > 0)
    if neg < 0:
        return round(max(-2.2, neg * 0.95), 3)
    return round(min(0.4, pos * 0.25), 3)


def snapshot():
    """唯一数据源：先结算，再一次性取时间、角色、计时器。"""
    settle()
    now = time.time()
    return {"now": now, "ch": load_character(), "timers": load_timers()}


def tick_lock():
    """每轮把冷却减一；归零就解锁。"""
    ch = load_character()
    lock = (ch.get("state") or {}).get("emotionLock")
    if not lock:
        return
    cool = int(lock.get("cool") or 0)
    if cool > 0:
        lock["cool"] = cool - 1
        if lock["cool"] <= 0:
            lock["side"] = "none"
        _write(CHARACTER, ch)


def places():
    """地点表：地点名 -> 属性。"""
    path = _paths.ROOT / "ConsciousnessRealm" / "world" / "places.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def place_of(local):
    """从「城/地点」里取地点名，匹配地点表。"""
    local = str(local or "")
    for name in places():
        if name in local:
            return name
    return ""


def relations(ch):
    """角色记得的人：{名字: {trust, met}}。"""
    rel = ch.get("relations")
    return rel if isinstance(rel, dict) else {}


def touch_relation(ch, name, trust=0, met=1):
    """记一次来往。"""
    if not name:
        return
    rel = ch.setdefault("relations", {})
    item = rel.setdefault(name, {"trust": 30, "met": 0})
    item["met"] = int(item.get("met") or 0) + met
    item["trust"] = max(0, min(100, float(item.get("trust") or 30) + trust))
    item["last"] = time.strftime("%Y-%m-%d %H:%M", time.localtime())


def render_environment(ch, now=None):
    """环境与风险：给主代理判断处境用的材料。"""
    now = time.time() if now is None else float(now)
    hour = time.localtime(now).tm_hour
    night = hour >= 21 or hour < 6
    local = str(ch.get("local") or "")
    name = place_of(local)
    if not name:
        return []
    info = places().get(name) or {}
    bits = [f"所处环境：{local}"]
    bits.append("室内" if info.get("indoor") else "露天")
    if info.get("private"):
        bits.append("她自己的地方")
    else:
        bits.append(f"夜里{info.get('nightCrowd') or '情况不明'}" if night
                    else "白天有人来往")
    risk = int(info.get("risky") or 0)
    if night:
        risk += 1
    level = "低" if risk <= 1 else ("中" if risk <= 3 else "高")
    bits.append(f"此时风险：{level}")
    lines = [" · ".join(bits)]

    known = relations(ch)
    regulars = [r for r in (info.get("regulars") or [])]
    if regulars:
        shown = []
        for who in regulars:
            item = known.get(who)
            if item:
                shown.append(f"{who}（来往 {int(item.get('met') or 0)} 次，信任 {int(item.get('trust') or 0)}）")
            else:
                shown.append(f"{who}（只是认得）")
        lines.append("此处常见：" + "、".join(shown))
    return lines


def render_state(ch=None, pending="", now=None, full=False):
    """渲染 <state> 的内容行。这是常驻信息：时间、位置、身体状况、刚发生的事。"""
    ch = ch if isinstance(ch, dict) else load_character()
    lines = [f"当前时间：{clock_text(now)}。"]
    if (ch.get("state") or {}).get("active") == "sleep":
        alarm = [e for e in load_timers() if e.get("alarm")]
        when = ""
        if alarm:
            when = "，闹钟定在 " + time.strftime("%H:%M", time.localtime(float(alarm[0]["at"])))
        lines.append(f"角色正在睡眠{when}。她已经躺下了，不必再递交睡下的动作；醒来自会由生理或闹钟触发。")

    moment = time.time() if now is None else float(now)
    last_action = ch.get("state", {}).get("lastActionAt")
    if not isinstance(last_action, (int, float)):
        last_action = 0
    for entry in reversed(load_timers()):
        stamp = entry.get("createdAt") or entry.get("at")
        if isinstance(stamp, (int, float)) and stamp <= moment:
            last_action = max(last_action, float(stamp))
            break
    if not last_action:                       # 没有计时器时，回看 session 最后一条
        try:
            path = _paths.data("session", "session.jsonl")
            for raw in reversed(path.read_text(encoding="utf-8").splitlines()[-40:]):
                raw = raw.strip()
                if not raw:
                    continue
                item = json.loads(raw)
                stamp = item.get("ts")
                if item.get("role") == "assistant" and stamp:
                    last_action = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
                    break
        except (OSError, ValueError, json.JSONDecodeError):
            last_action = 0
    if last_action:
        span = max(0, int((moment - last_action) // 60))
        if span >= 60:
            lines.append(f"距上一次行动已过去 {span // 60} 小时 {span % 60} 分钟。")
        else:
            lines.append(f"距上一次行动已过去 {span} 分钟。")

    local = str(ch.get("local") or "").strip()
    if local:
        lines.append(f"角色当前位置：{local}。")
    lines.extend(render_environment(ch, now))
    busy = [t for t in load_timers()
            if t.get("kind") == "action" and float(t.get("at") or 0) > moment]
    if busy:
        parts = []
        for entry in busy:
            doing = str(entry.get("semantic") or entry.get("text") or "").strip()
            left_sec = max(0.0, float(entry["at"]) - moment)
            slot = entry.get("slotId") or entry.get("slot") or ""
            left = (f"{int(left_sec // 60)} 分钟" if left_sec >= 60
                    else f"{int(left_sec)} 秒")
            parts.append(f"[{slot}] {doing}（还剩 {left}）")
        lines.append("角色正在做：" + "；".join(parts) + "。")


    if ch.get("state", {}).get("groggy"):
        lines.append("角色刚睡醒，没睡透（生理原因）：动作会慢半拍，说话也短。")

    if os.environ.get("PZ_TEST") == "1":
        lines.append("当前是测试环境（系统原因）：不要调用压缩、维护类工具；正式运行时才启用。")

    metab = ch.get("metabolism") or {}
    names = (("sleepiness", "困意"), ("fatigue", "疲惫"), ("satiety", "饱食"),
             ("hydration", "水分"), ("health", "健康"))
    for key, label in names:
        value = metab.get(key)
        if not isinstance(value, (int, float)):
            continue
        name, say = stage_of(key, value)
        head = f"{label} {int(round(value))}（{name}）" if full else f"{label}（{name}）"
        if say:
            lines.append(f"{head}：{say}")
        elif full:
            lines.append(head)
    lock = (ch.get("state") or {}).get("emotionLock") or {}
    if lock.get("side") in ("low", "high"):
        where = "跌到分界" if lock["side"] == "low" else "涨到分界"
        lines.append(
            f"【情绪已经{where}，暂时锁住】她再往前一步，就不是自己能回来的了。"
            f"要越过，递交 active 时带 emotion_break=true，并在 description 里写清她为什么认这一步。")

    emo = (ch.get("emotion") or {}).get("emotion")
    if isinstance(emo, (int, float)):
        name, say = stage_of("emotion", emo)
        head = f"情绪 {int(round(emo))}（{name}）" if full else f"情绪（{name}）"
        if say:
            lines.append(f"{head}：{say}")
        elif full:
            lines.append(head)

    if pending:
        lines.append(f"刚刚发生：{pending}")

    log("state.render", busy=[str(t.get("semantic"))[:24] for t in busy],
        clock=clock_text(now),
        metab={k: ch.get("metabolism", {}).get(k) for k in ("sleepiness", "fatigue")})
    return lines


# ---------------- 缓存池（主代理下一轮取走） ----------------

def push_pending(lines, now=None):
    now = time.time() if now is None else float(now)
    pool = _read(PENDING, [])
    if not isinstance(pool, list):
        pool = []
    log("pending.push", lines=[str(x)[:60] for x in lines])
    stamp = time.strftime("%H:%M", time.localtime(now))
    for line in lines:
        pool.append({"at": now, "clock": stamp, "text": line})
    _write(PENDING, pool)
    return pool


def peek_pending():
    pool = _read(PENDING, [])
    return pool if isinstance(pool, list) else []


def take_pending():
    """取走缓存池并清空，返回合并好的一段文本（多条按时间顺序拼接）。"""
    pool = peek_pending()
    if not pool:
        return ""
    _write(PENDING, [])
    return merge_lines(pool)


def merge_lines(pool):
    """把池子里的文本合并成一段。纯代码，不生成新内容。"""
    if not pool:
        return ""
    ordered = sorted(pool, key=lambda x: float(x.get("at") or 0))
    texts = [str(x.get("text") or "").strip() for x in ordered]
    texts = [t for t in texts if t]
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]
    return "\n".join(texts)


# ---------------- 命令行 ----------------

def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "list"

    if cmd == "list":
        timers = load_timers()
        now = time.time()
        print(f"计时器 {len(timers)} 条")
        for t in sorted(timers, key=lambda x: float(x.get("at") or 0)):
            left = float(t.get("at") or 0) - now
            slot = str(t.get("slot") or "-")
            print(f"  {t.get('id')}  {str(t.get('kind') or '-'):<6} {slot:<5} "
                  f"{'已过期' if left <= 0 else f'还剩 {left/60:.0f} 分'}  {t.get('semantic', '')[:30]}")
        pool = peek_pending()
        if pool:
            print(f"\n缓存池 {len(pool)} 条：")
            for p in pool:
                print(f"  [{p.get('clock')}] {p.get('text')}")

    elif cmd == "settle":
        result = settle()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "merge":
        print(take_pending() or "（空）")

    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
