"""工具层：注册表、虚拟工具、执行调度。

function.json 就是官方 tools 格式；路径表决定脚本，实现表决定代码。
"""

import json
import subprocess
import sys
from pathlib import Path

import core
import paths as _paths
import combat
import body
import timers


TOOL_PATHS = {}          # {工具名: 脚本相对路径}
VIRTUAL_HANDLERS = {}
TOOL_TIMEOUT = 300


def load_registry():
    """function.json 就是官方 tools 格式，只多一个 path 字段。"""
    try:
        items = json.loads(Path(core.REGISTRY).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        core.ERROR.append(f"[core.ERROR] 工具注册表读取失败：{e}")
        return []
    registry = [i for i in items if isinstance(i, dict) and i.get("function")]
    TOOL_PATHS.clear()
    for item in registry:
        name = (item.get("function") or {}).get("name")
        if name and item.get("path"):
            TOOL_PATHS[name] = item["path"]
        if name and name not in TOOL_PATHS and name not in VIRTUAL_HANDLERS:
            core.ERROR.append(f"[WARN] 工具 {name} 既没有脚本也没有实现")
    return registry


def tool_entry(name):
    """按名字取一条工具定义。"""
    for item in load_registry():
        if (item.get("function") or {}).get("name") == name:
            return item
    return None


def build_tools(registry):
    """去掉本地 path 字段，剩下的就是能直接发给 API 的列表。"""
    return [{k: v for k, v in item.items() if k != "path"} for item in registry]


def virtual_fight(raw_args):
    """回合制战斗。action=start 开打，action=act 走一回合。"""
    try:
        from engine import combat
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"战斗模块不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)

    action = str(args.get("action") or "").lower()
    if action == "start":
        from engine import commissions as commissions_mod
        cid = str(args.get("commission") or "").strip()
        enemy_id = str(args.get("enemy") or "").strip()
        if cid and not enemy_id:
            item = next((c for c in commissions_mod.active_commissions()
                         if c.get("id") == cid), None)
            if not item:
                # 也可能是委托模板 id：现接现打，顺手把单子记下来
                item = commissions_mod.accept(cid)
                if item:
                    cid = item["id"]
            if not item:
                return json.dumps({"ok": False, "reason": f"没有这张委托：{cid}"},
                                  ensure_ascii=False)
            enemy_id = item.get("enemy") or ""
        enemies = combat.load_enemies()
        if enemy_id not in enemies:
            return json.dumps({"ok": False, "reason": f"没有这个敌人：{enemy_id}",
                               "known": list(enemies)}, ensure_ascii=False)
        battle = combat.new_battle(combat.load_character_pack(), enemies[enemy_id])
        if cid:
            battle["commission"] = cid
        combat.save(battle)
        return json.dumps({"ok": True, "commission": cid or None,
                           **combat.status(battle)}, ensure_ascii=False)

    battle = combat.load()
    if not battle or battle.get("over"):
        return json.dumps({"ok": False, "reason": "现在没有进行中的战斗"}, ensure_ascii=False)

    if action == "heal":
        log, ok = combat.heal(battle)
        combat.save(battle)
        return json.dumps({"ok": ok, "log": log, **combat.status(battle)}, ensure_ascii=False)

    if action == "rescue":
        battle = combat.rescue(battle)
        applied = combat.apply_result(battle)
        combat.clear()
        return json.dumps({"ok": True, "result": battle["result"], "log": battle["log"][-1:],
                           "applied": applied, **combat.status(battle)}, ensure_ascii=False)

    if combat.is_critical(battle):
        return json.dumps({"ok": False, "critical": True,
                           "reason": "她伤得太重，撑不住了——先处理伤口（heal）或者撤（rescue）",
                           **combat.status(battle)}, ensure_ascii=False)

    skill = str(args.get("skill") or "").strip() or None
    battle, log = combat.act(battle, skill)
    result = {"ok": True, "log": log, **combat.status(battle)}
    if battle["over"]:
        result["applied"] = combat.apply_result(battle)
        if battle["result"] == "win":
            try:
                from engine import commissions as commissions_mod
                cid = str(battle.get("commission") or "")
                item = next((c for c in commissions_mod.active_commissions()
                             if c.get("id") == cid), None) or commissions_mod.current()
                if item:
                    commissions_mod.finish(item["id"])
                    result["commission"] = {"id": item["id"], "title": item.get("title"),
                                            "done": True,
                                            "rewardText": item.get("rewardText") or ""}
            except Exception as e:           # noqa: BLE001
                core.ERROR.append(f"[WARN] 委托结算失败：{type(e).__name__}: {e}")
        combat.clear()
    else:
        combat.save(battle)
    return json.dumps(result, ensure_ascii=False)


def virtual_alarm(raw_args):
    """设闹钟：独立槽位，与睡眠无关。"""
    import time as _t
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = {}
    else:
        args = raw_args or {}
    seconds = float(args.get("seconds") or 0)
    if seconds <= 0:
        return {"ok": False, "未执行": "闹钟没设：seconds 必须大于 0。"}
    seconds = min(seconds, 12 * 3600)
    note = str(args.get("note") or "").strip()
    timers.add(kind="alarm", text=note or "闹钟响了，她醒了过来。",
               minutes=seconds / 60.0, semantic="闹钟",
               slot="alarm", source="alarm")
    ent = timers.load_timers()
    for item in ent:
        if item.get("kind") == "alarm" and item.get("alarm") is not True:
            item["alarm"] = True
    timers.save_timers(ent)
    ends = _t.strftime("%H:%M", _t.localtime(_t.time() + seconds))
    left = (f"{int(seconds)} 秒" if seconds < 60 else f"{int(seconds // 60)} 分钟")
    return {"已执行": f"闹钟已设：{ends}（约 {left}后）。", "ok": True}


def virtual_active(raw_args):
    """主代理声明一个持续动作：交给 body 判定并挂计时器。"""
    try:
        import engine.body as body
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"body 不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)

    description = str(args.get("description") or "").strip()
    if not description:
        return json.dumps({"ok": False, "reason": "缺少 description"}, ensure_ascii=False)
    declared = str(args.get("slot") or "short").strip().lower()
    slot = declared if declared in ("short", "long", "event") else "short"
    force = bool(args.get("force"))

    receipt = body.commit(description, slot, force=force, appeal=args.get("appeal"),
                          sleep=args.get("sleep"), line=args.get("line"))
    if not receipt.get("ok") and receipt.get("degraded"):
        core.ERROR.append(f"[WARN] body 判定失败：{receipt.get('reason')}")
    return json.dumps(receipt, ensure_ascii=False)


def execute_tool_call(call, registry):
    """执行一次 tool_call，返回回灌给模型的文本。

    工具约定：stdin 收 JSON 参数，stdout 打 JSON 结果，非 0 退出视为失败。
    失败信息也要回灌——让模型知道发生了什么，而不是静默重试。
    """
    fn = call.get("function") or {}
    name = fn.get("name") or ""
    raw_args = fn.get("arguments") or "{}"

    handler = VIRTUAL_HANDLERS.get(name)
    if handler is not None:
        return handler(raw_args)

    path = TOOL_PATHS.get(name)
    if not path:
        return f"[工具不存在或没有脚本] {name}"

    script = (core.ROOT / path).resolve()
    if not script.exists():
        return f"[工具脚本缺失] {script}"
    if core.ROOT not in script.parents:
        return f"[拒绝执行] 脚本不在项目内：{script}"

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=raw_args, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(core.ROOT), timeout=TOOL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] {name} 超过 {TOOL_TIMEOUT} 秒未返回"
    except OSError as e:
        return f"[启动失败] {name}: {e}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        text = err or out or f"exit {proc.returncode}"
        return f"[失败 exit={proc.returncode}] {text}"
    return (out or err or "(无输出)")[:MAX_TOOL_OUTPUT]


