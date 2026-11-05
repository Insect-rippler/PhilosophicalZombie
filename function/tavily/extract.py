from pathlib import Path
import json, os
import requests
import sys
url = "https://api.tavily.com/extract"
# ---------------- 路径变量 ----------------
agent = Path(__file__).resolve().parents[2]
CFGPath = Path(agent).joinpath("state", "Philosophical.json")
configPath = Path(agent).joinpath("function", "tavily", "config.json")

# ---------------- config校验表 ----------------
paramRules = {
    "query": lambda v: isinstance(v, str) and v.strip() != "",
    "chunks_per_source": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5,
    "extract_depth": lambda v: v in {"basic", "advanced"},
    "include_images": lambda v: isinstance(v, bool),
    "include_favicon": lambda v: isinstance(v, bool),
    "format": lambda v: v in {"markdown", "text"},
    "timeout": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 60,
    "include_usage": lambda v: isinstance(v, bool)
}
capKeys = {"maxCharsPerSource", "maxTotalChars"}
# ---------------- 关闭值判定 ----------------
def isOff(v):
    if v is False or v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in {"", "false"}:
        return True
    return False
# ---------------- 读取凭据 ----------------
try:
    envCFG = json.loads(CFGPath.read_text(encoding='utf-8'))
except FileNotFoundError:
    raise FileNotFoundError(f"[Error] 配置文件不存在：{CFGPath}。\n请检查路径是否正确、文件是否被移动或删除。\n不得编造配置内容，不得假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。")
except json.JSONDecodeError as e:
    raise RuntimeError(f"[Error] 配置文件解析失败：{CFGPath}：{e}。\n文件可能格式错误、包含非法字符或已损坏。\n请勿编造配置内容，勿假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。\n若可重试，请修正文件或稍后重试；若无法恢复，请向用户说明配置读取失败。") from e
tavilyCFG = envCFG.get("tools", {}).get("tavily", {})
tavilyKey = os.environ.get("TAVILY_API_KEY") or tavilyCFG.get("api", "")
if not tavilyKey:
    raise ValueError(f"[Error] extract api 未正确配置：缺少 Tavily API Key。请检查配置后重试，不得编造抓取结果或假装联网成功。")
# ---------------- config初始化 ----------------
try:
    with open(configPath,'r',encoding="utf-8") as f:
        config = json.load(f).get("extract", {})
except FileNotFoundError:
    raise FileNotFoundError(f"[Error] 配置文件不存在：{configPath}。\n请检查路径是否正确、文件是否被移动或删除。\n不得编造配置内容，不得假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。")
except json.JSONDecodeError as e:
    raise RuntimeError(f"[Error] 配置文件解析失败：{configPath}：{e}。\n文件可能格式错误、包含非法字符或已损坏。\n请勿编造配置内容，勿假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。\n若可重试，请修正文件或稍后重试；若无法恢复，请向用户说明配置读取失败。") from e
if not config:
    raise ValueError("[Error] config extract 读取失败。请检查配置文件路径与内容后重试，不得编造配置或假装读取成功。")
def asInt(v, dv):
    try:
        return int(v)
    except (TypeError, ValueError):
        return dv
caps = {
    "maxCharsPerSource": max(0, asInt(config.pop("maxCharsPerSource", 20000), 20000)),
    "maxTotalChars": max(0, asInt(config.pop("maxTotalChars", 60000), 60000))
}
errorlist = []
for key, value in config.items():
    if not value:
        continue
    if key in paramRules:
        if paramRules[key](value):
            config[key] = value
        else:
            errorlist.append(f"config extract中参数 {key} 的值 {value} 不合法")
    else:
        errorlist.append(f"config extract中存在未知参数 {key}")
# ---------------- 接受外部参数 ----------------
arguments = sys.stdin.read().strip()
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
# ---------------- urls 归一化（字符串 → 单元素列表） ----------------
if "urls" not in arguments:
    raise ValueError("[Error] 缺少必填参数 urls。请传入 1-20 个合法链接后重试，不得编造 URL 或假装已抓取内容。")
if isinstance(arguments["urls"], str):
    arguments["urls"] = [arguments["urls"]]
if not isinstance(arguments["urls"], list) or not arguments["urls"]:
    raise ValueError("[Error] urls 不能为空。请传入 1-20 个合法 URL 后重试，不得编造 URL 或假装已抓取内容。")
if len(arguments["urls"]) > 20:
    raise ValueError(f"[Error] urls 最多 20 个，当前 {len(arguments['urls'])} 个。请分批抓取后重试。")
for u in arguments["urls"]:
    if not isinstance(u, str) or not u.strip().lower().startswith(("http://", "https://")):
        raise ValueError(f"[Error] urls 中存在非法项：{u}。每一项都必须是 http/https 开头的完整链接。")
for key, value in arguments.items():
    if key == "urls":
        continue
    if key in capKeys:
        raise ValueError(f"[Error] 参数 {key} 不由调用方设置，请在 config extract 中调整。")
    if key in paramRules:
        if paramRules[key](value):
            config[key] = value
        else:
            errorlist.append(f"[Error] 接受参数 {key} 的值 {value} 不合法。请检查该参数的类型、格式与取值范围，修正后重试。不得编造合法值，不得跳过校验，不得原样复述本警告。")
    else:
        errorlist.append(f"[Error] 未知参数 {key}。本 tool 不接受该参数，请核对参数名后重试。")
# ---------------- 取值归一化 ----------------
if config.get("include_answer"):
    del config["include_answer"]
if not config.get("query"):
    config.pop("chunks_per_source", None)          # chunks_per_source 仅在带 query 时生效
for key in [k for k in config if isOff(config[k])]:
    del config[key]
if errorlist:
    raise ValueError(errorlist)
# ---------------- 组装并发送请求 ----------------
payload = {"urls": arguments["urls"]}
for key, value in config.items():
    if value:
        payload[key] = value
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer " + tavilyKey
}
response = requests.post(url=url, headers=headers, data=json.dumps(payload), timeout=(10, 240))
if response.status_code != 200:
    print(f"[Error] HTTP {response.status_code}: {response.text}"); sys.exit(1)
# ---------------- 解析请求结果 ----------------
response = response.json()
checklist = {"url", "raw_content", "images", "favicon"}
total = 0
results = []
clipped = False
for item in response.get("results", []):
    keep = {k: v for k, v in item.items() if k in checklist and v}
    raw = keep.get("raw_content")
    if isinstance(raw, str) and caps["maxCharsPerSource"] and len(raw) > caps["maxCharsPerSource"]:
        keep["raw_content"] = raw[:caps["maxCharsPerSource"]] + f"...[原文共 {len(raw)} 字符, 已截断]"
        clipped = True
    if caps["maxTotalChars"] and total + len(json.dumps(keep, ensure_ascii=False)) > caps["maxTotalChars"]:
        clipped = True
        break
    total += len(json.dumps(keep, ensure_ascii=False))
    results.append(keep)
extractResults = {
    "results": results,
    "failed_results": response.get("failed_results", []),
    "response_time": response.get("response_time"),
    "truncated": clipped
}
print(json.dumps(extractResults, ensure_ascii=False))
