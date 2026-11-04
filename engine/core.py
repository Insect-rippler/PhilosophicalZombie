from pathlib import Path
import re,json

chat_completions_url = "https://api.deepseek.com/chat/completions"

ROOT = Path(__file__).resolve().parents[1]

REALM_ROOT = ROOT / "ConsciousnessRealm"
CFG_PATH = ROOT / "state" / "Philosophical.json"
REGISTRY = ROOT / "function" / "function.json"
SESSION_PATH = ROOT / "state" / "session" / "session.jsonl"

SESSION = None

MEMORY_PERSISTENT = None
LOREBOOK_PERSISTENT = None
SKILL_PROFICIENT = None
STATE = None
LAST_MESSAGE = None
PLAN = None

ERROR = []
# ---------------- 自定义函数 ----------------
def increase_heading_level(text):
    lines = text.split('\n')
    in_code = False
    fence = None
    out = []
    for line in lines:
        stripped = line.lstrip()
        # 检测代码块围栏 ``` 或 ~~~
        if stripped.startswith('```') or stripped.startswith('~~~'):
            ch = stripped[0]
            if not in_code:
                in_code, fence = True, ch
            elif ch == fence:
                in_code, fence = False, None
            out.append(line)
            continue

        if not in_code:
            line = re.sub(r'^(#{1,5})(?!#)', r'#\1', line)
        out.append(line)
    return '\n'.join(out)
# ---------------- 配置 ----------------
def loadConfig():
    """读配置；读失败也返回空壳并记 error，不抛异常。"""
    try:
        with open(Path(CFG_PATH), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        ERROR.append(f"[ERROR] 配置文件不存在：{CFG_PATH}")
    except json.JSONDecodeError as e:
        ERROR.append(f"[ERROR] 配置文件不是合法 JSON：{e}")
    return {}

Philosophical = loadConfig()
payload = Philosophical.get("payload",{}) # payload[message] = []、payload[tools] = [] 等待组装
harness = Philosophical.get("harness",{})

def context_assemble():
    """system层面组装"""
    MESSAGES = []
    system_content = []
    ConsciousnessRealm = ROOT / "ConsciousnessRealm"
    skills_path = ConsciousnessRealm / "skill" / "skill.json"
    system_type = ["PERSONA", "AGENT", "CHARACTER", "MEMORY", "LOREBOOK"]
    for filename in system_type:
        item_path = ConsciousnessRealm.joinpath(f"{filename}.md")
        if item_path.exists():
            with open(item_path, "r", encoding="utf-8") as f:
                content = f.read()
                content = increase_heading_level(content)
                system_content.append(f"# {filename}\n\n{content}")
        else:
            ERROR.append(f"[ERROR] 系统文件不存在：{item_path}")
    skill_items = []
    with open(skills_path, "r", encoding="utf-8") as f:
        skills_data = json.load(f).get("skills", [])
    for skill in skills_data:
        if skill.get("constant", False):
            skill_name = skill.get("title")
            skill_content = skill.get("content")
            skill_content = increase_heading_level(skill_content)
            skill_timestamp = skill.get("timestamp")
            if skill_name and skill_content:
                skill_items.append({"skill": f"{skill_name}\n\n{skill_content}", "timestamp": skill_timestamp})
            else:
                ERROR.append(f"[ERROR] 技能数据不完整：{skill_name}")
    skill_items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    skill_proficient = "\n\n".join([item["skill"] for item in skill_items])
    system_content.append(f"# SKILL\n\n{skill_proficient}")
    system = "\n\n".join(item for item in system_content)
    MESSAGES.append({"content":system,"role":"system","name":"system"})
    
    return MESSAGES

# 文件先在“state\file\media\hash\hash.json”对比hash，通过file-api上传到deepseek，返回file_id，存储在“state\file\media\hash\hash.json”中，缩减后续复用耗时