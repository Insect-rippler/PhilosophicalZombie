"""升格系数：召回率 + 重要性。

召回率 = 召回次数 / 建库以来的检索次数（机会数）
score  = ln(p / (1 - p)) + alpha * (重要性 - 3) / 2

p 用加一平滑，避免小样本爆成 ±∞。
"""

import math
import time

DEFAULTS = {
    "alpha": 0.5,
    "promote": 0.0,
    "promote2": -0.2,
    "delete": -4.0,
    "stage1Opp": 50.0,
    "stage2Opp": 200.0,
    "deleteOpp": 100.0,
    "d2": 0.5,
}


def settings(cfg=None):
    out = dict(DEFAULTS)
    got = {}
    if cfg is not None:
        got = cfg
    else:
        try:
            import core
            got = core.harness.get("promotion") or {}
        except Exception:                   # noqa: BLE001
            got = {}
    for key in out:
        if key in got:
            out[key] = float(got[key])
    return out


def _ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return time.mktime(time.strptime(value, fmt))
            except ValueError:
                continue
    return None


def traces(record, now=None):
    """这条记忆被使用过的时间点。"""
    now = time.time() if now is None else float(now)
    got = [_ts(x) for x in (record.get("recallsAt") or [])]
    got = [x for x in got if x]
    born = _ts(record.get("createdAt") or record.get("created"))
    if not got:
        got = [born or now]
    return got


def opportunities(record, searches=None):
    """从建库到现在，这条记忆有多少次被检索的机会。"""
    if searches is not None:
        born = record.get("searchesAtCreation")
        if born is not None:
            return max(int(searches) - int(born), 1)
    got = record.get("opportunities")
    if got:
        return max(int(got), 1)
    born = _ts(record.get("createdAt") or record.get("created"))
    if born:
        return max(int((time.time() - born) / 86400.0 * 200), 1)   # 约每天 200 次检索
    return 1


def base(record, now=None, cfg=None):
    """ACT-R 基础激活：ln(Σ t^-d)，和升格同一套时间痕迹。"""
    s = settings(cfg)
    now = time.time() if now is None else float(now)
    total = 0.0
    for t in traces(record, now):
        days = max((now - t) / 86400.0, 0.0)
        total += (days + 0.1) ** (-s.get("d2", 0.5))
    return math.log(total) if total > 0 else -9.0


def rate(record, searches=None):
    recalls = float(record.get("recalls") or 0)
    opp = opportunities(record, searches)
    return (recalls + 1.0) / (opp + 2.0)


def score(record, cfg=None, searches=None):
    s = settings(cfg)
    p = min(max(rate(record, searches), 0.001), 0.999)
    base = math.log(p / (1.0 - p))
    importance = float(record.get("importance") or 0)
    return round(base + s["alpha"] * (importance - 3) / 2.0, 3)


def eligible(record, searches=None, stage=1, cfg=None):
    """系数达标且机会数够。"""
    s = settings(cfg)
    need = s["stage1Opp"] if stage < 2 else s["stage2Opp"]
    if opportunities(record, searches) < need:
        return False
    return verdict(score(record, cfg=cfg, searches=searches), cfg, stage) == "promote"


def verdict(value, cfg=None, stage=1):
    s = settings(cfg)
    promote = s["promote"]
    if stage >= 2:
        promote = s.get("promote2", promote + 0.5)
    if value >= promote:
        return "promote"
    if value <= s["delete"]:
        return "delete"
    return "keep"
