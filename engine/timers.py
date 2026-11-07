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

import json
import os
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state" / "realTime"
TIMERS = STATE_DIR / "timers.json"
CHARACTER = STATE_DIR / "character.json"
PENDING = STATE_DIR / "pending.json"

# 生理指标与取值范围
METABOLISM_RANGE = {
    "hp": (0.0, 100.0), "health": (0.0, 100.0),
    "bodyTemperature": (34.0, 40.0), "satiety": (0.0, 100.0),
    "hydration": (0.0, 100.0), "fatigue": (0.0, 200.0),
    "sleepiness": (0.0, 100.0), "sexualDesire": (0.0, 100.0),
}
EMOTION_RANGE = {"favor": (0.0, 240.0), "trust": (0.0, 240.0),
                 "possessiveness": (0.0, 240.0), "unease": (0.0, 240.0)}


# ---------------- 读写 ----------------

def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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
        "emotion": {"favor": 120.0, "trust": 120.0,
                    "possessiveness": 120.0, "unease": 120.0},
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
    ch["lastUpdate"] = time.time()
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
    """事件槽放「大块的事」，其余都算身体槽。"""
    return "event" if str(slot or "").strip() == "event" else "active"


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
        if float(entry.get("at") or 0) > now:
            counts[sid] = counts.get(sid, 0) + 1
    return counts


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
        return False, f"{label}槽位已满（{used}/{capacity}）"
    return True, ""


# ---------------- 建计时器（body 调用） ----------------

def add(*, kind, text, at=None, minutes=0, semantic="", slot="short", chance=1000,
        reward=None, location="", state=None, silent=False, source="body"):
    """建一条计时器。at 是绝对时间戳；给 minutes 时按 now+minutes 算。

    text 是到期后进上下文的那句话，由 body 在建的时候一并写好——
    这样到期时纯代码搬运即可，不需要再调一次模型。
    """
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
        "slot": "body" if kind == "action" else str(slot or ""),
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
    if isinstance(state, dict) and state:
        entry["state"] = dict(state)
    timers.append(entry)
    save_timers(timers)
    return entry


def apply_instant(text, *, reward=None, location="", state=None, silent=True, now=None):
    """秒级、不移动的动作：立刻生效，不进槽、不建计时器。

    返回一条"已发生"的记录；silent=False 时同时进缓存池，供下一轮参考。
    """
    now = time.time() if now is None else float(now)
    ch = load_character()
    applied = _apply_reward(ch, reward) if reward else {}
    if location:
        ch["local"] = location
    if isinstance(state, dict) and state:
        ch.setdefault("state", {}).update(state)
    save_character(ch)
    if not silent and str(text or "").strip():
        push_pending([text], now)
    return {"applied": applied, "location": ch.get("local"), "at": now}


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
        if key in METABOLISM_RANGE:
            metab[key] = round(_clamp(float(metab.get(key, 0)) + float(value),
                                      METABOLISM_RANGE[key]), 2)
            applied[key] = float(value)
        elif key in EMOTION_RANGE:
            emo[key] = round(_clamp(float(emo.get(key, 0)) + float(value),
                                    EMOTION_RANGE[key]), 2)
            applied[key] = float(value)
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

        if entry.get("reward"):
            entry["applied"] = _apply_reward(ch, entry["reward"])
        if entry.get("location"):
            ch["local"] = entry["location"]
        if isinstance(entry.get("state"), dict):
            state = ch.setdefault("state", {})
            if "active" in entry["state"] and state.get("active") != entry["state"]["active"]:
                state["since"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
            state.update(entry["state"])

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
    "awake": {"satiety": -4.0, "hydration": -6.0, "fatigue": -0.5, "sleepiness": +4.0},
    "sleep": {"satiety": -1.5, "hydration": -2.0, "fatigue": -12.0, "sleepiness": -14.0},
}
MAX_GAP_HOURS = 12
MAINTAIN_AFTER_HOURS = 3      # 睡着超过这么久，就该跑一次维护
MAINTENANCE = ROOT / "state" / "realTime" / "maintenance.json"          # 一次结算最多补 12 小时，再长就当睡过去了


