"""工具层：注册表、虚拟工具、执行调度。

function.json 就是官方 tools 格式；路径表决定脚本，实现表决定代码。
"""

import json
import os
import runlog
import subprocess
import sys
from pathlib import Path

import core
import paths as _paths
import phone as phone_store
import combat
import body
import timers


TOOL_PATHS = {}          # {工具名: 脚本相对路径}
VIRTUAL_HANDLERS = {}
TOOL_TIMEOUT = 300
MAX_TOOL_OUTPUT = 8000


def load_registry():
    """function.json 就是官方 tools 格式，只多一个 path 字段。"""
    try:
        items = json.loads(Path(core.REGISTRY).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        core.ERROR.append(f"[core.ERROR] 工具注册表读取失败：{e}")
        return []
    # 本机专用工具：不入库，放同目录的 function.local.json
    local = Path(core.REGISTRY).with_name("function.local.json")
    if local.exists():
        try:
            extra = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(extra, list):
                items = list(items) + extra
        except (OSError, json.JSONDecodeError) as e:
            core.ERROR.append(f"[WARN] function.local.json 读取失败：{e}")
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


def allowed_tools():
    """读 harness.tools 的白/黑名单：有白名单读白名单，否则读黑名单，都没有就全放。"""
    try:
        cfg = (core.harness.get("tools") or {})
        allow = [str(x) for x in (cfg.get("allow") or []) if str(x).strip()]
        deny = [str(x) for x in (cfg.get("deny") or []) if str(x).strip()]
    except Exception:                      # noqa: BLE001
        return None
    if allow:
        return lambda name: name in allow
    if deny:
        return lambda name: name not in deny
    return None


def build_tools(registry):
    """去掉本地 path 字段与名单外的工具，剩下的就是能直接发给 API 的列表。"""
    keep = allowed_tools()
    items = [{k: v for k, v in item.items() if k != "path"} for item in registry]
    if keep is None:
        return items
    return [it for it in items if keep((it.get("function") or {}).get("name") or "")]


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
        return json.dumps({"ok": False, "未执行": "闹钟没设：seconds 必须大于 0。"},
                          ensure_ascii=False)
    seconds = min(seconds, 12 * 3600)
    note = str(args.get("note") or "").strip()
    # 闹钟只留一个：新的到了，旧的作废
    keep = [e for e in timers.load_timers() if not e.get("alarm")]
    timers.save_timers(keep)
    timers.add(kind="alarm", text=note or "闹钟", note=note,
               minutes=seconds / 60.0, semantic="闹钟",
               slot="alarm", source="alarm")
    ent = timers.load_timers()
    for item in ent:
        if item.get("kind") == "alarm" and item.get("alarm") is not True:
            item["alarm"] = True
    timers.save_timers(ent)
    ends = _t.strftime("%H:%M", _t.localtime(_t.time() + seconds))
    left = (f"{int(seconds)} 秒" if seconds < 60 else f"{int(seconds // 60)} 分钟")
    return json.dumps({"已执行": f"闹钟已设：{ends}（约 {left}后）。", "ok": True},
                      ensure_ascii=False)




def virtual_phone(raw_args):
    """本机手机：查看消息、发消息、发动态/日记。默认不唤醒。"""
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)
    action = str(args.get("action") or "apps").strip().lower()
    app = str(args.get("app") or ("moments" if action == "publish" else "chat")).strip().lower()
    wake = bool(args.get("wake") is True or str(args.get("wake")).lower() in ("true", "1", "yes"))
    try:
        if action == "apps":
            return json.dumps(phone_store.apps_payload(), ensure_ascii=False)
        if action == "list":
            data = phone_store.list_messages(app, args.get("limit") or 8)
            if not wake:
                return json.dumps({"ok": True, "app": app, "unread": data.get("unread", 0),
                                   "note": "list 默认不唤醒；需要看内容请带 wake=true。"}, ensure_ascii=False)
            return json.dumps(data, ensure_ascii=False)
        if action == "search":
            data = phone_store.search_messages(app, args.get("query") or "",
                                               args.get("limit") or 4)
            if not wake:
                return json.dumps({"ok": True, "app": app,
                                   "unread": phone_store.unread_count(app),
                                   "note": "search 默认不唤醒；需要看结果请带 wake=true。"},
                                  ensure_ascii=False)
            return json.dumps(data, ensure_ascii=False)
        if action == "read":
            data = phone_store.read_messages(app, args.get("line") or 0,
                                              args.get("around") or 2, mark=wake)
            if not wake:
                return json.dumps({"ok": True, "app": app,
                                   "unread": phone_store.unread_count(app),
                                   "note": "read 默认不唤醒；需要看内容请带 wake=true。"},
                                  ensure_ascii=False)
            return json.dumps(data, ensure_ascii=False)
        if action == "send":
            return json.dumps(phone_store.send(args.get("text") or "",
                                               args.get("kind") or "text",
                                               args.get("media") or "",
                                               args.get("caption") or ""),
                              ensure_ascii=False)
        if action == "publish":
            return json.dumps(phone_store.publish(app, args.get("text") or "",
                                                  args.get("media") or "",
                                                  args.get("caption") or "",
                                                  args.get("kind") or "text"),
                              ensure_ascii=False)
    except Exception as exc:                      # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)
    return json.dumps({"ok": False, "reason": "unknown action"}, ensure_ascii=False)

