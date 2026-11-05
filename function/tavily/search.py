from pathlib import Path
import json, os
import requests
import re
import sys
url = "https://api.tavily.com/search"
# ---------------- 路径变量 ----------------
agent = Path(__file__).resolve().parents[2]
CFGPath = Path(agent).joinpath("state", "Philosophical.json")
configPath = Path(agent).joinpath("function", "tavily", "config.json")

# ---------------- 日期校对正则格式 ----------------
datepattern = r"\w{4}-\w{2}-\w{2}"
# ---------------- config校验表 ----------------
paramRules = {
    "search_depth": lambda v: v in {"basic", "advanced"},
    "chunks_per_source": lambda v: isinstance(v, int) and 1 <= v <= 3,
    "topic": lambda v: v in {"general", "news"},
    "max_results": lambda v: isinstance(v, int) and 1 <= v <= 20,
    "time_range": lambda v: v in {"day", "week", "month", "year"},
    "start_date": lambda v: bool(re.search(datepattern, str(v))),
    "end_date": lambda v: bool(re.search(datepattern, str(v))),
    "include_answer": lambda v: v in {"basic", "advanced", "false", True, False},
    "include_published_date": lambda v: v in {True, False},
    "filter_by_published_date": lambda v: v in {True, False},
    "include_raw_content": lambda v: v in {"markdown", "text", "false", True, False},
    "include_images": lambda v: v in {True, False},
    "include_image_descriptions": lambda v: v in {True, False},
    "include_favicon": lambda v: v in {True, False},
    "include_domains_mode": lambda v: v in {"restrict", "prefer"},
    "exact_match": lambda v: v in {True, False},
    "include_usage": lambda v: v in {True, False},
    "safe_search": lambda v: v in {True, False}
}
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
    raise ValueError(f"[Error] search api 未正确配置：缺少 Tavily API Key。请检查配置后重试，不得编造搜索结果或假装联网成功。")
# ---------------- config初始化 ----------------
try:
    with open(configPath,'r',encoding="utf-8") as f:
        config = json.load(f).get("search", {})
except FileNotFoundError:
    raise FileNotFoundError(f"[Error] 配置文件不存在：{configPath}。\n请检查路径是否正确、文件是否被移动或删除。\n不得编造配置内容，不得假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。")
except json.JSONDecodeError as e:
    raise RuntimeError(f"[Error] 配置文件解析失败：{configPath}：{e}。\n文件可能格式错误、包含非法字符或已损坏。\n请勿编造配置内容，勿假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。\n若可重试，请修正文件或稍后重试；若无法恢复，请向用户说明配置读取失败。") from e
if not config:
    raise ValueError("[Error] config search 读取失败。请检查配置文件路径与内容后重试，不得编造配置或假装读取成功。")
errorlist = []
for key, value in config.items():
    if value:
        if key == "include_domains":
            config[key] = value
            continue
        if key == "exclude_domains":
            config[key] = value
            continue
        if key in paramRules:
            validator = paramRules[key]
            if validator(value):
                config[key] = value
            else:
                errorlist.append(f"config search中参数 {key} 的值 {value} 不合法")
# ---------------- 接受外部参数 ----------------
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
for key, value in arguments.items():
    if key == "query":
        if not value:
            raise ValueError("[Error] query 不能为空。请提供有效查询内容后重试，不得编造查询或假装已获取搜索结果。")
        config[key] = value
        continue
    if key == "include_domains":
        config[key] = value
        continue
    if key == "exclude_domains":
        config[key] = value
        continue
    if key in paramRules:
        validator = paramRules[key]
        if validator(value):
            config[key] = value
        else:
            errorlist.append(f"[Error] 接受参数 {key} 的值 {value} 不合法。请检查该参数的类型、格式与取值范围，修正后重试。不得编造合法值，不得跳过校验，不得原样复述本警告。")
# ---------------- 取值归一化 ----------------
# true 落到具体取值; false/null/""/"false" 视为关闭(删键, 不发送)
if config.get("include_answer") is True or str(config.get("include_answer", "")).lower() == "true":
    config["include_answer"] = "basic"
if config.get("include_raw_content") is True or str(config.get("include_raw_content", "")).lower() == "true":
    config["include_raw_content"] = "markdown"
for key in [k for k in config if isOff(config[k])]:
    del config[key]
if not config.get("include_domains"):
    config["include_domains_mode"] = ""
payload = {}
for key,value in config.items():
    if value:
        payload[key] = value
if errorlist:
    raise ValueError(errorlist)
# ---------------- 组装并发送请求 ----------------
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer " + tavilyKey
}
payload = json.dumps(payload)
response = requests.post(url=url, headers=headers, data=payload, timeout=(10, 240))
if response.status_code != 200:
    print(f"[Error] HTTP {response.status_code}: {response.text}"); sys.exit(1)
else:
    # ---------------- 解析请求结果 ----------------
    response = response.json()
    checklist = {"title", "url", "content", "raw_content", "published_date"}
    answer = response.get("answer") or ""
    results = response.get("results", [])
    result = [
        {key: value for key, value in item.items() if key in checklist and value} 
        for item in results
    ]
    searchResults = {"answer": answer, "results":result}
    print(json.dumps(searchResults,ensure_ascii=False))