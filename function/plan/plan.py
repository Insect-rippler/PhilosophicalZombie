"""目标管理：增、推进、改、放弃、列。

写 `ConsciousnessRealm/plan/agenda.json`，每次动作都往
`plan/archive/YYYY-MM/events.jsonl` 追加一条流水——事件是给以后分析用的，
不进上下文。

stdin 收 JSON，stdout 打 JSON。

动作：
    {"action":"list"}
    {"action":"add","objective":"...","steps":["第一步","第二步"]}
    {"action":"step_done","goal":"g_xxx","step":1,"note":"做完了"}
    {"action":"revise","goal":"g_xxx","objective":"...","note":"改主意了"}
    {"action":"pause"|"resume"|"abandon"|"done","goal":"g_xxx","note":"..."}
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENDA = ROOT / "ConsciousnessRealm" / "plan" / "agenda.json"
ARCHIVE = ROOT / "ConsciousnessRealm" / "plan" / "archive"

STATUSES = ("active", "paused", "done", "abandoned")
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
    return data


def save(data):
    AGENDA.parent.mkdir(parents=True, exist_ok=True)
    tmp = AGENDA.with_name(f"{AGENDA.name}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(AGENDA)


def log_event(goal_id, action, note=""):
    month = time.strftime("%Y-%m")
    folder = ARCHIVE / month
    folder.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "goal": goal_id,
           "action": action, "note": str(note or "").strip()}
    with open(folder / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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
    steps = [str(s).strip() for s in (args.get("steps") or []) if str(s).strip()]
    goal = {"id": new_id(data), "objective": objective, "status": "active",
            "createdAt": time.strftime("%Y-%m-%d %H:%M"),
            "updatedAt": time.strftime("%Y-%m-%d %H:%M"), "note": "",
            "steps": [{"id": i + 1, "title": s,
                       "status": "doing" if i == 0 else "pending",
                       "evidence": ""} for i, s in enumerate(steps)]}
    data["goal"].append(goal)
    log_event(goal["id"], "create", objective)
    return goal


def step_done(data, args):
    goal = find(data, str(args.get("goal") or ""))
    if not goal:
        raise ValueError("[ERROR] 没有这个目标")
    num = int(args.get("step") or 0)
    step = next((s for s in goal.get("steps") or [] if int(s.get("id") or 0) == num), None)
    if not step:
        raise ValueError(f"[ERROR] 没有第 {num} 步")
    step["status"] = "done"
    step["evidence"] = str(args.get("note") or "").strip()
    nxt = next((s for s in goal["steps"] if s.get("status") == "pending"), None)
    if nxt:
        nxt["status"] = "doing"
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
    log_event(goal["id"], "step_done", f"第 {num} 步：{step['title']}")
    return goal


def set_status(data, args, status):
    goal = find(data, str(args.get("goal") or ""))
    if not goal:
        raise ValueError("[ERROR] 没有这个目标")
    goal["status"] = status
    goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
    note = str(args.get("note") or "").strip()
    if note:
        goal["note"] = note
    log_event(goal["id"], status, note)
    return goal


def main():
    raw = sys.stdin.read().strip()
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"[ERROR] 参数解析失败：{e}") from e
    if not isinstance(args, dict):
        raise ValueError("[ERROR] 参数应为 JSON 对象")

    data = load()
    action = str(args.get("action") or "list").lower()

    if action == "list":
        active = [g for g in data["goal"] if g.get("status") in ("active", "paused")]
        print(json.dumps({"ok": True, "count": len(active),
                          "goals": [{"id": g["id"], "objective": g["objective"],
                                     "status": g["status"],
                                     "steps": [(s.get("id"), s.get("status")) for s in g.get("steps") or []]}
                                    for g in active]}, ensure_ascii=False))
        return

    if action == "add":
        goal = add(data, args)
    elif action == "step_done":
        goal = step_done(data, args)
    elif action == "revise":
        goal = find(data, str(args.get("goal") or ""))
        if not goal:
            raise ValueError("[ERROR] 没有这个目标")
        objective = str(args.get("objective") or "").strip()
        if objective:
            goal["objective"] = objective
        goal["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
        log_event(goal["id"], "revise", str(args.get("note") or objective))
    elif action in ACTION_STATUS:
        goal = set_status(data, args, ACTION_STATUS[action])
    else:
        raise ValueError(f"[ERROR] 未知动作：{action}")

    save(data)
    print(json.dumps({"ok": True, "id": goal["id"], "status": goal["status"],
                      "objective": goal["objective"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
