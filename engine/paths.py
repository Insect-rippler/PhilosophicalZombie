"""全项目统一的路径入口。

PZ_DATA 可把数据目录整体挪走（模拟器用），代码本身不关心挪到哪。
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("PZ_DATA") or (ROOT / "state"))


def data(*parts):
    """数据目录下的路径，例如 data("realTime", "events.json")。"""
    return DATA.joinpath(*parts)
