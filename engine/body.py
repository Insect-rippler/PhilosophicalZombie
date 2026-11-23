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
- text 是给主代理看的第三人称陈述，只写对方造成的变化，和 `<state>` 一致；20 字以内。
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
        "temperature": 0.0,
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


RATING_NOTE = {"缺陷": "完全没有这方面天赋", "普通": "普通人水平",
               "标准": "百里挑一", "优良": "千里挑一", "卓越": "万里挑一"}


def ability_lines():
    """她的能力评估：评级 + 标尺，供 body 判断能不能做到。"""
    try:
        import core
        import realm
        data = realm.load(Path(core.REALM_ROOT) / "CHARACTER.md", "CHARACTER")
    except Exception:                       # noqa: BLE001
        return ""
    body_data = data.get("body") or {}
    keys = ("物理强度", "战场机动", "生理耐受", "战术规划", "战斗技巧", "源石技艺适应性")
    rows = []
    for key in keys:
        value = str(body_data.get(key) or "").strip()
        if value:
            rows.append(f"- {key}：{value}（{RATING_NOTE.get(value, '')}）")
    if not rows:
        return ""
    return ("## 她的能力评估\n\n" + "\n".join(rows) +
            "\n\n判定能不能做到时，以这个范围为准：缺陷做不了，普通是常人水平，"
            "标准、优良、卓越依次更高。")


def judge(description, ch=None, force=False, declared_slot="", location=""):
    """让模型判一次。时长参考数据表，槽位由时长推导并检查容量。

    force=True 表示主代理要打断手上正在做的事：槽位占用不再是阻碍。
    判定只给效果与到点文本，不做通行判断。
    """
    import core
    ch = ch if isinstance(ch, dict) else timers.load_character()
    rule, judge_rules = load_realm()
    system = (f"{rule}\n\n# 判定标准\n\n{judge_rules}\n\n"

              "输出 JSON：{\"duration\":分钟参考值, \"effort\":\"轻|中|重\", "
              "\"state_read\":\"照抄你看到的当前地点与正在做的事\", "
              "\"basis\":\"这个时长是怎么来的，一句话\", "
              "\"reward\":{\"satiety\":0,\"emotion\":0}, \"text\":\"到点后的状态，和 state 的时间、地点一致，不写下一步\"}")
    user = (
        f"## 当前状态\n\n{state_snapshot(ch)}\n\n"
        f"{ability_lines()}\n\n"

        f"## 可参考的活动（时长来自数据表，别脱离这个量级）\n\n{_activity_lines(ch)}\n\n"
        f"## 最近的言行\n\n{recent_context()}\n\n"
        + f"## 她要做的事\n\n{description}\n\n"
        + f"## 她的去向\n\n{('应在：' + location) if str(location or '').strip() else '原地，不移动'}\n\n"
        "时长优先取活动表里的值；表里没有的按常识给，但不要离谱。"
        "槽位由代码管，你不用判。"
    )
    if force:
        user += ("\n\n## 强制打断\n主代理要求打断她手上正在做的事，改做上面这件。"
                 "按新动作判定即可；只给奖励与到点文本，不要拒绝。")

    body = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 600,
        "temperature": 0.0,
        "thinking": {"type": "enabled" if _body_thinking() else "disabled"},
        "response_format": {"type": "json_object"},
    }
    _t0 = vclock.real_now() if "vclock" in globals() else None
    response = modelio.send(body)
    try:
        import vclock as _vc
        _elapsed = _vc.real_now() - _t0
        _vc.advance_seconds(_elapsed)
        try:
            import core as _core
            _core.record_latency(_elapsed)
        except Exception:                   # noqa: BLE001
            pass
    except Exception:                       # noqa: BLE001
        pass
    if not response:
        return None
    message = (response.get("choices") or [{}])[0].get("message") or {}
    reasoning = str(message.get("reasoning_content") or "")
    if reasoning:
        log("judge.reasoning", text=reasoning[:1200])
    content = message.get("content") or ""
    log("judge.raw", chars=len(content), head=content[:200])
    data = _parse(content)
    if not isinstance(data, dict):
        return None
    if reasoning:
        data["thinking"] = reasoning[:2000]

    try:
        duration = float(data.get("duration") or 0)      # 不能取整，7 秒是 0.12 分钟
    except (TypeError, ValueError):
        duration = 0
    duration = max(1 / 60, float(duration))

    instant = False
    declared = str(declared_slot or data.get("slot") or "").strip().lower()
    slot = declared if declared in ("short", "long", "event") else (
        "short" if duration <= SHORT_LIMIT else "long")

    log("judge", desc=description[:60], minutes=duration,
        force=force, instant=instant,
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
                                          "description": description},
                                         ensure_ascii=False) + "\n")
        except OSError:
            pass

    effort = str(data.get("effort") or "").strip()
    location = str(data.get("location") or "").strip()

    text = str(data.get("text") or "").strip()

    return {
        "duration": duration,
        "instant": instant,
        "effort": effort,
        "slot": slot,
        "slotId": timers.slot_id_for(slot),
        "reward": _clean_reward(data.get("reward")),
        "text": text,
        "raw": data,
    }


def human_left_sec(seconds):
    total = int(round(max(0.0, float(seconds))))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"



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


# ---------------- 异步受理 ----------------

PROVISIONAL_MINUTES = 1.0          # 判定返回前的临时占位
_JUDGE_QUEUE = queue.Queue()
_WORKER_STARTED = False


