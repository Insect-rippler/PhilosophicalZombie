from pathlib import Path
import json, time, os, shutil

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "state" / "Philosophical.json"

timeSession = time.strftime("%Y-%m-%d %H:%M:%S")
timeNow = time.strftime("%y-%m-%d")

sessionPath = ROOT / r"state\session\session.jsonl"
sessionTempPath = sessionPath.with_name(f"{sessionPath.stem}.{os.getpid()}.tmp")

historyPath = sessionPath.resolve().parents[1].joinpath("history", f"{timeNow}.jsonl")
historyTempPath  = historyPath.with_suffix(f".{os.getpid()}.tmp")
historyPath.parent.mkdir(parents=True, exist_ok=True)

def textOf(obj):
    c = obj.get("content")
    if isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") in ("input_text", "output_text"):
                return p.get("text", "")
        return ""
    return c if isinstance(c, str) else ""

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

content = arguments.get("content", "")
retained = []
archived = []
try:
    SS_CFG = json.loads(CFG.read_text(encoding='utf-8'))
except FileNotFoundError:
    raise FileNotFoundError(f"[Error] 配置文件不存在：{CFG}。\n请检查路径是否正确、文件是否被移动或删除。\n不得编造配置内容，不得假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。")
except json.JSONDecodeError as e:
    raise RuntimeError(f"[Error] 配置文件解析失败：{CFG}：{e}。\n文件可能格式错误、包含非法字符或已损坏。\n请勿编造配置内容，勿假装读取成功。\n应以角色身份表达困惑、停顿或询问，不得原样复述本警告。\n若可重试，请修正文件或稍后重试；若无法恢复，请向用户说明配置读取失败。") from e
retainedTrun = max(1, int(SS_CFG.get("system", {}).get("session", {}).get("retainedTrun", 7)))

sliceGroup = []
allGroups = []
contentNew = f"[INFO] session 历史提要：{content}（内部参考，不主动复述，不得当作当前对话，不得编造未记录内容。）"
entry = {"type": "message", "role": "system", "content": [{"type": "input_text","text": contentNew}], "timestamp": timeSession}
# ---------------- 对session按user进行粗切 ----------------
# 去掉开头压缩概要，以及上轮压缩遗留的连续 user（只留最后一个）
lines = []
if sessionPath.exists():
    with open(sessionPath, 'r', encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
k = 0
# ① 丢掉开头那条提要
if k < len(lines) and lines[k].get("role") == "system" and "session 历史提要：" in textOf(lines[k]):
    k += 1
# ② 连续 user 只留最后一个
while k + 1 < len(lines) and lines[k].get("role") == "user" and lines[k + 1].get("role") == "user":
    k += 1

for obj in lines[k:]:
    if obj.get("role") == "user":
        if sliceGroup:
            allGroups.append(sliceGroup)
        sliceGroup = [obj]
    else:
        sliceGroup.append(obj)
if sliceGroup:
    allGroups.append(sliceGroup)
retained = allGroups[-retainedTrun:]
archived = allGroups[:-retainedTrun]
# ---------------- 对retained按reasoning进行细切 ----------------
flat = [it for g in retained for it in g]        # 展平，旧→新

keep, dropped, i = [], [], 0
for it in reversed(flat):                        # 从新往旧扫
    if i >= retainedTrun and it.get("role") != "user":
        dropped.append(it)                       # 超出的旧往返：归档
        continue
    keep.append(it)
    if it.get("type") == "reasoning":
        i += 1
keep.reverse()
dropped.reverse()

if historyPath.exists():
    shutil.copy(historyPath, historyTempPath)
with open(historyTempPath, 'a', encoding="utf-8") as f:
    for item in archived:
        for line in item:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
with open(historyTempPath, 'a', encoding="utf-8") as f:
    for it in dropped:
        f.write(json.dumps(it, ensure_ascii=False) + "\n")

with open(sessionTempPath, 'w', encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    for it in keep:
        f.write(json.dumps(it, ensure_ascii=False) + "\n")
os.replace(historyTempPath, historyPath)
os.replace(sessionTempPath, sessionPath)
result = f"[INFO] sessionSummary：压缩完毕，旧 session 已归档至：{historyPath}。"
print(result)