def tool_messages(tool_calls, registry):
    """把 tool_calls 逐个执行，组装成 role=tool 的消息列表。"""
    results = []
    for call in tool_calls:
        content = execute_tool_call(call, registry)
        results.append({
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "content": content[:MAX_TOOL_OUTPUT],
        })
    return results


def strip_summary_args(calls):
    """压缩工具的 arguments 就是整份摘要，落盘前换成占位符，避免与提要重复。"""
    out = []
    for call in calls:
        call = dict(call)
        fn = dict(call.get("function") or {})
        if fn.get("name") == SUMMARY_TOOL:
            fn["arguments"] = json.dumps({"content": SUMMARY_PLACEHOLDER}, ensure_ascii=False)
        call["function"] = fn
        out.append(call)
    return out


def should_loop(response):
    """这一轮要不要继续：以有 tool_calls 为准，finish_reason 兜底。"""
    choice = ((response or {}).get("choices") or [{}])[0]
    if (choice.get("message") or {}).get("tool_calls"):
        return True
    return choice.get("finish_reason") == "tool_calls"


def tool_turn_messages(response, registry):
    """把这一轮的工具调用执行掉，返回要写进 session 的消息（assistant + 每条 tool）。"""
    choice = ((response or {}).get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return [{"role": "assistant", "content": message.get("content") or ""}]
    results = tool_messages(calls, registry)          # 先按原文执行
    assistant = {"role": "assistant", "content": message.get("content") or "",
                 "tool_calls": strip_summary_args(calls)}   # 再按占位符落盘
    return [assistant] + results

VIRTUAL_HANDLERS.update({"active": virtual_active, "fight": virtual_fight,
                         "alarm": virtual_alarm})
