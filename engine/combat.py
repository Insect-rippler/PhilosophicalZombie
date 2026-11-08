"""回合制战斗结算。纯函数，不调模型，不认识人名和世界观。

数据分两包：角色包（属性 + 技能）和敌人包（属性 + 技能）。
换角色或换世界只换数据，这个文件不动。

一回合的顺序：她选技能 → 结算 → 敌人选动作 → 结算。
三到五个回合上限，打不完按血量判成败。hp 归零是重伤，不是死。
"""

import json
import paths as _paths
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "ConsciousnessRealm" / "world"
STATE = _paths.data("realTime", "battle.json")

MAX_ROUNDS = 25      # 兜底上限，真正的约束是敌人的血量


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_world():
    name = _read(WORLD / "active.json", {}).get("world") or ""
    root = WORLD / name if name else None
    return root if root and root.exists() else None


def load_enemies():
    root = load_world()
    return {e["id"]: e for e in (_read(root / "enemies.json", {}).get("enemies") or []
                                 if root else [])} if root else {}


def load_character_pack(ident="viviana"):
    root = ROOT / "ConsciousnessRealm" / "characters" / ident
    return _read(root / "combat.json", {})


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def new_battle(pack, enemy, rng=None):
    rng = rng or random
    attrs = dict(pack.get("attributes") or {})
    # 治疗次数按难度给：难度越高，允许的处理次数越多（不做背包）
    heals = {1: 1, 2: 2, 3: 3, 4: 4}.get(int(enemy.get("tier") or 1), 1)
    foe = dict(enemy.get("attributes") or {})
    battle = {
        "heals": {"left": heals, "max": heals, "ratio": 0.35},
        "startedAt": time.time(),
        "round": 0,
        "over": False,
        "result": "",
        "player": {"isPlayer": True, "name": pack.get("name") or "她", "hp": attrs.get("hp", 100),
                   "maxHp": attrs.get("hp", 100), "sp": 0,
                   "attrs": attrs, "skills": pack.get("skills") or [],
                   "cooldown": {}, "shield": 0, "log": [],
                   },  # 治疗次数挂在 battle["heals"] 上
        "enemy": {"name": enemy.get("name") or "它", "hp": foe.get("hp", 100),
                  "maxHp": foe.get("hp", 100), "sp": 0,
                  "attrs": foe, "skills": enemy.get("skills") or [],
                  "cooldown": {}, "shield": 0},
    }
    order = _initiative(battle, rng)
    battle["playerFirst"] = order
    return battle


def _initiative(battle, rng):
    ps = battle["player"]["attrs"].get("speed", 10) + rng.randint(0, 10)
    es = battle["enemy"]["attrs"].get("speed", 10) + rng.randint(0, 10)
    return ps >= es


def usable(battle):
    """她这回合能用的技能。"""
    me = battle["player"]
    out = []
    for skill in me["skills"]:
        if me["cooldown"].get(skill["id"], 0) > 0:
            continue
        if (skill.get("cost") or 0) > me["sp"]:
            continue
        out.append({"id": skill["id"], "name": skill.get("name", ""),
                    "type": skill.get("type", "attack"),
                    "cost": skill.get("cost", 0)})
    return out


CRIT_CHANCE = 0.15
CRIT_MULT = 1.8


def damage(attack, power, defense, resist=0, rng=None):
    rng = rng or random
    base = attack * float(power or 1.0) * rng.uniform(0.85, 1.15)
    crit = rng.random() < CRIT_CHANCE
    if crit:
        base *= CRIT_MULT
    after = base - float(defense or 0)          # 减防是全额，法抗另算
    if float(resist or 0):
        after *= 1 - min(0.9, float(resist) / 100.0)
    return int(max(round(base * 0.05), round(after))), crit   # 保底 5% 攻击力


def _use(actor, target, skill, rng, log):
    actor["sp"] = max(0, actor["sp"] - (skill.get("cost") or 0))
    cd = skill.get("cooldown") or 0
    if cd:
        actor["cooldown"][skill["id"]] = cd + 1
    effect = skill.get("effect") or {}
    shield = effect.get("shield")
    if shield:
        actor["shield"] = actor.get("shield", 0) + int(shield)
        log.append(f"{actor['name']}架住了，护盾 +{int(shield)}")
        return
    dmg, crit = damage(actor["attrs"].get("atk", 10), skill.get("power"),
                       target["attrs"].get("def", 0), target["attrs"].get("res", 0), rng)
    if target.get("isPlayer") and target["hp"] - dmg <= 0:
        dmg = max(0, target["hp"] - 1)      # 最后一击留一口气，让治疗/救援有机会介入
    absorbed = min(target.get("shield", 0), dmg)
    if absorbed:
        target["shield"] -= absorbed
        dmg -= absorbed
    target["hp"] = max(0, target["hp"] - dmg)
    mark = "｜暴击｜" if crit else ""
    log.append(f"{actor['name']}用了{skill.get('name', '一击')}{mark}，造成 {dmg} 点伤害"
               f"（剩余 {target['hp']}/{target['maxHp']}）")
    debuff = effect.get("debuff") or {}
    if debuff and "def" in debuff:
        target["attrs"]["def"] = max(0, target["attrs"].get("def", 0) + debuff["def"])
        log.append(f"{target['name']}的防御降低了")


