# -*- coding: utf-8 -*-
"""路径解析与黑名单判定的统一入口。

当前为开放模式：不做路径/文件/通配/名称黑名单限制，
readFiles / writeFile 只借它解析路径。以后要收紧，只改这里。

约定：
  loadRules(section)  -> 规则字典（开放模式下为空）
  denyReason(resolved, rules) -> 拒绝理由，空串表示放行
  resolve(rawPath)    -> 解析成绝对路径
  isDriveRelative(rawPath) -> 是否是 "C:foo" 这种盘符相对路径
  check(rawPath, section) -> (绝对路径, 拒绝理由)
"""

import os
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent


def loadRules(section="readFiles"):
    """开放模式：无规则。保留接口，供脚本按原有写法调用。"""
    return {"paths": [], "files": [], "globs": [], "names": []}


def denyReason(resolved, rules):
    """开放模式：不拒绝任何路径。"""
    return ""


def resolve(rawPath):
    p = Path(str(rawPath))
    return (p if p.is_absolute() else AGENT_ROOT / p).resolve()


def isDriveRelative(rawPath):
    p = Path(str(rawPath))
    return bool(os.name == "nt" and p.drive and not p.is_absolute())


def check(rawPath, section="readFiles", rules=None):
    return resolve(rawPath), ""
