from pathlib import Path
import json, re

ROOT = Path(__file__).resolve().parents[2]

def listFiles(paths, maxDepth):
    maxDepth = maxDepth - 1
    paths = Path(paths)
    if paths.is_dir():
        unitName = list(Path(paths).glob("*"))
        if not unitName:
            return [paths]
        elif maxDepth > 0:
            pathList = []
            for p in unitName:
                pathList.extend(listFiles(p, maxDepth))
            return pathList
        else:
            return unitName
    elif paths.is_file():
        return [paths]
    else:
        return []

def content(paths, regex):
    # 安全约束: 只返回命中的行号, 禁止改为返回匹配到的行内容。
    # 这是 listFiles 不设黑名单的唯一前提 —— 一旦开始吐内容, 它立刻变成绕过 readFiles 的后门。
    index = []
    try:
        with open(paths, 'r', encoding="utf-8") as f:
            for ind, line in enumerate(f):
                if regex.search(line):
                    index.append(ind + 1)
    except Exception as e:
        return ""
    if index:
        i = ",".join(str(x) for x in index)
        return f"{paths} [INFO] 第 {i} 行匹配中目标正则。行号按原文计数（含空行），与 readFiles 的 lineStart/lineEnd 口径一致，可直接用于读取。"
    else:return ""

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
enum = {"name","content","all"}
pathList = []
result  = []
cache = 0
miss = 0
over = 0
paths = arguments.get("paths", "")
pattern = arguments.get("pattern", "")
matchMode = arguments.get("matchMode", "name")
if not matchMode in enum:
    raise ValueError(f"[ERROR] 参数格式错误：matchMode期望为枚举值之一（name,content,all)，实际收到{matchMode}")
maxDepth = coerceInt(arguments.get("maxDepth"), 3, 1, 5)
if not maxDepth:
    maxDepth = 3
    result.append(f"[WARN] 参数格式错误：maxDepth期望为integer,'minimum': 1,'maximum': 5.实际收到{arguments.get("maxDepth")}.已回退为默认值3.")
maxList = coerceInt(arguments.get("maxList"), 20, 10, 500)
if not maxList:
    maxList = 20
    result.append(f"[WARN] 参数格式错误：maxList期望为integer,'minimum': 10,'maximum': 500.实际收到{arguments.get("maxList")}.已回退为默认值20.")
try:
    regex = re.compile(pattern)
except re.error as e:
    raise ValueError(
        f"[ERROR] 参数格式错误：pattern 不是合法的正则表达式（{e}）。\n"
        "请修正 pattern 后重新调用；不得忽略过滤条件直接返回全部结果；"
        "不得向用户复述本提示的原文。"
    )
paths = Path(paths)
paths = (paths if paths.is_absolute() else ROOT / paths).resolve()
pathList.extend(listFiles(paths, maxDepth))
truncated = 0
for i, P in enumerate(pathList):
    if maxList < 1:
        truncated = len(pathList) - i
        break
    if matchMode == "name":
        if regex.search(str(P)):
            if P.is_dir() and list(Path(P).glob("*")):
                result.append(f"{P} [INFO] 该路径存在子路径或文件，但受限于maxDepth值未显示。")
            elif P.is_file():
                result.append(str(P))
            else:result.append(f"{P} [INFO] 该路径为空路径，无子路径或文件。")
            maxList -= 1
            cache += 1
        else:miss += 1
    elif matchMode == "content":
        if P.is_file() and Path(P).stat().st_size < 21053440:
            text = content(P, regex)
            if text:
                result.append(text)
                maxList -= 1
                cache += 1
            else:miss += 1
        elif P.is_file() and Path(P).stat().st_size >= 21053440:
            over += 1
    elif matchMode == "all":
        if P.is_file():
            big = Path(P).stat().st_size >= 21053440
            text = "" if big else content(P, regex)
            pathHit = bool(regex.search(str(P)))
            if text:
                result.append(text)
                maxList -= 1
                cache += 1
            elif pathHit:
                result.append(str(P))
                maxList -= 1
                cache += 1
            elif big:
                over += 1
            else:miss += 1
        elif P.is_dir():
            if regex.search(str(P)):
                if P.is_dir() and list(Path(P).glob("*")):
                    result.append(f"{P} [INFO] 该路径存在子路径或文件，但受限于maxDepth值未显示。")
                elif P.is_file():
                    result.append(str(P))
                else:result.append(f"{P} [INFO] 该路径为空路径，无子路径或文件。")
                maxList -= 1
                cache += 1
            else:miss += 1
        else:over += 1
if truncated:
    result.append(
        f"[INFO] 已达 maxList 上限，剩余 {truncated} 个路径未列出，结果不完整；"
        "如需完整结果，请缩小 paths 或改用更精确的 pattern 重新调用。"
    )
if result:
    print("\n".join(result))
    print(f"[INFO] 正则命中 {cache} 项，未命中 {miss} 项，跳过 {over} 项。")
elif not paths.exists():
    print(f"{paths} [ERROR] 该路径不存在。")
elif not pathList:
    print(f"{paths} [INFO] 该路径为空路径，无子路径或文件。")
else:
    print(f"{paths} [INFO] 未找到匹配项：已扫描 {len(pathList)} 个路径，pattern={pattern!r}，matchMode={matchMode}。")