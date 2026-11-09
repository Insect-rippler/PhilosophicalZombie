"""按 tick 跑的加速测试：1 tick = 1 分钟，跑满 24 小时。

每个 tick 推进生理、结算计时器、检查触发器；需要时起一轮对话。
不等真实时间，tick 之间立即连续执行。

    python state/tmp/sim_tick.py [小时数]
"""

import json
import os
import sys
import time as real_time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import time as _real_time


class _ClockModule:
    """冒充 time 模块：time() 返回逻辑时间，其余属性原样转发。"""

    def __init__(self):
        self.offset = 0.0

    def time(self):
        return _real_time.time() + self.offset

    def __getattr__(self, name):
        return getattr(_real_time, name)


CLOCK = _ClockModule()
sys.modules["time"] = CLOCK

# 数据目录整体挪到 state/sim，真实 state 不受影响
import shutil
_SIM_DATA = Path(__file__).resolve().parents[1] / "state" / "sim"
_REAL_DATA = Path(__file__).resolve().parents[1] / "state"
if not _SIM_DATA.exists():
    for item in _REAL_DATA.iterdir():
        if item.name in ("sim", "tmp"):
            continue
        if item.is_dir():
            shutil.copytree(item, _SIM_DATA / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, _SIM_DATA / item.name)
os.environ["PZ_DATA"] = str(_SIM_DATA)

os.environ["PZ_TEST"] = "1"   # 模拟器：告诉模型这是测试环境     # 之后所有 import time 都拿到它


import paths as _paths            # noqa: E402
import core                       # noqa: E402
import server                     # noqa: E402
import timers                    # 顶层名字，和 body/core 用同一份         # noqa: E402

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
TICKS = int(HOURS * 60)
LOG = ROOT / "state" / "tmp" / "sim24.jsonl"
CLOCK_FILE = ROOT / "state" / "tmp" / "sim_clock.json"


class Clock:
    offset = 0.0

    @classmethod
    def now(cls):
        return CLOCK.time()

    def time(self):                          # 关键：time.time() 必须返回逻辑时间
        return self.now()

    def __getattr__(self, name):
        return getattr(real_time, name)

    @classmethod
    def step(cls, seconds):
        CLOCK.offset += seconds


def install():
    server.TICK_SECONDS = 60


def reset(start_hour=6, start_minute=30):
    """固定锚点：今天 08:00 开始，角色状态完全重置。"""
    timers.save_timers([])
    today = real_time.localtime()
    base = real_time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                             start_hour, start_minute, 0, 0, 0, -1))
    CLOCK.offset = base - real_time.time()          # 逻辑时钟从 08:00 起

    ch = timers.load_character()
    ch.clear()
    ch.update({
        "name": "viviana",
        "local": "奥格尼斯科/她的房间",
        "state": {"active": "wake"},
        "metabolism": {"sleepiness": 12, "fatigue": 20, "satiety": 85,
                       "hydration": 85, "hp": 100, "health": 100,
                       "bodyTemperature": 36.5},
        "emotion": {"emotion": 120.0},
        "lastUpdate": base,
    })
    timers._write(timers.CHARACTER, ch)

    plan = ROOT / "ConsciousnessRealm" / "plan" / "agenda.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({"goal": []}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

    marks = _paths.data("realTime", "events.json")
    marks.parent.mkdir(parents=True, exist_ok=True)
    marks.write_text(json.dumps({"lastMessageAt": base}), encoding="utf-8")
    return base


def forge_idle(now, minutes=31):
    path = _paths.data("realTime", "events.json")
    marks = {}
    if path.exists():
        try:
            marks = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marks = {}
    marks["lastMessageAt"] = now - minutes * 60
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marks, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    install()
    base = reset()
    LOG.write_text("", encoding="utf-8")
    stats = Counter()
    turn = 0

    prev = base
    for tick in range(TICKS):
        Clock.step(60)
        now = Clock.now()
        ch0 = timers.load_character()            # 强制 lastUpdate 精确等于上一 tick
        ch0["lastUpdate"] = prev
        timers._write(timers.CHARACTER, ch0)
        timers.advance(now)
        prev = now
        timers.settle(now)
        for hook in (getattr(core, "reminder_hooks", None),
                     getattr(core, "event_hooks", None)):
            if hook is not None:
                try:
                    hook.check(now)
                except Exception:            # noqa: BLE001
                    pass

        pending = timers.peek_pending()
        if not server.should_wake(now):
            continue

        if not server.LOCK.acquire(blocking=False):
            continue
        try:
            server.idle_tick()
            forge_idle(now, 0)              # 刚说完话，静默重新计时
        except Exception:                    # noqa: BLE001
            import traceback
            traceback.print_exc()
        finally:
            server.LOCK.release()

        turn += 1
        ch = timers.load_character()
        metab = ch.get("metabolism") or {}
        record = {"i": turn, "tick": tick + 1,
                  "clock": timers.clock_text(now),
                  "hours": round((now - base) / 3600, 2),
                  "reason": "事件/计时" if pending else "静默",
                  "state": (ch.get("state") or {}).get("active"),
                  "local": ch.get("local"),
                  "sleepiness": round(metab.get("sleepiness", 0), 1),
                  "fatigue": round(metab.get("fatigue", 0), 1)}
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        stats["turns"] += 1
        print(f"{turn:>3}. tick{tick+1:>5} {record['clock']} {record['hours']:>5.2f}h "
              f"困{record['sleepiness']:>5.1f} 疲{record['fatigue']:>5.1f} "
              f"{record['state']} {record['reason']}", flush=True)

    print("---")
    print("tick 数:", TICKS, "| 实际轮次:", stats["turns"],
          "| 逻辑小时:", round((Clock.now() - base) / 3600, 2))


if __name__ == "__main__":
    main()
