"""目标管理：增、推进、改、放弃、列。

写 `ConsciousnessRealm/plan/agenda.json`，每次动作都往
`plan/archive/YYYY-MM/events.jsonl` 追加一条流水——事件是给以后分析用的，
不进上下文。

stdin 收 JSON，stdout 打 JSON。

动作：
    {"action":"list"}
    {"action":"add","objective":"...","steps":["第一步","第二步"]}
    {"action":"step_done","goal":"g_xxx","step":1,"note":"做完了"}
    {"action":"step_drop","goal":"g_xxx","step":2,"note":"这一步不做了"}
    {"action":"revise","goal":"g_xxx","objective":"...","note":"改主意了"}
    {"action":"pause"|"resume"|"abandon"|"done","goal":"g_xxx","note":"..."}
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_REALM = Path(os.environ.get("PZ_CARD_ROOT") or (ROOT / "ConsciousnessRealm"))
AGENDA = _REALM / "plan" / "agenda.json"
ARCHIVE = _REALM / "plan" / "archive"


def _now():
    if os.environ.get("PZ_VCLOCK") == "1":
        try:
            import sys as _sys
            _sys.path.insert(0, str(ROOT / "engine"))
            import vclock
            return vclock.now()
        except Exception:                   # noqa: BLE001
            pass
    return time.time()

STATUSES = ("active", "paused", "done", "abandoned", "failed")
# 动作名和状态名不是一回事：abandon 动作对应 abandoned 状态
ACTION_STATUS = {"pause": "paused", "resume": "active",
                 "abandon": "abandoned", "done": "done"}


def load():
    try:
        data = json.loads(AGENDA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data.get("goal"), list):
        data["goal"] = []
    for goal in data["goal"]:
        if isinstance(goal, dict):
            goal.setdefault("kind", "self")
        ensure_ids(goal)
    return data


def save(data):
    AGENDA.parent.mkdir(parents=True, exist_ok=True)
    try:
        month = time.strftime("%Y-%m")
        folder = ARCHIVE / month / "plans"
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.json"
        (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except OSError:
        pass
    tmp = AGENDA.with_name(f"{AGENDA.name}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(AGENDA)


def log_event(goal_id, action, note="", step=None):
    month = time.strftime("%Y-%m")
    folder = ARCHIVE / month
    folder.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "goal": goal_id,
           "op": action, "note": str(note or "").strip()}
    if step is not None:
        row["step"] = step
    with open(folder / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def ensure_ids(goal):
    """步骤编号：手写计划没写 id 时按顺序补上。"""
    for i, step in enumerate(goal.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if not isinstance(step.get("id"), int):
            step["id"] = i + 1


def new_id(data):
    n = 1
    while any(g.get("id") == f"g_{n:04d}" for g in data["goal"]):
        n += 1
    return f"g_{n:04d}"


def find(data, gid):
    return next((g for g in data["goal"] if g.get("id") == gid), None)


def add(data, args):
    objective = str(args.get("objective") or "").strip()
    if not objective:
        raise ValueError("[ERROR] 缺少 objective")
    raw_steps = args.get("steps") or []
    steps = []
    for item in raw_steps:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            fallback = str(item.get("fallback") or "").strip()
        else:
            title, fallback = str(item).strip(), ""
        if title:
            steps.append({"title": title, "fallback": fallback})
    goal = {"id": new_id(data), "kind": "self", "objective": objective, "status": "active",
            "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "note": "",
            "steps": [{"id": i + 1, "title": s["title"],
                       "status": "doing" if i == 0 else "pending",
                       "fallback": s["fallback"], "evidence": ""}
                      for i, s in enumerate(steps)]}
    data["goal"].append(goal)
    log_event(goal["id"], "create", objective)
    return goal


def step_done(data, args):
    goal = find(data, str(args.get("goal") or ""))
    if not goal:
        raise ValueError("[ERROR] 没有这个目标")
    num = int(args.get("step") or 0)
    steps = goal.get("steps") or []
    step = next((s for s in steps if int(s.get("id") or 0) == num), None)
    if step is None and 0 < num <= len(steps):
        step = steps[num - 1]
    if step is None:
        raise ValueError(f"[ERROR] 没有第 {num} 步")
    step["status"] = "done"
    step["evidence"] = str(args.get("note") or "").strip()
    nxt = next((s for s in goal["steps"] if s.get("status") == "pending"), None)
    if nxt:
        nxt["status"] = "doing"
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log_event(goal["id"], "step_done", f"第 {num} 步：{step['title']}", step=num)
    return goal


TERMINAL = ("done", "dropped", "cancelled")


def step_drop(data, args):
    goal = find(data, str(args.get("goal") or ""))
    if not goal:
        raise ValueError("[ERROR] 没有这个目标")
    num = int(args.get("step") or 0)
    steps = goal.get("steps") or []
    step = next((s for s in steps if int(s.get("id") or 0) == num), None)
    if step is None and 0 < num <= len(steps):
        step = steps[num - 1]
    if step is None:
        raise ValueError(f"[ERROR] 没有第 {num} 步")
    step["status"] = "dropped"
    step["evidence"] = str(args.get("note") or "").strip()
    nxt = next((s for s in steps if s.get("status") == "pending"), None)
    if nxt:
        nxt["status"] = "doing"
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log_event(goal["id"], "step_drop", str(args.get("note") or ""), step=num)
    return goal


def set_status(data, args, status):
    goal = find(data, str(args.get("goal") or ""))
    if not goal:
        raise ValueError("[ERROR] 没有这个目标")
    if status == "done":
        left = [s for s in goal.get("steps") or []
                if str(s.get("status") or "pending") not in TERMINAL]
        if left:
            titles = "；".join(str(s.get("title") or "")[:16] for s in left[:3])
            raise ValueError(f"[ERROR] 还有步骤没完成或放弃：{titles}")
    goal["status"] = status
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    note = str(args.get("note") or "").strip()
    if note:
        goal["note"] = note
    log_event(goal["id"], status, note)
    return goal


def _event_done(goal, note=""):
    event = goal.setdefault("event", {})
    try:
        deadline = time.mktime(time.strptime(str(event.get("deadlineAt")),
                                             "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        deadline = 0
    now = _now()
    if deadline and now >= deadline:
        goal["status"] = "failed"
        event["state"] = "failed"
        goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        log_event(goal["id"], "event_failed", note or "超时")
        return {"ok": False, "reason": "事件已超时，按失败处理", "id": goal["id"]}
    goal["status"] = "done"
    event["state"] = "done"
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log_event(goal["id"], "event_done", note)
    return {"ok": True, "id": goal["id"], "status": "done",
            "objective": goal["objective"], "event": "done"}


def _event_abandon(goal, note=""):
    event = goal.setdefault("event", {})
    goal["status"] = "abandoned"
    event["state"] = "abandoned"
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log_event(goal["id"], "event_abandoned", note)
    return {"ok": True, "id": goal["id"], "status": "abandoned",
            "objective": goal["objective"], "event": "abandoned"}


def main():
    try:
        _main()
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
    except Exception as exc:                 # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))


def _main():

    raw = sys.stdin.read().strip()
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"[ERROR] 参数解析失败：{e}") from e
    if not isinstance(args, dict):
        raise ValueError("[ERROR] 参数应为 JSON 对象")

    data = load()
    action = str(args.get("action") or "list").lower()

    if not str(args.get("note") or "").strip():
        print(json.dumps({"ok": False, "reason": "note 必填：说清为什么做这次改动"},
                         ensure_ascii=False))
        return

    if action == "list":
        active = [g for g in data["goal"] if g.get("status") in ("active", "paused")]
        print(json.dumps({"ok": True, "count": len(active),
                          "goals": [{"id": g["id"], "kind": g.get("kind") or "self",
                                     "objective": g["objective"],
                                     "status": g["status"],
                                     "steps": [(s.get("id"), s.get("status")) for s in g.get("steps") or []]}
                                    for g in active]}, ensure_ascii=False))
        return

    if action == "add":
        goal = add(data, args)
    elif action == "step_done":
        goal = step_done(data, args)
    elif action == "step_drop":
        goal = step_drop(data, args)
    elif action == "revise":
        goal = find(data, str(args.get("goal") or ""))
        if not goal:
            raise ValueError("[ERROR] 没有这个目标")
        objective = str(args.get("objective") or "").strip()
        if objective:
            goal["objective"] = objective
        raw_steps = args.get("steps")
        if raw_steps:
            items = raw_steps if isinstance(raw_steps, list) else [raw_steps]
            parsed = []
            for it in items:
                if isinstance(it, dict):
                    title = str(it.get("title") or "").strip()
                    status = str(it.get("status") or "pending")
                    fallback = str(it.get("fallback") or "").strip()
                else:
                    title, status, fallback = str(it).strip(), "pending", ""
                if title:
                    parsed.append({"title": title, "status": status,
                                   "fallback": fallback})
            for i, step in enumerate(parsed):
                step.setdefault("id", i + 1)
            step_no = args.get("step")
            if step_no is not None and parsed:
                idx = int(step_no) - 1
                steps = goal.get("steps") or []
                if 0 <= idx < len(steps):
                    steps[idx] = parsed[0]
            elif parsed:
                goal["steps"] = parsed
        goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        log_event(goal["id"], "revise", str(args.get("note") or objective),
                  step=int(args.get("step") or 0) or None)
    elif action in ("done", "abandon"):
        goal = find(data, str(args.get("goal") or ""))
        if not goal:
            raise ValueError("[ERROR] 没有这个目标")
        if goal.get("kind") == "event":
            result = (_event_done(goal, str(args.get("note") or ""))
                      if action == "done"
                      else _event_abandon(goal, str(args.get("note") or "")))
            save(data)
            print(json.dumps(result, ensure_ascii=False))
            return
        goal = set_status(data, args, ACTION_STATUS[action])
    elif action in ACTION_STATUS:
        goal = set_status(data, args, ACTION_STATUS[action])
    else:
        raise ValueError(f"[ERROR] 未知动作：{action}")

    save(data)
    print(json.dumps({"ok": True, "id": goal["id"], "status": goal["status"],
                      "objective": goal["objective"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