def night_factor(now=None):
    """夜里更容易困：22 点到 6 点翻倍，傍晚次之。"""
    hour = time.localtime(time.time() if now is None else float(now)).tm_hour
    if hour >= 22 or hour < 6:
        return 2.2, 1.3          # 困意倍数、疲劳倍数
    if hour >= 18:
        return 1.5, 1.1
    return 1.0, 1.0


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

    sleeping = (ch.get("state") or {}).get("active") == "sleep"
    rates = dict(RATES["sleep" if sleeping else "awake"])
    if not sleeping:                       # 夜里更容易困、更累
        sleep_mul, fatigue_mul = night_factor(now)
        rates["sleepiness"] = rates.get("sleepiness", 0) * sleep_mul
        rates["fatigue"] = rates.get("fatigue", 0) * fatigue_mul
    metab = ch.setdefault("metabolism", {})
    applied = {}
    for key, rate in rates.items():
        if key not in METABOLISM_RANGE:
            continue
        before = float(metab.get(key, 0))
        after = _clamp(before + rate * hours, METABOLISM_RANGE[key])
        if round(after, 1) != round(before, 1):
            applied[key] = round(after - before, 1)
        metab[key] = round(after, 2)

    # 体温缓慢回到 36.5
    temp = float(metab.get("bodyTemperature", 36.5))
    metab["bodyTemperature"] = round(temp + (36.5 - temp) * min(1.0, hours / 3), 2)

    state = ch.setdefault("state", {})
    if sleeping and not state.get("sleepStartedAt"):
        state["sleepStartedAt"] = last      # 记下这一觉的起点
    if state.get("active") != "sleep":
        state.pop("sleepStartedAt", None)

    if sleeping and state.get("active") == "sleep" and float(metab.get("sleepiness", 0)) <= 12 and float(metab.get("fatigue", 0)) <= 30:
        state["active"] = "wake"
        state["since"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        applied["active"] = "wake"
        applied["_woke"] = True
        # 梦没做完就醒 = 没整理好，起来会头昏眼花
        mark = _read(MAINTENANCE, {}) or {}
        started = float(state.get("sleepStartedAt") or 0)
        if started and float(mark.get("doneFor") or 0) != started:
            state["groggy"] = True
            applied["_groggy"] = True
        else:
            state.pop("groggy", None)
        state.pop("sleepStartedAt", None)
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

DAY_PARTS = ((5, "清晨"), (8, "上午"), (11, "中午"), (13, "下午"),
             (17, "傍晚"), (22, "夜里"), (24, "深夜"))

# 只有不正常时才报，正常的不占 token。
# lt = 低于阈值算异常（饱食、水分），gt = 高于阈值算异常（疲劳、困意）。
# 顺序按「严重程度」排，命中第一条就停，不堆症状。
CONDITIONS = (
    ("satiety", "lt", 20, "肚子空得难受。"),
    ("hydration", "lt", 20, "渴得厉害。"),
    ("fatigue", "gt", 140, "累得快要撑不住。"),
    ("sleepiness", "gt", 85, "困得睁不开眼。"),
    ("satiety", "lt", 45, "有些饿了。"),
    ("hydration", "lt", 45, "有些渴。"),
    ("fatigue", "gt", 90, "身上发沉，有些累。"),
    ("sleepiness", "gt", 60, "有些困。"),
)


def clock_text(now=None):
    """返回 "21:07（夜里）" 这种带时段的时间串。"""
    now = time.time() if now is None else float(now)
    tm = time.localtime(now)
    hour = tm.tm_hour
    part = next((label for edge, label in DAY_PARTS if hour < edge), "深夜")
    return f"{time.strftime('%H:%M', tm)}（{part}）"


def render_state(ch=None, pending="", now=None):
    """渲染 <state> 的内容行。这是常驻信息：时间、位置、身体状况、刚发生的事。"""
    ch = ch if isinstance(ch, dict) else load_character()
    lines = [f"现在是 {clock_text(now)}。"]

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
            path = Path(__file__).resolve().parents[1] / "state" / "session" / "session.jsonl"
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
            lines.append(f"距离上一次行动已经过去 {span // 60} 小时 {span % 60} 分钟。")
        else:
            lines.append(f"距离上一次行动已经过去 {span} 分钟。")

    local = str(ch.get("local") or "").strip()
    if local:
        lines.append(f"你在{local}。")

    busy = [t for t in load_timers()
            if t.get("kind") == "action" and float(t.get("at") or 0) > moment]
    if busy:
        doing = str(busy[0].get("semantic") or busy[0].get("text") or "").strip()
        left = max(1, int((float(busy[0]["at"]) - moment) // 60) + 1)
        lines.append(f"手上的事：{doing}，约 {left} 分钟后结束。")

    state = ch.get("state") or {}
    if state.get("active") == "sleep":
        lines.append("你正在睡着。")

    if ch.get("state", {}).get("groggy"):
        lines.append("头昏眼花，脑子发涨，昨夜的梦没做完。")

    metab = ch.get("metabolism") or {}
    for key, op, threshold, text in CONDITIONS:
        value = metab.get(key)
        if not isinstance(value, (int, float)):
            continue
        if (op == "lt" and value < threshold) or (op == "gt" and value > threshold):
            lines.append(text)
            break                     # 一次只报最要紧的一条，不堆症状

    if pending:
        lines.append(f"[刚刚] {pending}")
    return lines


# ---------------- 缓存池（主代理下一轮取走） ----------------

def push_pending(lines, now=None):
    now = time.time() if now is None else float(now)
    pool = _read(PENDING, [])
    if not isinstance(pool, list):
        pool = []
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
            print(f"  {t.get('id')}  {t.get('kind'):<6} {t.get('slot'):<5} "
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
