"""运行日志：只给排查用，平时不读。

写到 state/logs/YYYY-MM-DD.jsonl，一行一个事件。
字段固定：t（时间）、e（事件）、ms（耗时）、其余按需。
"""

import json
import sys
import threading
import time
from pathlib import Path

import paths as _paths

_LOCK = threading.Lock()
OPEN = True


def _path():
    return _paths.data("logs", time.strftime("%Y-%m-%d") + ".jsonl")


def log(event, **fields):
    if not OPEN:
        return
    row = {"t": time.strftime("%H:%M:%S"), "e": event}
    row.update(fields)
    line = json.dumps(row, ensure_ascii=False, default=str)
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


class span:
    """with span("model.send", model=...): ...  自动记耗时与异常。"""

    def __init__(self, event, **fields):
        self.event = event
        self.fields = fields
        self.start = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = int((time.time() - self.start) * 1000)
        fields = dict(self.fields)
        fields["ms"] = ms
        if exc is not None:
            fields["error"] = f"{exc_type.__name__}: {exc}"[:200]
            log(self.event + ".fail", **fields)
            return False
        log(self.event, **fields)
        return False


def install_excepthook():
    def hook(exc_type, exc, tb):
        import traceback
        log("crash", error=f"{exc_type.__name__}: {exc}"[:200],
            stack="".join(traceback.format_exception(exc_type, exc, tb))[-1500:])
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook
