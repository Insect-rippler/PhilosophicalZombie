"""委托：按强度抽敌人，接下之后她可以去委托地点打。

池子不单独维护：委托写 category + difficulty，抽的就是 enemies.json 里
同类别、同 tier 的敌人。要加敌人不用改这里。
"""

import json
import paths as _paths
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = _paths.data("realTime", "commissions.json")
WORLD = ROOT / "ConsciousnessRealm" / "world"

from engine import combat


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def world_dir():
    name = _read(WORLD / "active.json", {}).get("world") or ""
    root = WORLD / name if name else None
    return root if root and root.exists() else None


def templates():
    root = world_dir()
    return (_read(root / "commissions.json", {}).get("commissions") or []) if root else []


def active_commissions():
    data = _read(STATE, [])
    return data if isinstance(data, list) else []


def pool(category, difficulty):
    return [e for e in combat.load_enemies().values()
            if e.get("category") == category and int(e.get("tier") or 1) == int(difficulty)]


def accept(template_id, rng=None):
    rng = rng or random
    tpl = next((t for t in templates() if t["id"] == template_id), None)
    if not tpl:
        return None
    choices = pool(tpl["category"], tpl["difficulty"])
    if not choices:
        return None
    enemy = rng.choice(choices)
    item = {"id": f"c-{time.time_ns() // 1000}", "template": tpl["id"],
            "title": tpl.get("title", ""), "place": tpl.get("place", ""),
            "difficulty": tpl["difficulty"], "enemy": enemy["id"],
            "enemyName": enemy.get("name", ""),
            "rewardText": tpl.get("rewardText") or "", "reward": tpl.get("reward") or {},
            "acceptedAt": time.time(), "done": False}
    data = active_commissions()
    data.append(item)
    _write(STATE, data)
    return item


def current():
    for item in reversed(active_commissions()):
        if not item.get("done"):
            return item
    return None


def finish(commission_id):
    data = active_commissions()
    for item in data:
        if item.get("id") == commission_id:
            item["done"] = True
    _write(STATE, data)
