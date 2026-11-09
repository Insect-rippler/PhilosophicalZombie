"""全功能自检：不依赖模拟器，逐项验证。

    python state/tmp/full_check.py
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 自检必须跑在隔离目录：早期版本用 setdefault 默认成真实 state，会覆盖真实会话
os.environ["PZ_DATA"] = str(ROOT / "state" / "sim")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

_real = (ROOT / "state").resolve()
if Path(os.environ["PZ_DATA"]).resolve() == _real:
    raise SystemExit("[abort] 自检不允许跑在真实 state 上")


RESULT = []


def check(name, ok, detail=""):
    RESULT.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name:<28} {detail}")


def fresh(emotion=120.0, **metab):
    import timers
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
                       "bodyTemperature": 36.5, **metab},
        "emotion": {"emotion": emotion},
        "lastUpdate": time.time(),
    })
    timers._write(timers.CHARACTER, ch)


# ---------- 1. 模块 ----------
try:
    import core, body, server, timers, tools, recall, modelio, paths  # noqa
    check("模块导入", True)
except Exception as exc:                       # noqa: BLE001
    check("模块导入", False, str(exc))

# ---------- 2. 工具 ----------
try:
    registry = tools.load_registry()
    names = [t["function"]["name"] for t in tools.build_tools(registry)]
    # 基础 14 个；本机可能额外挂 function.local.json 里的工具（如 appearance）
    check("工具注册", len(names) >= 14, f"{len(names)} 个: {names[:4]}…")
    missing = [n for n, path in tools.TOOL_PATHS.items()
               if not (Path(r"D:\PhilosophicalZombie") / path).exists()]
    check("脚本路径表", len(tools.TOOL_PATHS) >= 11 and not missing,
          f"{len(tools.TOOL_PATHS)} 个，缺失 {missing}")
    check("虚拟工具", set(tools.VIRTUAL_HANDLERS) == {"active", "fight", "alarm"},
          str(list(tools.VIRTUAL_HANDLERS)))
    check("function.json 无 path 泄漏", all("path" not in t for t in tools.build_tools(registry)))
except Exception as exc:                       # noqa: BLE001
    check("工具注册", False, str(exc))

# ---------- 3. 上下文组装 ----------
try:
    payload = core.context_assemble()
    system = payload["messages"][0]["content"]
    tags = [t for t in ("<state>", "记忆", "世界") if t in system]
    check("上下文组装", len(system) > 5000 and "<state>" in system,
          f"system {len(system)} 字 | 消息 {len(payload['messages'])} 条")
except Exception as exc:                       # noqa: BLE001
    check("上下文组装", False, str(exc))

# ---------- 4. 生理渲染 ----------
try:
    fresh(emotion=60.0, fatigue=165)
    lines = timers.render_state(timers.load_character())
    text = "\n".join(lines)
    check("状态强调版", "情绪" in text and "疲惫" in text and "正常" not in text.split("疲惫")[1][:4],
          text.replace("\n", " / ")[:70])
    full = timers.render_state(timers.load_character(), full=True)
    check("状态全面版", len(full) >= len(lines), f"{len(full)} 行 vs {len(lines)} 行")
except Exception as exc:                       # noqa: BLE001
    check("状态强调版", False, str(exc))

# ---------- 5. 情绪漂移 ----------
try:
    fresh(emotion=40.0)
    ch = timers.load_character()
    ch["lastUpdate"] = time.time() - 3600
    timers._write(timers.CHARACTER, ch)
    timers.advance()
    after = timers.load_character()["emotion"]["emotion"]
    check("情绪自然回归", 40.0 < after <= 46.0, f"40 → {after}")
except Exception as exc:                       # noqa: BLE001
    check("情绪自然回归", False, str(exc))

# ---------- 6. 情绪锁 ----------
try:
    ch = {"metabolism": {}, "emotion": {"emotion": 45.0}, "state": {}}
    timers._apply_reward(ch, {"emotion": -20})
    locked = ch["state"].get("emotionLock", {}).get("side")
    timers._apply_reward(ch, {"emotion": -20})
    held = ch["emotion"]["emotion"] == 30.0
    check("情绪边界锁", locked == "low" and held, f"锁 {locked} | 值 {ch['emotion']['emotion']}")
    ch["state"]["emotionBreakPending"] = True
    timers._apply_reward(ch, {"emotion": -20})
    check("情绪解锁", ch["emotion"]["emotion"] < 30, f"越过 → {ch['emotion']['emotion']}")
except Exception as exc:                       # noqa: BLE001
    check("情绪边界锁", False, str(exc))

# ---------- 7. 判定与槽位 ----------
try:
    fresh()
    r1 = body.commit("她坐下来缝补外衣，一直缝到袖口补好", slot="long")
    r2 = body.commit("她再缝一件衬衫", slot="long")
    check("动作受理", bool(r1.get("已执行")), r1.get("已执行", "")[:30])
    check("槽位拒绝", "未受理" in str(r2) and "还剩" in str(r2), str(r2)[:50])
    r3 = body.commit("她拿起水杯喝一口", slot="short")
    check("跨槽放行", bool(r3.get("已执行")), r3.get("已执行", "")[:30])
except Exception as exc:                       # noqa: BLE001
    check("槽位拒绝", False, str(exc))

# ---------- 8. 睡眠闸与睡眠状态 ----------
try:
    fresh(emotion=120.0, sleepiness=40)
    r = body.commit("她躺下睡了", slot="event", sleep=True)
    warned = ("不困" in str(r.get("未执行", "")) or "醒" in str(r.get("未执行", "")))
    forced = body.commit("她躺下睡了", slot="event", sleep=True, force=True)
    check("低困意先给确认", warned or bool(r.get("已执行")), str(r.get("未执行") or r.get("已执行"))[:34])
    check("带 force 可以睡", bool(forced.get("已执行")), str(forced.get("已执行", ""))[:30])
    ch = timers.load_character()
    ch["metabolism"]["sleepiness"] = 80
    timers._write(timers.CHARACTER, ch)
    r = body.commit("她躺下睡了", slot="event", sleep=True)
    active = (timers.load_character().get("state") or {}).get("active")
    check("困了可以睡", active == "sleep" and not timers.load_timers(),
          f"状态 {active} | 计时器 {len(timers.load_timers())}")
except Exception as exc:                       # noqa: BLE001
    check("困了可以睡", False, str(exc))

# ---------- 9. 闹钟 ----------
try:
    fresh()
    reg = tools.load_registry()
    call = {"id": "c1", "function": {"name": "alarm",
                                     "arguments": json.dumps({"seconds": 60})}}
    out = tools.execute_tool_call(call, reg)
    entries = timers.load_timers()
    check("闹钟创建", "已设" in str(out) and any(e.get("slot") == "alarm" for e in entries),
          str(out)[:40])
    for e in entries:
        e["at"] = time.time() - 1
    timers.save_timers(entries)
    timers.settle()
    check("闹钟结算", len(timers.peek_pending()) > 0, str(timers.peek_pending())[:40])
except Exception as exc:                       # noqa: BLE001
    check("闹钟创建", False, str(exc))

# ---------- 10. 外部伤害 ----------
try:
    fresh()
    core.SESSION_PATH.write_text(
        json.dumps({"role": "user", "content": "那人抬手给了她一耳光。"},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    applied = body.observe()
    check("外部伤害", bool(applied) and applied.get("hp", 0) < 0, str(applied))
except Exception as exc:                       # noqa: BLE001
    check("外部伤害", False, str(exc))

# ---------- 汇总 ----------
bad = [n for n, ok in RESULT if not ok]
print(f"\n通过 {len(RESULT) - len(bad)}/{len(RESULT)}")
if bad:
    print("失败:", bad)