def _enemy_pick(enemy, rng):
    pool = [s for s in enemy["skills"] if enemy["cooldown"].get(s["id"], 0) <= 0
            and (s.get("cost") or 0) <= enemy["sp"]]
    if not pool:
        return {"id": "basic", "name": "扑咬", "power": 1.0, "cost": 0}
    return pool[rng.randrange(len(pool))]


def _player_default(battle):
    for skill in battle["player"]["skills"]:
        if (skill.get("type") or "attack") == "attack":
            return skill
    return {"id": "basic", "name": "随手一击", "power": 1.0, "cost": 0}


def act(battle, skill_id=None, rng=None):
    """走一回合。skill_id 为空时用第一个可用的攻击技能。"""
    rng = rng or random
    if battle.get("over"):
        return battle, ["战斗已经结束了。"]

    me, foe = battle["player"], battle["enemy"]
    log = []
    picked = next((s for s in me["skills"] if s["id"] == skill_id), None)
    if not picked:
        picked = _player_default(battle)

    def player_turn():
        _use(me, foe, picked, rng, log)

    def enemy_turn():
        _use(foe, me, _enemy_pick(foe, rng), rng, log)

    if battle.get("playerFirst"):
        player_turn()
        if foe["hp"] > 0:
            enemy_turn()
    else:
        enemy_turn()
        if me["hp"] > 0:
            player_turn()

    battle["round"] += 1
    for actor in (me, foe):
        actor["sp"] = min(12, actor["sp"] + 2)
        for key in list(actor["cooldown"]):
            actor["cooldown"][key] -= 1

    if foe["hp"] <= 0:
        battle["over"], battle["result"] = True, "win"
    elif me["hp"] <= 0:
        battle["over"], battle["result"] = True, "lose"
    elif battle["round"] >= MAX_ROUNDS:
        battle["over"] = True
        battle["result"] = "win" if me["hp"] / me["maxHp"] > foe["hp"] / foe["maxHp"] else "lose"

    battle["log"] = (battle.get("log") or []) + log
    return battle, log


CRITICAL_RATIO = 0.25


def is_critical(battle):
    me = battle["player"]
    return me["hp"] > 0 and me["hp"] / max(1, me["maxHp"]) < CRITICAL_RATIO


def heal(battle):
    """用一次治疗。次数按敌人难度给，不设背包，回了就没了。"""
    me = battle["player"]
    slot = battle.setdefault("heals", {"left": 0, "max": 0, "ratio": 0.35})
    if slot.get("left", 0) <= 0:
        return ["已经没有余力再处理伤口了。"], False
    slot["left"] -= 1
    amount = int(me["maxHp"] * float(slot.get("ratio") or 0.35))
    before = me["hp"]
    me["hp"] = min(me["maxHp"], me["hp"] + amount)
    log = [f"{me['name']}退开半步，草草处理了伤口，回了 {me['hp'] - before} 点"
           f"（{me['hp']}/{me['maxHp']}，还剩 {slot['left']} 次）"]

    # 治疗占掉一回合：处理伤口的时候，对面不会等她
    foe = battle["enemy"]
    if foe["hp"] > 0:
        _use(foe, me, _enemy_pick(foe, battle["rng"] if battle.get("rng") else random), random, log)
    battle["round"] += 1
    for actor in (me, foe):
        actor["sp"] = min(12, actor["sp"] + 2)
        for key in list(actor["cooldown"]):
            actor["cooldown"][key] -= 1
    battle["log"] = (battle.get("log") or []) + log
    if me["hp"] <= 0:
        battle["over"], battle["result"] = True, "lose"
    return log, True


def rescue(battle):
    """用户介入：强制结束战斗，把人救回来。委托不因此失败。"""
    me = battle["player"]
    me["hp"] = max(me["hp"], int(me["maxHp"] * 0.35))
    battle["over"] = True
    battle["result"] = "rescue"
    battle["log"] = (battle.get("log") or []) + [
        f"有人赶到，把{me['name']}从里面带了出来。"]
    return battle


def status(battle):
    me, foe = battle["player"], battle["enemy"]
    return {
        "round": battle["round"],
        "over": battle["over"],
        "result": battle["result"],
        "player": {"hp": me["hp"], "maxHp": me["maxHp"], "sp": me["sp"],
                   "shield": me.get("shield", 0)},
        "enemy": {"name": foe["name"], "hp": foe["hp"], "maxHp": foe["maxHp"]},
        "skills": usable(battle),
        "critical": is_critical(battle),
        "heals": battle.get("heals") or {"left": 0, "max": 0},
    }


def apply_result(battle):
    """把战果落到她的生理上。赢了也累，输了是重伤。"""
    from engine import timers
    ch = timers.load_character()
    me = battle["player"]
    lost = me["maxHp"] - me["hp"]
    ratio = lost / max(1, me["maxHp"])
    ch["metabolism"]["hp"] = max(1, round(me["maxHp"] * 0.55 + me["hp"] * 0.45))
    ch["metabolism"]["fatigue"] = _clamp(float(ch["metabolism"].get("fatigue", 0)) + 35, 0, 200)
    if battle["result"] == "lose" or ratio >= 0.6:
        ch["metabolism"]["health"] = _clamp(float(ch["metabolism"].get("health", 100)) - 25, 0, 100)
    timers.save_character(ch)
    return {"lost": lost, "ratio": round(ratio, 2)}


def save(battle):
    path = Path(STATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(battle, ensure_ascii=False, indent=2), encoding="utf-8")


def load():
    data = _read(STATE, None)
    return data if isinstance(data, dict) else None


def clear():
    try:
        Path(STATE).unlink()
    except OSError:
        pass
