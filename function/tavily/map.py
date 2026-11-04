from pathlib import Path
import json
import requests
import sys
url = "https://api.tavily.com/map"
# ---------------- 路径变量 ----------------
agent = Path(__file__).resolve().parents[2]
CFGPath = Path(agent).joinpath(f"{Path(agent).name}.json")
configPath = Path(agent).joinpath("function", "tavily", "config.json")

# ---------------- config校验表 ----------------
paramRules = {
    "instructions": lambda v: isinstance(v, str) and v.strip() != "",
    "max_depth": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5,
    "max_breadth": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 500,
    "limit": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 500,
    "select_paths": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "select_domains": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "exclude_paths": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "exclude_domains": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "allow_external": lambda v: isinstance(v, bool),
    "timeout": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 10 <= v <= 150,
    "include_usage": lambda v: isinstance(v, bool)
}
capKeys = {"maxResults"}
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
tavilyCFG = envCFG.get("config", {}).get("tavily", {})
tavilyKey = tavilyCFG.get("api", "")
if not tavilyKey:
    raise ValueError(f"[Error] map api 未正确配置：缺少 Tavily API Key。请检查配置后重试，不得编造站点地图或假装联网成功。")
# ---------------- config初始化 ----------------
try:
    with open(configPath,'r',encoding="utf-8") as f:
        config = json.load(f).get("map", {})
except FileNotFoundError:
    raise FileNotFoundError(f"[Error] 配置文件不存在：{configPath}。\n请检查路径是否正确、文件是否被移动或删除。\n不得编造配置内容，不得假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。")
except json.JSONDecodeError as e:
    raise RuntimeError(f"[Error] 配置文件解析失败：{configPath}：{e}。\n文件可能格式错误、包含非法字符或已损坏。\n请勿编造配置内容，勿假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。\n若可重试，请修正文件或稍后重试；若无法恢复，请向用户说明配置读取失败。") from e
if not config:
    raise ValueError("[Error] config map 读取失败。请检查配置文件路径与内容后重试，不得编造配置或假装读取成功。")
def asInt(v, dv):
    try:
        return int(v)
    except (TypeError, ValueError):
        return dv
caps = {"maxResults": max(0, asInt(config.pop("maxResults", 200), 200))}
errorlist = []
for key, value in config.items():
    if not value:
        continue
    if key in paramRules:
        if paramRules[key](value):
            config[key] = value
        else:
            errorlist.append(f"config map中参数 {key} 的值 {value} 不合法")
    else:
        errorlist.append(f"config map中存在未知参数 {key}")
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
target = arguments.get("url")
if not isinstance(target, str) or not target.strip():
    raise ValueError("[Error] url 不能为空。请提供要测绘的根站点地址后重试，不得编造站点或假装已测绘。")
if "://" not in target:
    target = "https://" + target.strip().strip("/")
for key, value in arguments.items():
    if key == "url":
        continue
    if key in capKeys:
        raise ValueError(f"[Error] 参数 {key} 不由调用方设置，请在 config map 中调整。")
    if key in paramRules:
        if paramRules[key](value):
            config[key] = value
        else:
            errorlist.append(f"[Error] 接受参数 {key} 的值 {value} 不合法。请检查该参数的类型、格式与取值范围，修正后重试。不得编造合法值，不得跳过校验，不得原样复述本警告。")
    else:
        errorlist.append(f"[Error] 未知参数 {key}。本 tool 不接受该参数，请核对参数名后重试。")
# ---------------- 取值归一化 ----------------
for key in [k for k in config if isOff(config[k])]:
    del config[key]
if errorlist:
    raise ValueError(errorlist)
# ---------------- 组装并发送请求 ----------------
payload = {"url": target}
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
results = response.get("results", [])
truncated = False
if caps["maxResults"] and len(results) > caps["maxResults"]:
    results = results[:caps["maxResults"]]
    truncated = True
mapResults = {
    "base_url": response.get("base_url"),
    "results": results,
    "total": len(response.get("results", [])),
    "response_time": response.get("response_time"),
    "truncated": truncated
}
print(json.dumps(mapResults, ensure_ascii=False))
