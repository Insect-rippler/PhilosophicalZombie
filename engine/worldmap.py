"""运行时地图：节点、双向边、风险与路线查询。

地图存在 PZ_DATA/world/map.json；第一次读时从卡里的 world/map.seed.json 初始化。
节点用完整地点路径做 id（如「荒野/营地」）。
"""

import hashlib
import json
import os
import time

import paths as _paths

MAP_PATH = _paths.data("world", "map.json")
SEED_PATH = _paths.realm("world", "map.seed.json")

RISK_LABELS = {0: "安全", 1: "正常", 2: "警戒", 3: "危险"}
RISK_PENALTY = 600.0          # 每一级风险折算的秒数
MAX_EDGE_SECONDS = 120         # 一段路最多 2 分钟；更远要拆节点
MAX_HOPS = 24
MAX_ROUTES = 3


def _seed():
    try:
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"nodes": {}, "edges": []}
    nodes = {}
    for item in data.get("nodes") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        nodes[name] = {"risk": _risk(item.get("risk")),
                       "createdAt": time.time(), "updatedAt": time.time()}
    edges = []
    for item in data.get("edges") or []:
        a = str(item.get("a") or "").strip()
        b = str(item.get("b") or "").strip()
        seconds = int(item.get("seconds") or 0)
        if a in nodes and b in nodes and a != b and seconds > 0:
            edges.append({"a": a, "b": b, "seconds": seconds})
    return {"nodes": nodes, "edges": edges}


