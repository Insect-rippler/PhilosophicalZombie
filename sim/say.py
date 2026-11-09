"""手动一轮：把一句话递给角色，看她怎么答。

    python state/tmp/say.py "你要说的话"
    python state/tmp/say.py --reset          # 重开
    python state/tmp/say.py --freeze         # 开/关情绪冻结
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PZ_DATA"] = str(ROOT / "state" / "sim")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

import core      # noqa: E402
import server    # noqa: E402
import timers    # noqa: E402

FLAG = ROOT / "state" / "tmp" / "manual_freeze.flag"


def reset():
    timers.save_timers([])
    timers.set_sleeping(False)
    ch = timers.load_character()
    ch.clear()
    ch.update({
        "name": "viviana",
        "local": "奥格尼斯科/工坊区",
        "state": {"active": "wake"},
        "metabolism": {"sleepiness": 40, "fatigue": 60, "satiety": 80,
                       "hydration": 80, "hp": 100, "health": 100,
                       "bodyTemperature": 36.5},
        "emotion": {"emotion": 120.0},
        "lastUpdate": timers.time.time(),
    })
    timers._write(timers.CHARACTER, ch)
    core.SESSION_PATH.write_text("", encoding="utf-8")
    FLAG.write_text("1", encoding="utf-8")
    print("已重置，情绪冻结开。")


def say(text):
    rows = [x for x in core.SESSION_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    n_before = len(rows)
    core.append_session([{"role": "user", "content": text}])
    server.idle_tick()
    rows = [json.loads(x) for x in core.SESSION_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    acts, said = [], ""
    for r in rows[n_before:]:
        if r.get("role") != "assistant":
            continue
        for c in (r.get("tool_calls") or []):
            try:
                a = json.loads((c.get("function") or {}).get("arguments") or "{}")
            except Exception:
                continue
            if a.get("description"):
                acts.append(f"{a.get('slot')}: {a.get('description')}")
            if a.get("line"):
                said = str(a["line"])
        if (r.get("content") or "").strip():
            said = str(r["content"]).strip().replace("\n", " ")
    thought = ""
    for r in rows[n_before:]:
        if r.get("role") == "assistant" and r.get("reasoning_content"):
            thought = str(r["reasoning_content"])
    ch = timers.load_character()
    print(f"\n他：{text}")
    for a in acts:
        print(f"  动作 {a}")
    print(f"她：{said}")
    if thought:
        print(f"  [思考] {thought.replace(chr(10), ' ')[:400]}")
    print(f"  [情绪 {ch['emotion']['emotion']:.0f} | 位置 {ch.get('local')} | hp {ch['metabolism'].get('hp', 100):.0f}]")
    if FLAG.exists():                       # 冻结：写回 120
        ch["emotion"] = {"emotion": 120.0}
        timers._write(timers.CHARACTER, ch)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--reset":
        reset()
    elif args[0] == "--nofreeze":
        FLAG.unlink(missing_ok=True)
        print("情绪不再冻结。")
    else:
        say(" ".join(args))