def virtual_map(raw_args):
    """地图查询与维护。日常只开放 query/create/link；dream 才开放全量。"""
    try:
        import engine.worldmap as worldmap
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"地图不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)
    action = str(args.get("action") or "").strip().lower()
    if not action:
        return json.dumps({"ok": False, "reason": "action 必填：query / create / link"}, ensure_ascii=False)
    dream = os.environ.get("PZ_DREAM") == "1"
    if action in ("unlink", "set_risk", "delete", "merge") and not dream:
        return json.dumps({"ok": False, "reason": "unlink / set_risk / delete / merge 只在 dream 里开放"}, ensure_ascii=False)
    op = {"action": action, "target": args.get("target"),
          "risk": args.get("risk"), "link": args.get("link"),
          "time": args.get("time")}
    result = worldmap.apply_op(op, dream=dream)
    return json.dumps(result, ensure_ascii=False)


def virtual_sleep(raw_args):
    """切换睡眠状态：交给 body 的睡眠确认与生理逻辑。"""
    try:
        import engine.body as body
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"body 不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    receipt = body.commit("我躺下睡了", "long", sleep=True)
    return json.dumps(receipt, ensure_ascii=False)


def virtual_pause(raw_args):
    """short 还在跑时，明确停在这一拍；不推进时间，不新建计时器。"""
    try:
        import time as _t
        timers.settle()
        now = _t.time()
        running = [e for e in timers.load_timers()
                   if str(e.get("slotId") or e.get("slot") or "") == "short"
                   and float(e.get("at") or 0) > now]
        if not running:
            return json.dumps({"ok": False,
                               "未执行": "short 槽是空的，不能暂停；直接结束本轮。"},
                              ensure_ascii=False)
        return json.dumps({"ok": True,
                           "已暂停": "这一拍停在这里；short 到点后会叫你。"},
                          ensure_ascii=False)
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "未执行": f"暂停失败：{type(e).__name__}"},
                          ensure_ascii=False)


VIRTUAL_HANDLERS["sleep"] = virtual_sleep
VIRTUAL_HANDLERS["map"] = virtual_map
VIRTUAL_HANDLERS["pause"] = virtual_pause


