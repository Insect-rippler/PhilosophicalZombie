"""原子落盘：同一进程多线程也不会互相踩。

临时文件名带 进程+PID 线程+随机串，同进程的两个线程不会写到同一个 tmp；
每个目标路径一把锁，避免并发写同一份文件时后写覆盖先写的中间状态。
Windows 上杀毒/索引偶尔占用句柄，替换失败重试几次。
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

_LOCKS = {}
_GUARD = threading.Lock()


def _lock_for(path):
    key = str(Path(path).resolve())
    with _GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


def write_json(path, data, indent=2, retries=6):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp")
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    with _lock_for(path):
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return path
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    return path


def append_line(path, line, retries=6):
    """追加一行（带重试），用于 jsonl 日志。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        for attempt in range(retries):
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
