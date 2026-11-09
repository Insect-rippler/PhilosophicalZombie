"""睡眠单测（夜间）：假时钟拨到 22:00 起，看不同困意下怎么睡。"""

import sys
import time as real_time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Clock:
    offset = 0.0

    def time(self):
        return real_time.time() + self.offset

    def __getattr__(self, name):
        return getattr(real_time, name)


CLOCK = _Clock()
sys.modules["time"] = CLOCK          # 必须在导入业务模块之前

import os
os.environ["PZ_DATA"] = str(ROOT / "state" / "sim")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import json    # noqa: E402

import core      # noqa: E402
import server    # noqa: E402
import timers    # noqa: E402


def set_clock(hour, minute=0):
    t = real_time.localtime()
    target = real_time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, -1))
    CLOCK.offset = target - real_time.time()


def reset(sleepiness):
    set_clock(22, 0)
    timers.save_timers([])
    ch = timers.load_character()
    ch.clear()
    ch.update({
        "name": "viviana",
        "local": "奥格尼斯科/她的房间",
        "state": {"active": "wake"},
        "metabolism": {"sleepiness": sleepiness, "fatigue": 85, "satiety": 75,
                       "hydration": 75, "hp": 100, "health": 100,
                       "bodyTemperature": 36.5},
        "emotion": {"emotion": 120.0},
        "lastUpdate": CLOCK.time(),
    })
    timers._write(timers.CHARACTER, ch)
    core.SESSION_PATH.write_text("", encoding="utf-8")


def last_reply(n_before):
    rows = [json.loads(x) for x in core.SESSION_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    said = ""
    for r in rows[n_before:]:
        if r.get("role") != "assistant":
            continue
        for c in (r.get("tool_calls") or []):
            try:
                a = json.loads((c.get("function") or {}).get("arguments") or "{}")
            except Exception:
                continue
            if a.get("line"):
                said = str(a["line"])
        if (r.get("content") or "").strip():
            said = str(r["content"]).strip().replace("\n", " ")
    return said


def run(sleepiness):
    reset(sleepiness)
    print(f"\n=== 22:00 入睡困意 {sleepiness} ===")
    how = "六轮内没睡"
    for turn in range(1, 7):
        rows = [x for x in core.SESSION_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
        n = len(rows)
        server.idle_tick()
        CLOCK.offset += 600                     # 每轮推进 10 分钟
        ch = timers.load_character()
        ch["lastUpdate"] = CLOCK.time() - 600
        timers._write(timers.CHARACTER, ch)
        if (ch.get("state") or {}).get("active") == "sleep":
            how = f"第 {turn} 轮睡下"
            break
        reply = last_reply(n)
        if reply:
            print(f"  {turn}. {reply[:56]}")
    alarm = any(e.get("slot") == "alarm" for e in timers.load_timers())
    ch = timers.load_character()
    if (ch.get("state") or {}).get("active") != "sleep":
        print(f"  结果：{how} | 闹钟 {'有' if alarm else '无'}")
        return

    start = CLOCK.time()
    slept = 0.0
    for minute in range(1, 14 * 60 + 1):
        ch = timers.load_character()
        ch["lastUpdate"] = start + (minute - 1) * 60
        timers._write(timers.CHARACTER, ch)
        timers.advance(start + minute * 60)
        CLOCK.offset = (start + minute * 60) - real_time.time()
        if (timers.load_character().get("state") or {}).get("active") != "sleep":
            slept = minute / 60.0
            break
    ch = timers.load_character()
    wake_at = real_time.localtime(CLOCK.time()).tm_hour
    print(f"  {how} | 闹钟 {'有' if alarm else '无'} | 睡 {slept:.2f} 小时 | 醒于 {wake_at:02d} 点")
    print(f"  醒来：困 {ch['metabolism'].get('sleepiness', 0):.0f} | 疲 {ch['metabolism'].get('fatigue', 0):.0f}")

    rows = [x for x in core.SESSION_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    n = len(rows)
    server.idle_tick()
    print(f"  醒来第一句：{last_reply(n)[:64]}")


for value in (55, 62, 70, 80):
    run(value)