def _apply_judgement(job, verdict):
    entries = timers.load_timers()
    target = next((e for e in entries if e.get("id") == job["id"]), None)
    if target is None:                       # 已被 force 撤掉
        return
    if verdict:
        target["text"] = str(verdict.get("text") or job["description"])
        target["reward"] = verdict.get("reward") or {}
        if job.get("location"):
            target["location"] = job.get("location")
    else:
        target["text"] = f"{job['description']}（完成）"
    target.pop("pending", None)
    target.pop("judging", None)
    timers.save_timers(entries)


def _worker():
    while True:
        job = _JUDGE_QUEUE.get()
        try:
            verdict = None
            for _attempt in range(3):
                verdict = judge(job["description"],
                                declared_slot=job.get("slot") or "short",
                                location=job.get("location") or "")
                if verdict:
                    break
                time.sleep(1.2 * (_attempt + 1))
            _apply_judgement(job, verdict)
        except Exception:                    # noqa: BLE001
            try:
                _apply_judgement(job, None)
            except Exception:                # noqa: BLE001
                pass
        finally:
            _JUDGE_QUEUE.task_done()


def _ensure_worker():
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    _WORKER_STARTED = True
    threading.Thread(target=_worker, daemon=True).start()


def submit(description, slot="short", *, location="", line="", force=False,
           seconds=0, end=""):
    """受理即建完整计时器；body 后台只补奖励与文本。"""
    ch_now = timers.load_character()
    if (ch_now.get("state") or {}).get("active") == "sleep":
        return {"ok": False, "未执行": "角色正在睡眠，不能受理动作。"}
    slot = str(slot or "").strip()
    if slot not in ("short", "long", "event"):
        slot = "short"
    if str(end or "").strip() in ("long", "event"):
        done = timers.finish_early(str(end).strip())
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
                    ch_now = timers.load_character()
                    timers._apply_reward(ch_now, scaled)
                    timers._write(timers.CHARACTER, ch_now)
            log("finish_early", slot=end, ratio=round(ratio, 2),
                seconds=int(elapsed), reward=scaled)

    try:
        timers.settle()
    except Exception:                         # noqa: BLE001
        pass
    now = time.time()
    running = [e for e in timers.load_timers()
               if str(e.get("slotId") or "") == slot
               and float(e.get("at") or 0) > now]
    if running and not force:
        return _reject(running)
    if force and running:
        timers.cancel(slot)
    estimated = float(seconds or 0)
    if estimated <= 0:
        estimated = PROVISIONAL_MINUTES * 60
    try:
        entry = timers.add(kind="action", text=description,
                           minutes=estimated / 60.0,
                           semantic=description[:30], slot=slot,
                           location=location or "", source="body")
    except timers.SlotFull:
        return _reject(timers.blocking_entries())
    _ensure_worker()
    _JUDGE_QUEUE.put({"id": entry.get("id"), "slot": slot,
                      "description": description,
                      "location": location or "", "line": line or ""})
    if slot == "short":
        return {"已执行": "动作已受理；完成情况写在 <state> 的「刚刚发生」中。",
                "ok": True}
    ends = time.strftime("%H:%M:%S", time.localtime(float(entry.get("at") or 0)))
    left = human_left_sec(max(0.0, float(entry.get("at") or 0) - time.time()))
    return {"已执行": f"{slot} 已受理，还剩 {left}，{ends} 完成。可以继续调工具，也可以写正文收口。",
            "ok": True}


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
            timers._write(timers.CHARACTER, ch)
            timers.set_sleeping(True)
            return {"已执行": "睡眠请求已受理，即将入睡。",
                    "ok": True}
        metab = ch.get("metabolism") or {}
        sleepy = float(metab.get("sleepiness") or 0)
        fatigue_now = float(metab.get("fatigue") or 0)
        satiety_now = float(metab.get("satiety") or 0)
        health = float(metab.get("health") or 100)
        expect_h = max(0.5, (sleepy - timers.SLEEP_REST_TO) / 7.0) * (1.0 + (100.0 - health) / 200.0)
        wake_ts = now + expect_h * 3600
        wake_at = time.strftime("%H:%M:%S", time.localtime(wake_ts))
        wake_sleepy = max(0.0, sleepy - 7.0 * expect_h)
        wake_fatigue = max(0.0, fatigue_now - 7.0 * expect_h)
        wake_satiety = max(0.0, satiety_now - 1.5 * expect_h)
        sleepy_name, sleepy_say = timers.stage_of("sleepiness", wake_sleepy)
        fatigue_name, fatigue_say = timers.stage_of("fatigue", wake_fatigue)
        satiety_name, _ = timers.stage_of("satiety", wake_satiety)
        state["sleepAskedAt"] = now
        timers._write(timers.CHARACTER, ch)
        return {"未执行": (f"要睡下吗？按现在的困意，预计睡 {expect_h:.1f} 小时，"
                          f"{wake_at} 左右自己醒。醒来时大约：困意 {wake_sleepy:.0f}（{sleepy_name}）、"
                          f"疲惫 {wake_fatigue:.0f}（{fatigue_name}）、饱食 {wake_satiety:.0f}（{satiety_name}）。"
                          f"如果半夜反复醒或睡眠不足，这些都会更差。确认要睡，5 分钟内再调一次 sleep；"
                          f"不睡就到此为止。"),
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
