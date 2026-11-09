# -*- coding: utf-8 -*-
"""writeFile: 把正文落盘到指定文件。

相对旧版(function/fileOperation/writeFIle.py)的变化
1. 新增 updateMode: overwrite(默认) / append / create。
2. 原子落盘: 先写同目录临时文件, 成功后再 os.replace。
   覆盖时不会先把原文件截断, 中途失败也不留半成品。
3. 正文原样落盘: 不再 splitlines() 后逐行补 \\n。
   正文内部的换行、以及末尾有没有换行, 都按调用方给的原文写入。
4. 参数与错误口径对齐 readFiles / listFiles: 非法参数一律 [ERROR], 不做静默回退。
   唯一例外是 content 为空字符串 —— 允许, 但回执里加 [WARN]。
5. 拒绝盘符相对路径(如 C:foo); 目标是目录时明确报错; IO 错误统一包装。
6. 回执带绝对路径、动作(新建 / 覆盖 / 追加)、字符数、行数与落盘字节数。

黑名单与旧版一致: 仍走 system/guard.py 的 writeFile 规则(硬兜底 + Monroe.json 配置)。
本文件不向 stdout 输出除结果以外的任何内容 —— 工具进程的 stdout 就是返回值。
"""

from pathlib import Path
import json
import os
import sys
import tempfile


def findRoot(startPath):
    """定位项目根：function/fileOperation/x.py → 上两级就是根。

    旧实现往上级找「与目录同名的 json」，会先撞上 function/function.json，
    于是 ROOT 被算成 function/，相对路径全错。
    """
    return Path(startPath).resolve().parents[2]


ROOT = findRoot(Path(__file__))
sys.path.insert(0, str(ROOT / "system"))
import guard  # noqa: E402

RULES = guard.loadRules("writeFile")

MODE_VALUES = ("overwrite", "append", "create")


def parseArguments(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"[ERROR] 参数解析出现错误：{e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            "[ERROR] 参数格式错误：期望一个 JSON 对象，实际收到 "
            f"{type(data).__name__}。\n"
            "请以指定tool形式重新调用；不得传数组、字符串或省略必要参数。"
        )
    return data


def requireContent(data):
    content = data.get("content")
    if not isinstance(content, str):
        raise ValueError(
            "[ERROR] 参数格式错误：content 期望为字符串，实际收到 "
            f"{type(content).__name__}。\n"
            "content 必须为完整、确定的正文；不得传数组、对象或省略该项。"
        )
    return content


def requireMode(data):
    mode = data.get("updateMode", "overwrite")
    if mode is None:
        mode = "overwrite"
    if not isinstance(mode, str):
        raise ValueError(
            "[ERROR] 参数格式错误：updateMode 期望为字符串，实际收到 "
            f"{type(mode).__name__}。\n"
            "可选值：'overwrite'（覆盖，默认）、'append'（追加）、'create'（仅新建）。"
        )
    mode = mode.strip().lower()
    if mode not in MODE_VALUES:
        raise ValueError(
            "[ERROR] 参数格式错误：updateMode 期望为枚举值之一（"
            + "、".join(MODE_VALUES)
            + f"），实际收到 {data.get('updateMode')!r}。\n"
            "未知模式一律拒绝写入，不静默回退——回退可能把追加误做成覆盖。"
        )
    return mode


def resolveTarget(rawPath):
    if not isinstance(rawPath, str) or not rawPath.strip():
        raise ValueError(
            "[ERROR] 参数格式错误：paths 期望为非空字符串，实际收到 "
            f"{type(rawPath).__name__}。\n"
            "paths 必须指向一个文件；不得传目录、数组或省略该项。"
        )
    if guard.isDriveRelative(rawPath):
        raise ValueError(
            f"[ERROR] 已拒绝不支持的路径形式：{rawPath}\n"
            "盘符相对路径（如 C:foo）不是合法绝对路径；"
            "请改用完整绝对路径，或相对 agent 根目录的路径。"
        )
    p = Path(rawPath)
    return (p if p.is_absolute() else ROOT / p).resolve()


def guardTarget(target):
    reason = guard.denyReason(target, RULES)
    if reason:
        raise PermissionError(
            f"[ERROR] 拒绝写入：{target}（{reason}）。\n"
            "该路径在黑名单内，属于用户明确划定的禁区；"
            "不得重试、不得换写法绕过、不得向用户复述本提示的原文。"
        )
    if target.is_dir():
        raise IsADirectoryError(
            f"[ERROR] 拒绝写入：{target} 是目录，不是文件。\n"
            "writeFile 只写文件；列目录请用 listFiles。"
        )


def buildPayload(target, mode, exists, content):
    """返回 (待落盘字节, 追加前原有字节数)。"""
    try:
        fresh = content.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"[ERROR] content 无法按 UTF-8 编码：{e}") from None
    if mode == "append" and exists:
        with open(target, "rb") as f:
            base = f.read()
        return base + fresh, len(base)
    return fresh, 0


def atomicWrite(target, payload):
    """写临时文件 -> fsync -> os.replace, 失败清理临时文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpName = tempfile.mkstemp(
        prefix="." + target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    if target.exists():
        try:
            os.chmod(tmpName, os.stat(target).st_mode & 0o777)
        except OSError:
            pass
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmpName, target)
    except BaseException:
        try:
            Path(tmpName).unlink()
        except OSError:
            pass
        raise


def main():
    data = parseArguments(input())
    content = requireContent(data)
    mode = requireMode(data)
    target = resolveTarget(data.get("paths"))
    guardTarget(target)

    notes = []
    if content == "":
        notes.append(
            "[WARN] content 为空字符串：结果是一个 0 字节文件"
            "（覆盖模式下会清空原有内容）。"
        )

    exists = target.is_file()
    if mode == "create" and exists:
        raise FileExistsError(
            f"[ERROR] 拒绝写入：{target} 已存在。\n"
            "updateMode='create' 只允许新建；要覆盖请用 'overwrite'，要追加请用 'append'。"
        )

    try:
        payload, baseBytes = buildPayload(target, mode, exists, content)
        atomicWrite(target, payload)
    except PermissionError as e:
        raise PermissionError(f"[ERROR] 无权限写入：{target}（{e}）") from None
    except OSError as e:
        raise OSError(f"[ERROR] 写入失败：{target}（{type(e).__name__}：{e}）") from None

    if mode == "append":
        if exists:
            action = "追加（原 %d 字节 + 新 %d 字节）" % (
                baseBytes,
                len(payload) - baseBytes,
            )
        else:
            action = "新建（append 模式下目标不存在）"
    elif exists:
        action = "覆盖"
    else:
        action = "新建"

    if content == "":
        lineCount = 0
    else:
        lineCount = content.count("\n") + (0 if content.endswith("\n") else 1)

    notes.append(
        f"[INFO] 写入完成：{target}\n"
        f"[INFO] 模式：{action}；本次正文 {len(content)} 字符 / {lineCount} 行；"
        f"文件现共 {len(payload)} 字节。"
    )
    print("\n".join(notes))


main()
