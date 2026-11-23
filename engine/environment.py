"""轻量环境：天气与气温。只给事件生成用，不进 state。"""

import json
import random
import time

import paths as _paths

ENV_PATH = _paths.data("realTime", "environment.json")

SEASON = {
    12: "冬", 1: "冬", 2: "冬",
    3: "春", 4: "春", 5: "春",
    6: "夏", 7: "夏", 8: "夏",
    9: "秋", 10: "秋", 11: "秋",
}
BASE_TEMP = {"冬": -2.0, "春": 10.0, "夏": 24.0, "秋": 9.0}
CONDITIONS = ("晴", "晴", "阴", "多云", "小雨", "风")
OFFSET = {"晴": 2.0, "阴": 0.0, "多云": 1.0, "小雨": -2.0, "风": -1.0}


def _default(now):
    month = time.localtime(now).tm_mon
    season = SEASON.get(month, "秋")
    return {"season": season, "condition": "晴", "temperatureC": BASE_TEMP[season],
            "updatedAt": 0}


def snapshot(now=None):
    now = time.time() if now is None else float(now)
    data = {}
    try:
        data = json.loads(ENV_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict) or not data:
        data = _default(now)
    season = data.get("season") or SEASON.get(time.localtime(now).tm_mon, "秋")
    stale = now - float(data.get("updatedAt") or 0) >= 3 * 3600
    if stale or "condition" not in data:
        condition = random.choice(CONDITIONS)
        hour = time.localtime(now).tm_hour
        diurnal = 4.0 if 10 <= hour <= 16 else -3.0
        temp = BASE_TEMP.get(season, 10.0) + diurnal + OFFSET.get(condition, 0.0)
        temp += random.uniform(-1.5, 1.5)
        data = {"season": season, "condition": condition,
                "temperatureC": round(temp, 1), "updatedAt": now}
        try:
            ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
            ENV_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        except OSError:
            pass
    return data