def _risk(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 1
    return max(0, min(3, value))


def _hash(data):
    nodes = sorted((name, int(item.get("risk") or 0)) for name, item in (data.get("nodes") or {}).items())
    edges = sorted((min(str(e["a"]), str(e["b"])), max(str(e["a"]), str(e["b"])),
                    int(e.get("seconds") or 0)) for e in (data.get("edges") or []))
    raw = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _save(data, *, refactored=None):
    data.setdefault("nodes", {})
    data.setdefault("edges", [])
    data["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["hash"] = _hash(data)
    if refactored is not None:
        data["refactoredHash"] = refactored
    data.setdefault("refactoredHash", "")
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MAP_PATH.with_name(MAP_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(MAP_PATH)
    return data


def load():
    if MAP_PATH.exists():
        try:
            data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = _seed()
    else:
        data = _seed()
    if not isinstance(data.get("nodes"), dict) or not isinstance(data.get("edges"), list):
        data = _seed()
    data.setdefault("refactoredHash", "")
    data["hash"] = _hash(data)
    return data


def ensure_current(name, risk=0):
    name = str(name or "").strip()
    if not name:
        return load()
    data = load()
    if name not in data["nodes"]:
        data["nodes"][name] = {"risk": _risk(risk),
                               "createdAt": time.time(), "updatedAt": time.time()}
        _save(data)
    return data


def risk_of(name):
    data = load()
    item = data["nodes"].get(str(name or "").strip())
    return int(item.get("risk") or 0) if item else None


def node_exists(name):
    return str(name or "").strip() in load()["nodes"]


def neighbors(name):
    name = str(name or "").strip()
    data = load()
    out = []
    for edge in data["edges"]:
        a, b = str(edge["a"]), str(edge["b"])
        if a == name:
            out.append((b, int(edge["seconds"])))
        elif b == name:
            out.append((a, int(edge["seconds"])))
    return out


def edge_seconds(a, b):
    a, b = str(a or "").strip(), str(b or "").strip()
    for edge in load()["edges"]:
        if {str(edge["a"]), str(edge["b"])} == {a, b}:
            return int(edge["seconds"])
    return None


def add_node(name, risk=1):
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "reason": "地点不能为空"}
    data = load()
    if name in data["nodes"]:
        return {"ok": False, "reason": f"地图已有 {name}"}
    data["nodes"][name] = {"risk": _risk(risk),
                           "createdAt": time.time(), "updatedAt": time.time()}
    _save(data)
    return {"ok": True, "created": name, "risk": _risk(risk), "hash": data["hash"]}


def set_risk(name, risk):
    name = str(name or "").strip()
    data = load()
    if name not in data["nodes"]:
        return {"ok": False, "reason": f"地图没有 {name}"}
    data["nodes"][name]["risk"] = _risk(risk)
    data["nodes"][name]["updatedAt"] = time.time()
    _save(data)
    return {"ok": True, "node": name, "risk": _risk(risk), "hash": data["hash"]}


def link(a, b, seconds):
    a, b = str(a or "").strip(), str(b or "").strip()
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 0
    if not a or not b or a == b:
        return {"ok": False, "reason": "两个地点不能相同或为空"}
    if seconds <= 0:
        return {"ok": False, "reason": "time 必须是正整数秒"}
    if seconds > MAX_EDGE_SECONDS:
        return {"ok": False, "reason": f"单段路最长 {MAX_EDGE_SECONDS} 秒；更远请拆成中间节点"}
    data = load()
    if a not in data["nodes"] or b not in data["nodes"]:
        return {"ok": False, "reason": "两个地点都要先在地图里"}
    for edge in data["edges"]:
        if {str(edge["a"]), str(edge["b"])} == {a, b}:
            edge["seconds"] = seconds
            _save(data)
            return {"ok": True, "linked": [a, b], "seconds": seconds, "updated": True,
                    "hash": data["hash"]}
    data["edges"].append({"a": a, "b": b, "seconds": seconds})
    _save(data)
    return {"ok": True, "linked": [a, b], "seconds": seconds, "updated": False,
            "hash": data["hash"]}


def unlink(a, b):
    a, b = str(a or "").strip(), str(b or "").strip()
    data = load()
    before = len(data["edges"])
    data["edges"] = [e for e in data["edges"]
                     if {str(e["a"]), str(e["b"])} != {a, b}]
    if len(data["edges"]) == before:
        return {"ok": False, "reason": f"{a} 与 {b} 之间没有连线"}
    _save(data)
    return {"ok": True, "unlinked": [a, b], "hash": data["hash"]}


def delete(name):
    name = str(name or "").strip()
    data = load()
    if name not in data["nodes"]:
        return {"ok": False, "reason": f"地图没有 {name}"}
    if neighbors(name):
        return {"ok": False, "reason": f"{name} 还有连线，先用 unlink 隔离"}
    data["nodes"].pop(name, None)
    _save(data)
    return {"ok": True, "deleted": name, "hash": data["hash"]}


def merge(keep, drop):
    keep, drop = str(keep or "").strip(), str(drop or "").strip()
    if keep == drop:
        return {"ok": False, "reason": "两个地点相同"}
    data = load()
    if keep not in data["nodes"] or drop not in data["nodes"]:
        return {"ok": False, "reason": "两个地点都要在地图里"}
    for edge in data["edges"]:
        a, b = str(edge["a"]), str(edge["b"])
        if drop in (a, b):
            other = b if a == drop else a
            if other == keep:
                continue
            edge["a"], edge["b"] = keep, other
    data["edges"] = [e for e in data["edges"] if str(e["a"]) != str(e["b"])]
    data["nodes"][keep]["risk"] = max(int(data["nodes"][keep].get("risk") or 0),
                                      int(data["nodes"][drop].get("risk") or 0))
    data["nodes"][keep]["updatedAt"] = time.time()
    data["nodes"].pop(drop, None)
    _save(data)
    return {"ok": True, "kept": keep, "merged": drop, "hash": data["hash"]}


def search_nodes(keyword, limit=8):
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        return []
    hits = []
    for name in load()["nodes"]:
        low = name.lower()
        if keyword in low or any(part.lower() == keyword for part in name.split("/")):
            hits.append(name)
    return hits[:limit]


def _node_risk(data, name):
    return int((data["nodes"].get(name) or {}).get("risk") or 0)


def routes(start, target, limit=MAX_ROUTES):
    """按时间 + 风险代价找前 limit 条简单路径。"""
    start, target = str(start or "").strip(), str(target or "").strip()
    data = load()
    if start not in data["nodes"] or target not in data["nodes"]:
        return {"ok": False, "reason": "起点或终点不在地图里", "routes": []}
    if start == target:
        return {"ok": True, "from": start, "to": target, "routes": []}
    graph = {}
    for edge in data["edges"]:
        a, b, sec = str(edge["a"]), str(edge["b"]), int(edge["seconds"])
        graph.setdefault(a, []).append((b, sec))
        graph.setdefault(b, []).append((a, sec))
    for name in graph:
        graph[name].sort()

    found = []
    worst = [float("inf")]

    def dfs(node, path, seconds, risk_sum, seen):
        if len(path) > MAX_HOPS:
            return
        for nxt, sec in graph.get(node, []):
            if nxt in seen:
                continue
            new_seconds = seconds + sec
            new_risk = risk_sum + _node_risk(data, nxt)
            cost = new_seconds + RISK_PENALTY * new_risk
            if cost >= worst[0]:
                continue
            new_path = path + [nxt]
            if nxt == target:
                found.append({"path": new_path, "seconds": new_seconds,
                              "max_risk": max(_node_risk(data, n) for n in new_path),
                              "cost": round(cost, 1)})
                found.sort(key=lambda x: x["cost"])
                del found[limit:]
                if len(found) >= limit:
                    worst[0] = found[-1]["cost"]
                continue
            dfs(nxt, new_path, new_seconds, new_risk, seen | {nxt})

    dfs(start, [start], 0, _node_risk(data, start), {start})
    return {"ok": True, "from": start, "to": target, "routes": found}


def _short_name(name):
    return str(name or "").strip().rsplit("/", 1)[-1]


def _name_ratio(a, b):
    import difflib
    return difflib.SequenceMatcher(None, _short_name(a), _short_name(b)).ratio()


def _neighbors_set(data, name):
    out = set()
    for edge in data["edges"]:
        a, b = str(edge["a"]), str(edge["b"])
        if a == name:
            out.add(b)
        elif b == name:
            out.add(a)
    return out


def duplicate_candidates():
    """疑似重复：名称相近，并且有边或共同邻居。远隔的同名地点不算。"""
    data = load()
    names = list(data["nodes"])
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ratio = _name_ratio(a, b)
            if ratio < 0.6:
                continue
            na, nb = _neighbors_set(data, a), _neighbors_set(data, b)
            connected = b in na or a in nb
            common = sorted(na & nb)
            if not connected and not common:
                continue
            out.append({"a": a, "b": b, "name_ratio": round(ratio, 2),
                        "connected": connected, "common": common[:5]})
    out.sort(key=lambda x: x["name_ratio"], reverse=True)
    return out


def validate():
    data = load()
    errors = []
    if not data["nodes"]:
        errors.append("地图没有节点")
    for name, item in data["nodes"].items():
        if not str(name).strip():
            errors.append("节点名为空")
        risk = item.get("risk")
        if not isinstance(risk, int) or risk < 0 or risk > 3:
            errors.append(f"{name} 的风险不是 0~3")
    seen = set()
    for edge in data["edges"]:
        a, b = str(edge.get("a") or ""), str(edge.get("b") or "")
        if a not in data["nodes"] or b not in data["nodes"]:
            errors.append(f"连线指向不存在的节点：{a} - {b}")
        if a == b:
            errors.append(f"节点连到自己：{a}")
        key = tuple(sorted((a, b)))
        if key in seen:
            errors.append(f"重复连线：{a} - {b}")
        seen.add(key)
        if int(edge.get("seconds") or 0) <= 0:
            errors.append(f"连线时间为 0：{a} - {b}")
    dups = duplicate_candidates()
    return {"ok": not errors, "errors": errors, "duplicates": dups,
            "hash": data["hash"]}


def changed_since_refactor():
    data = load()
    return bool(data.get("hash")) and data.get("hash") != data.get("refactoredHash")


def mark_refactored():
    data = load()
    _save(data, refactored=data["hash"])
    return data["hash"]


def apply_op(op, dream=False):
    """执行地图操作；dream=True 才允许改风险/断开/删除/合并。"""
    if not isinstance(op, dict):
        return {"ok": False, "reason": "op 不是对象"}
    action = str(op.get("action") or "").strip().lower()
    target = str(op.get("target") or "").strip()
    link_to = str(op.get("link") or "").strip()
    if action in ("unlink", "set_risk", "delete", "merge") and not dream:
        return {"ok": False, "reason": "unlink / set_risk / delete / merge 只在 dream 里开放"}
    if action == "query":
        if not target:
            return {"ok": False, "reason": "query 需要 target"}
        if not node_exists(target):
            matches = search_nodes(target)
            if matches:
                return {"ok": True, "found": False, "target": target,
                        "matches": matches,
                        "note": "没有精确节点；这些是名字相近的，先用准确的名字再查路线。"}
            return {"ok": True, "found": False, "target": target, "matches": [],
                    "note": "地图还没有这个地点；确认没有再 create。"}
        start = str(op.get("from") or "").strip() or _current()
        if start:
            ensure_current(start, 0)
        return routes(start, target)
    if action == "create":
        if not target:
            return {"ok": False, "reason": "create 需要 target"}
        risk = _risk(op.get("risk"))
        try:
            seconds = int(op.get("time") or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0:
            return {"ok": False, "reason": "create 需要 time（秒）"}
        if seconds > MAX_EDGE_SECONDS:
            return {"ok": False, "reason": f"单段路最长 {MAX_EDGE_SECONDS} 秒；更远请拆成中间节点"}
        created = add_node(target, risk)
        if not created.get("ok"):
            return created
        current = _current()
        if not current:
            delete(target)
            return {"ok": False, "reason": "不知道当前地点，无法连线"}
        ensure_current(current, 0)
        linked = link(current, target, seconds)
        if not linked.get("ok"):
            delete(target)
            return {"ok": False, "reason": linked.get("reason")}
        return {"ok": True, "created": target, "linked": [current, target],
                "seconds": seconds, "risk": risk, "hash": linked.get("hash")}
    if action == "link":
        return link(target, link_to, op.get("time"))
    if action == "unlink":
        return unlink(target, link_to)
    if action == "set_risk":
        return set_risk(target, op.get("risk"))
    if action == "delete":
        return delete(target)
    if action == "merge":
        return merge(target, link_to)
    return {"ok": False, "reason": f"未知 action：{action}"}


def _current():
    try:
        import timers
        return str(timers.load_character().get("local") or "").strip()
    except Exception:                       # noqa: BLE001
        return ""


def restore(data):
    """回滚到快照。"""
    _save(data)
    return load()
