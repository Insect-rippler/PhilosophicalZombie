"""body 代理：主代理说「她要做什么」，它判定做不做得成、要多久、做完有什么变化。

和主代理共用同一个 key 和模型（判定是结构化输出，不需要文笔）。
通用 body 在 `ConsciousnessRealm/body/`：`rule.md` 说它是谁，`judge.json` 放判定标准。
卡片的 `body/` 是覆盖层：同名文件覆盖通用文件，额外文件按 `body/order.json` 的顺序追加。

它是**同步**的：主代理在回合内调 `active` 工具 → 这里立刻判定 → 写计时器 → 返回回执。
到期结算和文本合并是另一条线（`engine/timers.py`），这里不参与。
"""

import json
import modelio
import vclock
import paths as _paths

try:
    from trace import log
except ImportError:
    def log(*a, **k):
        pass
import os
import queue
import threading
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMMON_BODY = ROOT / "ConsciousnessRealm" / "body"

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


WORLD_DIR = _paths.realm("world")


def load_world():
    """活动表；地图在 state/world/map.json，这里不再读静态 places。"""
    try:
        acts = json.loads((WORLD_DIR / "activities.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        acts = {}
    return {"name": "activities", "places": [], "activities": acts.get("activities") or []}


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


def _card_body_dir():
    root = os.environ.get("PZ_CARD_ROOT")
    return Path(root) / "body" if root else None


def _body_file_order(folder):
    """世界观层里非同名文件的加载顺序；没有 order.json 就用文件名排序。"""
    if not folder or not folder.exists():
        return []
    order = []
    meta = folder / "order.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            order = [str(x).strip() for x in (data.get("files") or []) if str(x).strip()]
        except (OSError, json.JSONDecodeError):
            order = []
    names = [p.name for p in folder.iterdir()
             if p.is_file() and p.name not in ("order.json",)]
    return order + [n for n in sorted(names) if n not in order]


def load_rule():
    """通用 rule 先读；卡片同名 rule.md 覆盖；其它 md 按顺序追加。"""
    base = COMMON_BODY / "rule.md"
    text = base.read_text(encoding="utf-8").strip() if base.exists() else ""
    card = _card_body_dir()
    if not card or not card.exists():
        return text
    names = _body_file_order(card)
    # 同名覆盖先执行，额外文件始终在覆盖之后追加。
    if (card / "rule.md").exists():
        text = (card / "rule.md").read_text(encoding="utf-8").strip()
        names = [n for n in names if n != "rule.md"]
    for name in names:
        path = card / name
        if not path.name.endswith(".md"):
            continue
        extra = path.read_text(encoding="utf-8").strip()
        if extra:
            text = f"{text}\n\n{extra}".strip()
    return text


def load_judge():
    """通用 judge 先读；卡片同名 judge.json 覆盖；额外 json 合并 sections。"""
    data = {"sections": {}}
    base = COMMON_BODY / "judge.json"
    if base.exists():
        try:
            data = json.loads(base.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"sections": {}}
    card = _card_body_dir()
    if not card or not card.exists():
        return data
    names = _body_file_order(card)
    if (card / "judge.json").exists():
        try:
            data = json.loads((card / "judge.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        names = [n for n in names if n != "judge.json"]
    for name in names:
        path = card / name
        if not path.name.endswith(".json"):
            continue
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sections = data.setdefault("sections", {})
        for key, value in (extra.get("sections") or {}).items():
            sections[key] = value
    return data


def load_realm():
    data = load_judge()
    sections = []
    for sec in (data.get("sections") or {}).values():
        if isinstance(sec, dict) and sec.get("content"):
            sections.append(str(sec["content"]).strip())
    return load_rule(), "\n\n".join(sections)


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
        title = a.get("title") or a.get("name") or a.get("key") or "（未命名活动）"
        duration = a.get("duration")
        if isinstance(duration, (list, tuple)) and len(duration) >= 2:
            duration = f"{duration[0]}~{duration[1]}"
        out.append(f"- {title}：约 {duration} 分钟（{place}）")
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
                desc = next((str(a.get(k) or "").strip()
                             for k in ("short", "long", "event")
                             if str(a.get(k) or "").strip()), "")
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


SAFE_SECONDS = 2.0


def _reject_text(busy, fallback="", now=None):
    """拒绝的正文（整句，不含前缀）。"""
    now = time.time() if now is None else float(now)
    if busy:
        entry = busy[0]
        at = float(entry.get("at") or 0)
        left = max(0.0, at - now)
        slot = str(entry.get("slotId") or entry.get("slot") or "short").strip()
        if left >= SAFE_SECONDS:
            hint = "，或用 end 提前结束" if slot in ("long", "event") else ""
            return (f"未受理：{slot} 正在执行，还剩 {human_left_sec(left)}。"
                    f"等它结束再递{hint}，或带 force 打断。")
        return (f"未受理：{slot} 正在执行，还剩 {human_left_sec(left)}。"
                f"等它结束再递。")
    return fallback or "未受理：这会儿做不了。"


def _reject(busy=None, fallback="", now=None):
    """统一的拒绝回执。"""
    return {"未执行": _reject_text(busy or [], fallback, now),
            "ok": False}


ADJUST_LIMIT = {
    "hp": 10, "health": 6, "bodyTemperature": 0.5,
    "satiety": 15, "hydration": 15, "fatigue": 8,
    "sleepiness": 8, "sexualDesire": 12, "emotion": 12,
}


ADJUST_RULES = """

# 最新一条 message 的生理调整

读最近的言行和当前状态，只判断**最新一条用户 message**对她整体生理状态的影响。
背景用来理解这句话/这件事，不要替她重算 active、timer 已经结算过的疲劳和消耗。

只给真正发生变化的字段，幅度保守；正常聊天、客套、问候、聊天本身不给数值。

| 最新一条里的情况 | 可给的变化 |
|---|---|
| 被打、推、掐、烫、拉拽 | hp −2~−10，health −1~−6，emotion −4~−12 |
| 被撕毁/抢走/弄坏在意的东西 | emotion −4~−12 |
| 被当众羞辱、诬蔑、骂 | emotion −4~−12 |
| 被冷落、敷衍、不信 | emotion −2~−6 |
| 真的被帮忙、被带吃的、被给药 | satiety/hydration +2~+15，health +1~+6，emotion +2~+8 |
| 吃到东西、喝到水 | satiety/hydration +5~+15 |
| 被吓、紧张、兴奋、亲密 | emotion ±4~±12，sexualDesire +2~+12 |
| 明确说她受伤/生病/发烧/失血 | hp/health/bodyTemperature 按程度小幅变化 |
| 只是说话、客套、问候、互相客气 | **全部省略** |

**话不算数，事才算数。**  
一句话不能直接让她睡着、饿死、痊愈或累垮；这些由身体节律和 active 结算。

输出 JSON（不变就省略）：
{"hp":0,"health":0,"bodyTemperature":0,"satiety":0,"hydration":0,
 "fatigue":0,"sleepiness":0,"sexualDesire":0,"emotion":0,
 "text":"一句话描述她此刻的身体/情绪反应，20字以内"}
"""


def _clamp_adjust(raw):
    out = {}
    for key, value in (raw or {}).items():
        if key not in METAB_KEYS and key not in EMOTION_KEYS:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if float(value) == 0:
            continue
        limit = ADJUST_LIMIT.get(key, 10)
        out[key] = round(max(-limit, min(limit, float(value))), 1)
    return out


def adjust():
    """看最新一条用户 message 对整体生理状态的影响；情绪与代谢合并为一次调整。"""
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
    stamp = f"{last_user.get('ts') or ''}:{hash(body_text) & 0xffff:x}"
    ch = timers.load_character()
    state = ch.setdefault("state", {})
    seen = state.get("lastAdjusted") or state.get("lastObserved")
    if stamp and seen == stamp:
        return None

    rule, judge_rules = load_realm()
    system = (f"{rule}\n\n{judge_rules}\n{ADJUST_RULES}\n\n"
              f"## 最新一条 message\n\n对方：{body_text[:600]}\n\n"
              f"## 最近的言行\n\n{recent_context(10)}\n\n"
              f"## 她现在的状态\n\n{state_snapshot(ch)}")
    payload = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": "据此判断这条 message 对她整体生理状态的影响。"}],
        "temperature": 0.0,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 600,
    }
    data = modelio.send(payload) or {}
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not content:
        log("adjust.empty")
        return None
    verdict = _parse(content) or {}
    delta = _clamp_adjust(verdict)
    if delta:
        applied = timers._apply_reward(ch, delta)
        log("adjust", applied=applied, text=str(verdict.get("text") or "")[:40])
    else:
        applied = {}
    if stamp:
        state["lastAdjusted"] = stamp
    timers._write(timers.CHARACTER, ch)
    return applied or None


def observe():
    """旧入口保留兼容；已并入 adjust。"""
    return adjust()


def _sleep_threshold():
    """犯困段的起点；从 metabolism.json 动态读取，不用旧常量。"""
    try:
        return float(timers.band_start("sleepiness", "犯困", 70.0))
    except Exception:                        # noqa: BLE001
        return 70.0


def _sleep_forced_level(sleepy):
    """睡觉预期/入睡时使用的困意：低于阈值抬到阈值，已在阈值以上拉满。"""
    threshold = _sleep_threshold()
    return 100.0 if float(sleepy) >= threshold else threshold


def commit(description, slot="short", *, ch=None, force=False, sleep=None,
           line=None, location=None, emotion_break=False, end=""):
    """判定 + 建计时器，返回给主代理的回执。

    slot 参数只是主代理的建议，实际用哪个槽位由 body 按时长决定。
    """
    snap = timers.snapshot()             # 结算 + 时间 + 角色，一次取齐
    if emotion_break:
        ch_break = snap["ch"]
        ch_break.setdefault("state", {})["emotionBreakPending"] = True
        timers._write(timers.CHARACTER, ch_break)
    ch = ch if isinstance(ch, dict) else snap["ch"]

    # end 优先：先提前结束指定槽位，再走正常的冲突判定
    if str(end or "").strip().lower() in ("long", "event"):
        done = timers.finish_early(str(end).strip().lower())
        if done:
            ratio = float(done["ratio"])
            elapsed = float(done["elapsed"])
            scaled = {}
            if ratio >= 0.1 and elapsed >= 60:
                for key, value in (done["entry"].get("reward") or {}).items():
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    scaled[key] = round(float(value) * ratio, 3)
                if scaled:
                    timers._apply_reward(ch, scaled)
                    timers._write(timers.CHARACTER, ch)
            log("finish_early", slot=end, ratio=round(ratio, 2),
                seconds=int(elapsed), reward=scaled)
    now = snap["now"]
    known = str(slot or "").strip().lower() in ("short", "long", "event")
    target_slot = timers.slot_id_for(slot) if known else ""
    busy = [t for t in timers.blocking_entries(now)
            if known and (t.get("slotId") or timers.slot_id_for(t.get("slot"))) == target_slot]
    if busy and not force:
        return _reject(busy, now=now)

    if (ch.get("state") or {}).get("active") == "sleep" and not sleep:
        return {"未执行": "动作未受理：她正在睡，叫醒之前做不了别的事。",
                "ok": False}

    verdict = judge(description, ch=ch, force=force,
                    declared_slot=slot, location=location)
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
            return {"已执行": "她已经在睡了。", "ok": True}
        # 二次确认：第一次只告知后果，五分钟内再递一次才真的睡下
        now = time.time()
        state = ch.setdefault("state", {})
        asked = state.get("sleepAskedAt")
        if asked and now - float(asked) <= 300 and not force:
            state.pop("sleepAskedAt", None)
            metab = ch.setdefault("metabolism", {})
            metab["sleepiness"] = round(
                _sleep_forced_level(float(metab.get("sleepiness") or 0)), 2)
            timers._write(timers.CHARACTER, ch)
            timers.set_sleeping(True)
            return {"已执行": "睡眠请求已受理，即将入睡。",
                    "ok": True}
        metab = ch.get("metabolism") or {}
        sleepy = float(metab.get("sleepiness") or 0)
        health = float(metab.get("health") or 100)
        expected_level = _sleep_forced_level(sleepy)
        expect_h = max(0.5, (expected_level - timers.SLEEP_REST_TO) / 7.0) * (1.0 + (100.0 - health) / 200.0)
        duration_sec = int(round(expect_h * 3600))
        wake_ts = now + duration_sec
        wake_at = time.strftime("%H:%M", time.localtime(wake_ts))
        state["sleepAskedAt"] = now
        timers._write(timers.CHARACTER, ch)
        return {"未执行": (f"当前状态下预计会睡 {duration_sec} 秒，即 {wake_at} 时醒来。"
                          f"该预期符合你的计划可以在5min内再递交一次sleep，会直接受理入睡；"
                          f"否则可以设定alarm后再决定睡觉或者不睡觉换别的事情做。"),
                "ok": False}
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
            location=(location or ""),
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
        return {"ok": False,
                "未执行": _reject_text(timers.blocking_entries(), str(exc))
                .replace("动作未受理：", "", 1)}

    ends = time.strftime("%H:%M:%S", time.localtime(float(entry["at"])))
    slot_name = str(verdict.get("slot") or slot or "short").strip()
    left = human_left_sec(max(0.0, float(entry["at"]) - time.time()))
    receipt = {
        "已执行": (f"{slot_name} 已受理，还剩 {left}，预计 {ends} 完成。"
                   f"可以：不调用工具说一句话；或带 force 打断当前动作。"
                   + (f"（原估 {verdict['capped_from']:.0f} 分钟，已按 {slot_name} 上限压缩）"
                      if verdict.get("capped_from") else "")),
        "ok": True,
    }
    if cancelled:
        receipt["cancelled"] = [t.get("semantic") or t.get("text") for t in cancelled]
    return receipt
