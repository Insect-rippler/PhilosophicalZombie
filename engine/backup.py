"""滚动备份：给会话与角色状态留退路。

每次服务启动、每次维护完成后各存一份，按天分目录，保留 N 天。
只备份小文件（会话、角色、计时器、事件标记），不动向量库和媒体。
"""

import shutil
import sys
import time
from pathlib import Path

import paths as _paths

FILES = ("session/session.jsonl", "realTime/character.json",
         "realTime/timers.json", "realTime/events.json",
         "realTime/maintenance.json")
KEEP_DAYS = 14

ROOT = _paths.ROOT


def _target_dir(now=None):
    """按天建目录、按时间建子目录——一天里可以存多份，互不覆盖。"""
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    clock = time.strftime("%H%M%S", time.localtime(now)) + f"-{int(time.time() * 1000) % 1000:03d}"
    return _paths.data("backup", day, clock)


def snapshot(tag="auto", now=None):
    """存一份快照；返回写入的文件数。"""
    dest = _target_dir(now)
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for rel in FILES:
        src = _paths.data(*rel.split("/"))
        if not src.exists():
            continue
        name = src.name
        target = dest / name
        if target.exists():
            continue
        try:
            shutil.copy2(src, target)
            written += 1
        except OSError:
            continue
    marker = dest / "tag.txt"
    if not marker.exists():
        marker.write_text(f"{tag} {time.strftime('%H:%M:%S')}\n", encoding="utf-8")
    prune()
    return written


def prune(now=None, keep_days=KEEP_DAYS):
    """删掉超过保留期的目录。"""
    base = _paths.data("backup")
    if not base.exists():
        return 0
    now = time.time() if now is None else float(now)
    removed = 0
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        try:
            stamp = time.mktime(time.strptime(folder.name, "%Y-%m-%d"))
        except ValueError:
            continue
        if (now - stamp) / 86400 > keep_days:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


def main(argv=None):
    n = snapshot(tag="manual")
    print(f"备份完成：{_target_dir()}（{n} 个文件）")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "engine"))
    raise SystemExit(main())
