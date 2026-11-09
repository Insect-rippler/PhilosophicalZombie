"""生理数值模拟：模拟一天（吃饭、喝水、干活、睡觉），不调模型。

    python state/tmp/phys_day.py
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PZ_DATA"] = str(ROOT / "state" / "sim")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import timers   # noqa: E402

# 一天的计划：(分钟偏移, 事件)
PLAN = [
    (0, ("start", None)),          # 06:30 起床
    (90, ("eat", 42)),             # 08:00 早饭
    (90, ("drink", 60)),
    (180, ("work", 5.0)),          # 09:30 打铁
    (330, ("eat", 45)),            # 12:00 午饭
    (330, ("drink", 55)),
    (390, ("work", 5.0)),          # 13:00 下午的活
    (720, ("eat", 38)),            # 18:30 晚饭
    (720, ("drink", 50)),
    (780, ("sleep", None)),        # 19:30 睡
]


def apply(ch, kind, value):
    if kind == "eat":
        timers._apply_reward(ch, {"satiety": value, "fatigue": 2})
    elif kind == "drink":
        timers._apply_reward(ch, {"hydration": value})
    elif kind == "work":
        timers._apply_reward(ch, {"fatigue": value})


def main():
    timers.save_timers([])
    ch = timers.load_character()
    ch.clear()
    ch.update({
        "state": {"active": "wake"},
        "local": "奥格尼斯科/工坊区",
        "metabolism": {"sleepiness": 12, "fatigue": 20, "satiety": 85,
                       "hydration": 85, "hp": 100, "health": 100,
                       "bodyTemperature": 36.5},
        "emotion": {"emotion": 120.0},
        "lastUpdate": time.time(),
    })
    timers._write(timers.CHARACTER, ch)

    start = time.time()
    plan = sorted(PLAN)
    events = {m: (k, v) for m, (k, v) in plan}

    print(f"{'时刻':<7}{'困':>5}{'疲':>5}{'饱':>5}{'水':>5}{'健':>5}  状态")
    woke = None
    for minute in range(1, 24 * 60 + 1):
        now = start + minute * 60
        ch = timers.load_character()
        ch["lastUpdate"] = now - 60
        timers._write(timers.CHARACTER, ch)

        if minute in events:
            kind, value = events[minute]
            apply(ch, kind, value)
            if kind == "sleep":
                timers.set_sleeping(True, now)
                ch = timers.load_character()
            timers._write(timers.CHARACTER, ch)

        timers.advance(now)
        timers.settle(now)
        ch = timers.load_character()
        m = ch["metabolism"]
        state = (ch.get("state") or {}).get("active")

        if state != "sleep" and woke is None and minute > 780:
            woke = minute
            print(f"{'醒':<7}{m['sleepiness']:>5.0f}{m['fatigue']:>5.0f}"
                  f"{m['satiety']:>5.0f}{m['hydration']:>5.0f}{m['health']:>5.0f}  "
                  f"（第 {minute/60:.1f} 小时醒）")

        if minute % 120 == 0:
            clock = time.strftime("%H:%M", time.localtime(now))
            print(f"{clock:<7}{m['sleepiness']:>5.0f}{m['fatigue']:>5.0f}"
                  f"{m['satiety']:>5.0f}{m['hydration']:>5.0f}{m['health']:>5.0f}  {state}")

    ch = timers.load_character()
    m = ch["metabolism"]
    print(f"\n结束：困 {m['sleepiness']:.0f} 疲 {m['fatigue']:.0f} "
          f"饱 {m['satiety']:.0f} 水 {m['hydration']:.0f} 健 {m['health']:.0f}")
    print("睡眠时长:", "未醒" if woke is None else f"{(woke-780)/60:.2f} 小时")


main()
