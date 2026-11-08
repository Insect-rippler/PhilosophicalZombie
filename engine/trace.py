"""全链路日志：任何模块都可以写一行结构化事件，便于事后追查。

    from trace import log
    log("advance", hours=1.2, metab={"sleepiness": 30})
"""

import json
import paths as _paths
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = _paths.data("tmp")
ENABLED = os.environ.get("PZ_TRACE", "1") != "0"
FILE = LOG_DIR / f"trace-{time.strftime('%Y%m%d')}.jsonl"


def log(event, **fields):
    if not ENABLED:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {"t": time.strftime("%H:%M:%S"), "e": event}
        record.update(fields)
        with FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
