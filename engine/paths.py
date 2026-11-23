"""全项目统一的路径入口。

正式版默认使用 ROOT/state；PZ_DATA 可整体挪走，供本机调试。
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("PZ_DATA") or (ROOT / "state"))


def data(*parts):
    return DATA.joinpath(*parts)


def realm(*parts):
    card = os.environ.get("PZ_CARD_ROOT")
    if card:
        target = Path(card).joinpath(*parts)
        if target.exists():
            return target
    return (ROOT / "ConsciousnessRealm").joinpath(*parts)
