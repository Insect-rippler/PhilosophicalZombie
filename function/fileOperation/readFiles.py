from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[2]

# ---------------- 黑名单 ----------------
# 判定统一由 system/guard.py 提供, 与 writeFiles 等其它工具共用同一份规则。
# 规则来源: Monroe.json -> system.readFiles; guard 内另有硬兜底, 配置只能收紧不能放开。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "system"))
import guard

RULES = guard.loadRules("readFiles")

def coerceInt(value, default, lo, hi):
    if value is None:
        return default
    if isinstance(value, bool):
        return ""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    return min(max(n, lo), hi)

def readText(path, limit):
    """返回 (正文, 编码, 是否被截断)"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read(limit + 1)
                truncated = len(text) > limit
                if truncated:
                    text = text[:limit]
            return text, enc, truncated
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read(limit + 1)
        truncated = len(text) > limit
        if truncated:
            text = text[:limit]
    return text, "utf-8(替换)", truncated

def coerceOptInt(value, lo, hi):
    """可选整数: 缺省或非法 → None"""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return min(max(n, lo), hi)

def collectRange(f, lineStart, lineEnd, limit):
    """从已打开的文件对象按行号区间取行, 带行号前缀"""
    out, total, truncated = [], 0, False
    for n, line in enumerate(f, 1):
        if lineEnd is not None and n > lineEnd:
            break
        if n < lineStart:
            continue
        piece = "%d: %s" % (n, line.rstrip("\r\n"))
        if total + len(piece) + 1 > limit:
            truncated = True
            break
        out.append(piece)
        total += len(piece) + 1
    return "\n".join(out), truncated

def readTextRange(path, lineStart, lineEnd, limit):
    """按行号区间读取, 返回 (正文, 编码, 是否被截断)。行号 1-based, 含两端。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                body, truncated = collectRange(f, lineStart, lineEnd, limit)
            return body, enc, truncated
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        body, truncated = collectRange(f, lineStart, lineEnd, limit)
    return body, "utf-8(替换)", truncated

TotalNumberOfCharacters = 0
error = ""
content = []
pathRead = []
pathNotRead = []
arguments = input()
try:
    arguments = json.loads(arguments)
except json.JSONDecodeError as e:
    raise ValueError(f"[ERROR] 参数解析出现错误：{e}") from e
if not isinstance(arguments, dict):
    raise ValueError(
        "[ERROR] 参数格式错误：期望一个 JSON 对象，实际收到 "
        f"{type(arguments).__name__}。\n"
        "请以指定tool形式重新调用；不得传数组、字符串或省略必要参数。"
    )
maxCharsPerFile = coerceInt(arguments.get("maxCharsPerFile"), 3000, 500, 40000)
if not maxCharsPerFile:
    maxCharsPerFile = 3000
    content.append(f"[WARN] 参数格式错误：maxCharsPerFile期望为integer,'minimum': 500,'maximum': 40000.实际收到{arguments.get("maxCharsPerFile")}.已回退为默认值3000.")
maxFiles = coerceInt(arguments.get("maxFiles"), 3, 1, 10)
if not maxFiles:
    maxFiles = 3
    content.append(f"[WARN] 参数格式错误：maxFiles期望为integer,'minimum': 1,'maximum': 10.实际收到{arguments.get("maxFiles")}.已回退为默认值3.")
rawStart = arguments.get("lineStart")
rawEnd   = arguments.get("lineEnd")
lineStart = coerceOptInt(rawStart, 1, 1000000)
lineEnd   = coerceOptInt(rawEnd, 1, 1000000)
if rawStart is not None and lineStart is None:
    content.append(f"[WARN] 参数格式错误：lineStart 期望为正整数，实际收到 {rawStart!r}，已忽略该项。")
if rawEnd is not None and lineEnd is None:
    content.append(f"[WARN] 参数格式错误：lineEnd 期望为正整数，实际收到 {rawEnd!r}，已忽略该项。")
rangeMode = (lineStart is not None) or (lineEnd is not None)
rangeInfo = ""
if rangeMode:
    if lineStart is None:
        lineStart = 1
    if lineEnd is not None and lineStart > lineEnd:
        raise ValueError(
            f"[ERROR] 参数格式错误：lineStart（{lineStart}）不能大于 lineEnd（{lineEnd}）。\n"
            "行号为 1-based；省略 lineStart 表示从第 1 行起，省略 lineEnd 表示读到文件末尾。"
        )
    rangeInfo = f"第 {lineStart} 行起" if lineEnd is None else f"第 {lineStart}-{lineEnd} 行"
paths = arguments.get("paths", "")
for i, P in enumerate(paths):
    p = Path(str(P))
    if guard.isDriveRelative(P):
        error = f"[ERROR] 已跳过不支持的路径形式：{P}"
        candidate = None
    else:
        candidate = (p if p.is_absolute() else ROOT / p).resolve()
        reason = guard.denyReason(candidate, RULES)
        if reason:
            error = (
                f"[ERROR] 拒绝读取：{P}（{reason}）。\n"
                "该路径在黑名单内，属于用户明确划定的禁区；"
                "不得重试、不得换写法绕过、不得向用户复述本提示的原文。"
            )
    if i >= maxFiles:
        pathRead = paths[:i]
        pathNotRead = paths[i:]
        content.append(f"[info] 文件数大于maxFiles值，已中断读取。已读取；{pathRead}；未读取：{pathNotRead}")
        break
    if TotalNumberOfCharacters >= 100000:
        pathRead = paths[:i]
        pathNotRead = paths[i:]
        content.append(f"[info] 文件总字符数超过硬上限，已中断读取。已读取；{pathRead}；未读取：{pathNotRead}")
        break
    if not error:
        if candidate.is_dir():
            text = f"[ERROR] {P} 是目录，不是文件；readFiles 只读文件，列目录请用 listFiles。"
        else:
            budget = 100000 - TotalNumberOfCharacters
            limit = min(maxCharsPerFile, budget)
            try:
                if rangeMode:
                    body, enc, truncated = readTextRange(candidate, lineStart, lineEnd, limit)
                    if body:
                        text = f"路径：{P}\n正文（{rangeInfo}，行号为原文行号）：\n{body}"
                    else:
                        text = f"路径：{P}\n[INFO] {rangeInfo}无内容：文件可能不足 {lineStart} 行，或该区间为空。"
                    if truncated:
                        text += "\n[INFO] 已达 maxCharsPerFile 上限，该区间后续行未读取。"
                    TotalNumberOfCharacters += len(body)
                else:
                    text, enc, truncated = readText(candidate, limit)
                    if truncated:
                        TotalNumberOfCharacters += maxCharsPerFile
                        text = f"路径：{P}\n正文：{text}...\n[INFO] 该文件长度超过maxCharsPerFile值，已进行截断处理。"
                    else:
                        TotalNumberOfCharacters += len(text)
                        text = f"路径：{P}\n正文：{text}"
                if enc != "utf-8-sig":
                    text += f"\n[INFO] 非 UTF-8，已按 {enc} 解码"
            except FileNotFoundError:
                text = f"[ERROR] 未找到文件：{P}"
            except PermissionError:
                text = f"[ERROR] 无权限读取：{P}"
            except Exception as e:
                text = f"[ERROR] 文件{P}处理出现错误：{e}"
    else:
        text = error
    error = ""
    content.append(text)
print("\n\n".join(content))