def virtual_active(raw_args):
    """主代理递交一个 short 动作：先受理，判定交给 body 后台。"""
    try:
        import engine.body as body
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"body 不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)

    slot = next((k for k in ("short", "long", "event")
                 if str(args.get(k) or "").strip()), "")
    description = str(args.get(slot) or "").strip() if slot else ""
    if not description:
        return json.dumps({"ok": False, "reason": "short / long / event 里要有一个动作。"},
                          ensure_ascii=False)
    seconds = args.get("seconds")
    if not isinstance(seconds, (int, float)) or int(seconds) <= 0:
        return json.dumps({"ok": False, "reason": "seconds 必填：按这一步字面估整秒数。"},
                          ensure_ascii=False)
    if slot == "short" and int(seconds) > 120:
        return json.dumps({"ok": False, "reason":
                           "short 只装短动作，最长 2 分钟；更长的事用 long 或 event。"
                           "赶路拆成多段 short，最后一段再写到达地点。"},
                          ensure_ascii=False)
    location = str(args.get("location") or "").strip()
    ch_now = timers.load_character()
    current = str(ch_now.get("local") or "").strip()
    if not location:
        return json.dumps({"ok": False, "reason":
                           "active 必须带 location：写动作结束时她所在的地点；"
                           "原地动作也照抄 <state> 当前地点。补上后重新递交。"},
                          ensure_ascii=False)
    move_note = ""
    if slot == "short" and location:
        import engine.worldmap as worldmap
        worldmap.ensure_current(current, 0)
        if location != current:
            if not worldmap.node_exists(location):
                return json.dumps({"ok": False, "reason":
                                   f"地图没有 {location}。先用 map(action=query, target=关键词) "
                                   "确认是不是已有节点；确实没有再用 "
                                   f"map(action=create, target={location}, risk=0~3, time=秒) 建点，"
                                   "建好后再递交同一条 short。"}, ensure_ascii=False)
            edge = worldmap.edge_seconds(current, location)
            if edge is None:
                route = worldmap.routes(current, location)
                hint = "；".join(" → ".join(r["path"]) for r in route.get("routes") or []) or "无路线"
                return json.dumps({"ok": False, "reason":
                                   f"地图有 {location}，但与当前 {current} 不直接相连。"
                                   f"先用 map(action=query, target={location}) 看路线，按路线逐段走。"
                                   f"当前可用路线：{hint}"}, ensure_ascii=False)
            if int(seconds) != edge:
                delta = abs(int(seconds) - edge) / max(1, edge)
                if delta > 0.2:
                    move_note = f" 地图记为 {edge} 秒，本次按地图用时受理。"
                    seconds = edge
                else:
                    move_note = f" 地图记为 {edge} 秒，按你填的 {int(seconds)} 秒受理。"
    receipt = body.submit(description, slot, force=bool(args.get("force")),
                          line=args.get("line"), location=location,
                          seconds=int(seconds or 0), end=args.get("end") or "")
    if move_note and receipt.get("ok"):
        receipt["已执行"] = str(receipt.get("已执行") or "") + move_note
    if not receipt.get("ok") and receipt.get("degraded"):
        core.ERROR.append(f"[WARN] body 受理失败：{receipt.get('reason') or ''}")
    return json.dumps(receipt, ensure_ascii=False)


def execute_tool_call(call, registry):
    """执行一次 tool_call，返回回灌给模型的文本。

    工具约定：stdin 收 JSON 参数，stdout 打 JSON 结果，非 0 退出视为失败。
    失败信息也要回灌——让模型知道发生了什么，而不是静默重试。
    """
    import time as _t
    _started = _t.time()
    fn = call.get("function") or {}
    name = fn.get("name") or ""
    raw_args = fn.get("arguments") or "{}"

    handler = VIRTUAL_HANDLERS.get(name)
    if handler is not None:
        result = handler(raw_args)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

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
    runlog.log("tool", name=(call.get("function") or {}).get("name"),
               ms=int((_t.time() - _started) * 1000))
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

def virtual_emotion(raw_args):
    """情绪突破：break=true 才翻标记；reason 记进 state 与日志。"""
    import timers
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        args = {}
    if not args.get("break"):
        return json.dumps({"ok": False, "未执行": "没有越过：break 不是 true。"},
                          ensure_ascii=False)
    ch = timers.load_character()
    st = ch.setdefault("state", {})
    st["emotionBreakPending"] = True
    reason = str(args.get("reason") or "").strip()
    if reason:
        st["emotionBreakReason"] = reason[:120]
    timers.save_character(ch)
    return json.dumps({"已执行": "情绪越过了边界。"}, ensure_ascii=False)


def virtual_line(raw_args):
    """只是说话：内容由 server 落成台词事件。"""
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        args = {}
    text = str(args.get("text") or "").strip()
    if not text:
        return json.dumps({"ok": False, "原因": "缺少 text"}, ensure_ascii=False)
    return json.dumps({"ok": True}, ensure_ascii=False)


VIRTUAL_HANDLERS.update({"active": virtual_active, "fight": virtual_fight,
                         "alarm": virtual_alarm,
                         "emotion": virtual_emotion,
                         "line": virtual_line,
                         "phone": virtual_phone})
