"""上下文组装：把 ConsciousnessRealm 与检索结果拼成 chat/completions 的 messages。

顺序（权威定义见 `ConsciousnessRealm/AGENT.md`）：

    persona → agent → character → memory_persistent → lorebook_persistent → user
    → skill_proficient ‖ session ‖ memory_momentary → lorebook_momentary
    → skill_knowledge → state → message → plan

- 前六段（含 SKILL 常驻技能）拼成**一条 system 消息**，以 `# 段落名` 分隔。
- session 之后的部分用同名闭合标签包成独立消息，模型靠标签区分"资料"与"对方的话"。
- 顺序：session → memory_momentary → lorebook_momentary → skill_knowledge → state → plan → message。
- 最后一条 user 消息不加标签，且永远排在最后：它是唯一要回应的内容。
- **state / plan 暂不实现**，`state_loading()` / `plan_loading()` 固定返回空串。
"""

from pathlib import Path
import json
import os
import paths as _paths
import tools as _tools

try:
    from trace import log
except ImportError:
    def log(*a, **k):
        pass
import re
import subprocess
import sys
import time

chat_completions_url = "https://api.deepseek.com/chat/completions"

ROOT = Path(__file__).resolve().parents[1]
REALM_ROOT = ROOT / "ConsciousnessRealm"
CFG_PATH = _paths.data("Philosophical.json")
REGISTRY = ROOT / "function" / "function.json"
SESSION_PATH = _paths.data("session", "session.jsonl")
SKILL_PATH = REALM_ROOT / "skill" / "skill.json"

# system 级段落，按顺序拼接
SYSTEM_BLOCKS = ["PERSONA", "AGENT", "CHARACTER", "MEMORY", "LOREBOOK", "USER"]

# 可调参数全部读 harness（state/Philosophical.json），下面的常量在配置加载后定义。
# state / plan 两段均已实现：state 来自 timers.render_state，plan 来自 agenda.json。

# 检索层按路径导入；失败时降级，不影响其它段落
sys.path.insert(0, str(ROOT))
try:
    from model.memory_store import search as memory_search, lib as memory_lib
except Exception as e:                       # noqa: BLE001
    memory_search = memory_lib = None
    _memory_error = f"{type(e).__name__}: {e}"
else:
    _memory_error = ""

try:
    from ConsciousnessRealm.lorebook.lorebook_store import lookup as lorebook_lookup
except Exception as e:                       # noqa: BLE001
    lorebook_lookup = None
    _lorebook_error = f"{type(e).__name__}: {e}"
else:
    _lorebook_error = ""

try:
    from engine import timers
    from engine import events as event_hooks
    from engine import reminders as reminder_hooks
except Exception as e:                       # noqa: BLE001
    timers = event_hooks = None
    _timers_error = f"{type(e).__name__}: {e}"
else:
    _timers_error = ""

try:
    from engine.file_api import FileAPI, clean_messages as clean_images
except Exception as e:                       # noqa: BLE001
    FileAPI = None
    clean_images = None
    _file_error = f"{type(e).__name__}: {e}"
else:
    _file_error = ""

ERROR: list[str] = []
if _memory_error:
    ERROR.append(f"[ERROR] 记忆库不可用：{_memory_error}")
if _lorebook_error:
    ERROR.append(f"[ERROR] 世界书不可用：{_lorebook_error}")


# ---------------- 基础工具 ----------------

def increase_heading_level(text):
    """Markdown 标题整体降一级，让 `# 段落名` 成为唯一的一级标题。"""
    lines = text.split("\n")
    in_code = False
    fence = None
    out = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            ch = stripped[0]
            if not in_code:
                in_code, fence = True, ch
            elif ch == fence:
                in_code, fence = False, None
            out.append(line)
            continue
        if not in_code:
            line = re.sub(r"^(#{1,5})(?!#)", r"#\1", line)
        out.append(line)
    return "\n".join(out)


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
payload = {k.strip(): v for k, v in Philosophical.get("payload", {}).items()}
harness = Philosophical.get("harness", {})


def harness_cfg(path, default):
    """按 "a.b.c" 读 harness 下的值；缺失或类型不对时用默认值。"""
    node = harness
    for key in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def _num(path, default, cast=int):
    value = harness_cfg(path, default)
    try:
        return cast(value)
    except (TypeError, ValueError):
        ERROR.append(f"[WARN] harness.{path} 不是合法数值（{value!r}），用默认 {default}")
        return cast(default)


# ---------------- 可调参数 ----------------
# 查询：短问（"博士呢"）靠上下文才能过阈值。
# 实测 0.403（孤立）→ 0.592（带 1 轮）→ 0.686（带 2 轮）。
QUERY_TURNS_WITH_MESSAGE = _num("retrieval.queryTurns.withMessage", 2)
QUERY_TURNS_LOOP = _num("retrieval.queryTurns.loopRound", 3)
QUERY_MAX_CHARS = _num("retrieval.queryTurns.maxChars", 500)
QUERY_WINDOW_MINUTES = _num("retrieval.queryTurns.windowMinutes", 60)

MEMORY_TOP_K = _num("retrieval.memory.topK", 6)
BLOCK_TOP_K = _num("retrieval.memory.blockTopK", 3)
SLICE_FLOOR = _num("retrieval.memory.sliceFloor", 0.55, float)
SLICE_MARGIN = _num("retrieval.memory.sliceMargin", 0.08, float)
BLOCK_FLOOR = _num("retrieval.memory.blockFloor", 0.40, float)


# api key 不是请求体参数：单独拿出，组装出的 payload 里不含它
API_KEY = payload.pop("api", "")


# ---------------- 会话 ----------------

def normalize_message(rec):
    """接受 chat 风格或 responses 风格，统一成 chat/completions 的消息。"""
    if not isinstance(rec, dict):
        return None
    role = rec.get("role")
    if role not in {"user", "assistant", "tool", "system"}:
        return None
    content = rec.get("content")
    if isinstance(content, list):            # responses 风格 [{type:text,text:...}]
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        content = ""
    calls = rec.get("tool_calls") or []
    if not content.strip() and not calls:    # 空消息（且不是工具调用）丢弃
        return None
    msg = {"role": role, "content": content}
    if rec.get("name"):
        msg["name"] = rec["name"]
    if rec.get("reasoning_content"):
        msg["reasoning_content"] = rec["reasoning_content"]
    if calls:
        msg["tool_calls"] = calls            # assistant 的工具调用必须留着，否则后续 tool 消息无主
    if role == "tool" and rec.get("tool_call_id"):
        msg["tool_call_id"] = rec["tool_call_id"]
    return msg


def loadSession(path=SESSION_PATH):
    """读 session.jsonl，返回规范化的消息列表。"""
    session = []
    if not Path(path).exists():
        ERROR.append(f"[ERROR] session 文件不存在：{path}")
        return session
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as e:
                ERROR.append(f"[WARN] session 第 {lineno} 行解析失败：{e}")
                continue
            msg = normalize_message(item)
            if msg is None:
                ERROR.append(f"[WARN] session 第 {lineno} 行不是有效消息，已跳过")
                continue
            session.append(msg)
    return session




# 历史里的标签块不该进入检索查询
_TAG_RE = re.compile(r"<[a-z_]+>.*?</[a-z_]+>", re.S)


def content_text(content):
    """从 content（字符串或内容块数组）里取出可检索的文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def search_text_make(session, user_message=None):
    """拼检索查询：从最近的一条往回抓，攒到上限为止（约 500 字）。

    不按时间窗口截断——睡一觉醒来，睡前那几轮仍要在原料里。
    """
    texts = []
    for message in reversed(session or []):
        role = message.get("role")
        if role in ("user", "assistant"):
            text = content_text(message.get("content")).strip()
            if text:
                texts.append(text)
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                if fn.get("name") != "active":
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    continue
                for key in ("description", "scene"):
                    piece = str(args.get(key) or "").strip()
                    if piece:
                        texts.append(piece)

    if user_message:
        texts.append(content_text(user_message.get("content")))

    chunks, total = [], 0
    for text in texts:                     # 已是「新的在前」
        text = _TAG_RE.sub("", text).strip()
        if not text:
            continue
        if total + len(text) + len(chunks) > QUERY_MAX_CHARS:
            break                          # 装不下（含换行）就不装，也不再往上溯
        chunks.append(text)
        total += len(text)
        if total >= QUERY_MAX_CHARS:
            break
    return "\n".join(reversed(chunks)).strip()


# ---------------- 检索段落 ----------------













COMPRESSION_NOTICE = (
    "[INFO] 记忆挤得发沉，前面的话堆得太多了。\n"
    "把手头这一段收个尾；合适的时候用 sessionSummary 工具，把旧对话收拢成一段提要。\n"
    "这一轮不要开新长话题。内部指令，不向对方复述。"
)


def state_loading():
    """当前状态：常驻的时间与位置，加上刚发生的事。

    计时器在这里结算（纯代码）：到点的条目落数值/位置，文本并入缓存池，
    再一次性取走合并——主代理读到的是「刚刚发生了什么」。
    """
    if timers is None:
        return ""

    try:
        hours, applied = timers.advance()   # 先按真实时间推进生理
        if applied.get("_maintain"):
            ERROR.append(f"[INFO] 睡够 {applied['_maintain']} 小时，维护已排入 queue")
        timers.settle()
        ch_now = timers.load_character()
        if reminder_hooks is not None:
            try:
                reminder_hooks.check()       # 生理提醒：满足就必触发
            except Exception as e:           # noqa: BLE001
                ERROR.append(f"[WARN] 生理提醒失败：{type(e).__name__}: {e}")
        if event_hooks is not None:
            try:
                event_hooks.check()          # 地点事件 + 全局事件，先过条件再掷骰
            except Exception as e:           # noqa: BLE001
                ERROR.append(f"[WARN] 事件判定失败：{type(e).__name__}: {e}")
        pending = timers.take_pending()
        ch = timers.load_character()
    except Exception as e:                   # noqa: BLE001
        ERROR.append(f"[WARN] 状态结算失败：{type(e).__name__}: {e}")
        return ""

    lines = timers.render_state(ch, pending)

    # 两条睡觉的理由：身体撑不住，或者脑子装不下了
    state = ch.get("state") or {}
    metab = ch.get("metabolism") or {}
    if state.get("active") != "sleep":
        if float(metab.get("sleepiness", 0)) >= 92:
            lines.append("角色的困意已经到极限，继续下去会昏迷，该让她找地方睡。")
        elif LAST_PROMPT_TOKENS >= COMPRESSION_THRESHOLD * 0.9:
            lines.append("上下文占用接近上限，建议让她睡一觉触发记忆整理。")
    if LAST_PROMPT_TOKENS and LAST_PROMPT_TOKENS >= COMPRESSION_THRESHOLD:
        lines.append(COMPRESSION_NOTICE)
    return "<state>\n" + "\n".join(lines) + "\n</state>"


# plan 渲染参数
PLAN_PATH = REALM_ROOT / "plan" / "agenda.json"
PLAN_STEP_WINDOW = _num("plan.stepWindow", 1, int)
PLAN_ACTIVE_LIMIT = _num("plan.activeLimit", 3, int)
PLAN_PAUSED_LIMIT = _num("plan.pausedLimit", 2, int)


def _parse_dt(text):
    from datetime import datetime
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(text), fmt)
        except (TypeError, ValueError):
            continue
    return None


def _age_text(text):
    """时间戳 → "3 天" 这样的相对时长；每次组装现算，所以不会过期。"""
    from datetime import datetime
    dt = _parse_dt(text)
    if not dt:
        return ""
    days = (datetime.now() - dt).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    return f"{days} 天"


def _render_goal(goal):
    """一个目标渲染成一段 Markdown，步骤只给当前步前后各 N 步。"""
    gid = goal.get("id", "?")
    objective = goal.get("objective", "").strip()
    steps = [s for s in (goal.get("steps") or []) if isinstance(s, dict)]

    lines = []
    head = f"- [{gid}] {objective}"
    age = _age_text(goal.get("createdAt"))
    if steps:
        cur = next((i for i, st in enumerate(steps) if st.get("status") == "doing"), None)
        if cur is None:
            cur = next((i for i, st in enumerate(steps) if st.get("status") == "pending"), None)
        if cur is None:
            cur = len(steps) - 1
        head += f" · 第 {cur + 1}/{len(steps)} 步"
    if age:
        head += f"（立了 {age}）"
    lines.append(head)

    if not steps:
        return "\n".join(lines)

    lo = max(0, cur - PLAN_STEP_WINDOW)
    hi = min(len(steps), cur + PLAN_STEP_WINDOW + 1)
    for i in range(lo, hi):
        st = steps[i]
        title = st.get("title", "").strip()
        evidence = st.get("evidence", "").strip()
        if i == cur:
            marker = "当前："
        elif i < cur:
            marker = "上一步："
        else:
            marker = "下一步："
        line = f"  - {marker}{title}"
        if evidence:
            line += f" —— {evidence}"
        lines.append(line)
        fallback = str(st.get("fallback") or "").strip()
        if fallback:
            lines.append(f"      · 若不成：{fallback}")
    if lo > 0:
        lines.insert(1, f"  - （前 {lo} 步已完成）")
    if hi < len(steps):
        lines.append(f"  - （后 {len(steps) - hi} 步未展示）")
    return "\n".join(lines)


def plan_loading():
    """读取 agenda.json，渲染当前视角的计划块；没有任何内容时返回空串。"""
    path = Path(PLAN_PATH)
    if not path.exists():
        return ""
    try:
        goals = json.loads(path.read_text(encoding="utf-8")).get("goal", [])
    except (OSError, json.JSONDecodeError) as e:
        ERROR.append(f"[ERROR] agenda.json 读取失败：{e}")
        return ""
    if not isinstance(goals, list):
        return ""

    by_status = {"active": [], "paused": []}
    for g in goals:
        if not isinstance(g, dict):
            continue
        status = g.get("status") or "active"
        if status in by_status:
            by_status[status].append(g)

    sections = []
    active = by_status["active"]
    if active:
        active.sort(key=lambda g: str(g.get("updatedAt") or g.get("createdAt") or ""), reverse=True)
        body = "\n".join(_render_goal(g) for g in active[:PLAN_ACTIVE_LIMIT])
        if len(active) > PLAN_ACTIVE_LIMIT:
            body += f"\n- （另有 {len(active) - PLAN_ACTIVE_LIMIT} 个进行中的计划未展示）"
        sections.append("## 进行中\n" + body)

    paused = by_status["paused"]
    if paused:
        paused.sort(key=lambda g: str(g.get("updatedAt") or g.get("createdAt") or ""), reverse=True)
        lines = []
        for g in paused[:PLAN_PAUSED_LIMIT]:
            age = _age_text(g.get("updatedAt") or g.get("createdAt"))
            tail = f"（搁置 {age}）" if age else ""
            lines.append(f"- [{g.get('id', '?')}] {g.get('objective', '').strip()}{tail}")
        if len(paused) > PLAN_PAUSED_LIMIT:
            lines.append(f"- （另有 {len(paused) - PLAN_PAUSED_LIMIT} 个搁置中的计划未展示）")
        sections.append("## 搁置\n" + "\n".join(lines))

    if not sections:
        return ""
    if not active and not paused:
        sections.insert(0, "目前没有进行中的计划。")
    return "<plan>\n" + "\n\n".join(sections) + "\n</plan>"


# ---------------- 组装 ----------------

# ---------------- 工具 ----------------

MAX_TOOL_OUTPUT = _num("loop.maxToolOutput", 8000, int)   # 单个工具结果最多回灌多少字
TOOL_TIMEOUT = _num("subprocess.timeout", 300, int)
_tools.TOOL_TIMEOUT = TOOL_TIMEOUT
_tools.MAX_TOOL_OUTPUT = MAX_TOOL_OUTPUT




TOOL_PATHS = {}          # {工具名: 脚本相对路径}，执行时用




















SUMMARY_TOOL = "sessionSummary"
SUMMARY_PLACEHOLDER = "（摘要已写入 session 首条提要，此处不再重复）"








def append_session(messages, path=SESSION_PATH):
    """追加写入 session.jsonl。每行一条消息，只追加不重写。"""
    if not messages:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        for m in messages:
            if "ts" not in m:
                m = {**m, "ts": stamp}
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    if event_hooks is not None:
        try:
            event_hooks.note_message()       # 静默时长从最后一条消息算
        except Exception:                    # noqa: BLE001
            pass
    return len(messages)


def appearance_message():
    """她此刻的样子：单独一条，放在 system 之后、session 之前。

    这里只给本地路径——发送前由 file_api.clean_messages 上传一次换成 file_id，
    同一张图后续请求直接复用，不用每轮背 base64。
    """
    base_dir = _paths.data("media", "self", "appearance")
    picture = base_dir / "current.png"          # 当前衣着
    if not picture.exists():
        picture = base_dir / "appearance.png"   # 还没换过装，就用底图
    if not picture.exists():
        # 模拟器没有自己的图时，只读借用真实那份，不复制
        real = _paths.ROOT / "state" / "media" / "self" / "appearance"
        picture = real / "current.png"
        if not picture.exists():
            picture = real / "appearance.png"
    if not picture.exists():
        return None
    return {"role": "user", "content": [
        {"type": "text", "text": "<appearance>她此刻的样子（参考，不必逐条描述）</appearance>"},
        {"type": "image_url", "image_url": {"url": str(picture)}},
    ]}


INTERRUPTED = "[ERROR] 这次工具执行被服务重启或超时中断，未完成；不要当作成功。"


def repair_messages(messages):
    """修复会话结构：给缺失回执的工具调用补一条中断回执，丢掉孤立回执。

    写坏的会话会让每次请求 400（tool_calls 后面没有 tool 消息），
    所以发送前和启动时都要过一遍。
    """
    fixed, changed = [], False
    tags = ("<memory_momentary>", "<lorebook_momentary>", "<skill_knowledge>",
            "<state>", "<plan>", "<user>", "<assistant>", "<impression>")
    index = 0
    while index < len(messages):
        msg = messages[index]
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            text = msg["content"]
            cut = min((text.find(t) for t in tags if t in text), default=-1)
            if cut >= 0:
                msg = dict(msg, content=text[:cut].rstrip())
                messages[index] = msg
                changed = True
        fixed.append(msg)
        index += 1
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue

        wanted = [c for c in msg["tool_calls"] if isinstance(c, dict)]
        ids = {c.get("id") for c in wanted}
        seen = set()
        while index < len(messages) and messages[index].get("role") == "tool":
            row = messages[index]
            cid = row.get("tool_call_id")
            if cid in ids and cid not in seen:
                seen.add(cid)
                fixed.append(row)
            else:
                changed = True          # 孤立或重复的回执，丢掉
            index += 1
        for call in wanted:
            cid = call.get("id")
            if cid not in seen:
                fixed.append({"role": "tool", "tool_call_id": cid,
                              "content": INTERRUPTED,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
                changed = True

    # 末尾还有孤立的 tool 消息：没有对应的调用，清掉
    known = {c.get("id") for m in fixed if m.get("role") == "assistant"
             for c in (m.get("tool_calls") or []) if isinstance(c, dict)}
    cleaned = [m for m in fixed
               if m.get("role") != "tool" or m.get("tool_call_id") in known]
    if len(cleaned) != len(fixed):
        changed = True
    return cleaned, changed


def sanitize_tool_pairs(messages):
    """发送前清洗：只摘掉没有回执的 tool_calls，不改文件。"""
    out, _ = repair_messages(messages)
    return out


def repair_session(path=None):
    """启动时修一次会话文件；改动了就写回。"""
    target = Path(path or SESSION_PATH)
    if not target.exists():
        return 0
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    fixed, changed = repair_messages(rows)
    if not changed:
        return 0
    tmp = target.with_name(target.name + ".repair.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in fixed:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(target)
    return len(fixed) - len(rows)


def build_system(skills_data):
    """六个 system 段落 + 常驻技能，拼成一条消息。"""
    parts = []
    for filename in SYSTEM_BLOCKS:
        item_path = REALM_ROOT / f"{filename}.md"
        if not item_path.exists():
            ERROR.append(f"[ERROR] 系统文件不存在：{item_path}")
            continue
        content = increase_heading_level(item_path.read_text(encoding="utf-8")).strip()
        if not content:
            continue
        if filename == "USER":
            parts.append(f"<impression>\n\n{content}\n\n</impression>")
        else:
            parts.append(f"# {filename}\n\n{content}")

    constant = [s for s in skills_data
                if s.get("constant") and s.get("title") and s.get("content")]
    if constant:
        constant.sort(key=lambda s: s.get("timestamp", 0), reverse=True)
        body = "\n\n".join(
            f"## {s['title']}\n\n{increase_heading_level(s['content'])}" for s in constant
        )
        parts.append(f"# SKILL\n\n{body}")

    return {"role": "system", "content": "\n\n".join(parts)}


def clean_payload(body):
    """归一化请求体：去掉互斥/非法组合，避免服务端 400。"""
    body.pop("api", None)
    if not body.get("stream"):
        body.pop("stream_options", None)          # 只有 stream=true 才允许带
    if not body.get("logprobs"):
        body.pop("top_logprobs", None)            # top_logprobs 需要 logprobs=true
    return body


def load_skills():
    try:
        return json.loads(Path(SKILL_PATH).read_text(encoding="utf-8")).get("skills", [])
    except (OSError, json.JSONDecodeError) as e:
        ERROR.append(f"[ERROR] skill.json 读取失败：{e}")
        return []


def build_blocks(search_text, skills_data, record=True):
    """检索类背景块。一轮对话里只算一次——工具循环的后续轮次复用同一份。"""
    import recall
    blocks = []
    for name, block in (
        ("memory", recall.memory_recall(search_text, record=record)),
        ("lorebook", recall.lorebook_recall(search_text)),
        ("skill", recall.skill_recall(skills_data, search_text)),
        ("state", state_loading()),
        ("plan", plan_loading()),
    ):
        if block:
            blocks.append({"role": "user", "name": name, "content": block})
    return blocks


def context_assemble(user_message=None, *, session_path=SESSION_PATH, record=True,
                     blocks=None):
    """返回完整 payload（含 messages）。

    user_message 可以是 {"role","content"} 或纯字符串；为空时取 session 最后一条 user。
    blocks 传入时复用已有的检索块（工具循环用），不再重新检索。
    """
    session = loadSession(session_path)

    if isinstance(user_message, str):
        user_message = {"role": "user", "name": payload.get("user_id", ""),
                        "content": user_message}
    if user_message is None:
        if session and session[-1].get("role") == "user":
            user_message = session.pop()      # 最后一条就是待回应的话
        elif session and session[-1].get("role") == "tool":
            # 不变量：只有上一轮有 tool_calls，本轮才是工具回复轮；
            # 工具回复轮必然没有 message，输入就是刚回灌的 tool 结果。
            user_message = None
        else:
            ERROR.append("[ERROR] 没有待回应的 user 消息")
            user_message = {"role": "user", "content": ""}

    skills_data = load_skills()
    system = build_system(skills_data)
    if blocks is None:
        blocks = build_blocks(search_text_make(session, user_message), skills_data,
                              record=record)

    # 顺序：session → memory_momentary → lorebook_momentary → skill_knowledge
    #       → state → plan → message
    # message 必须最后：它是唯一要回应的内容；plan 是背景，不能抢它的位置
    session = sanitize_tool_pairs(session)
    MESSAGES = [system]
    shot = appearance_message()
    if shot:
        MESSAGES.append(shot)                # 外观图：system 之后、session 之前
    MESSAGES.extend(session)                 # session 历史

    MESSAGES.extend(blocks)                  # 检索块原样复用，不重新算

    if not (user_message and user_message.get("content")):
        # 自主回合：没人说话。明确告诉她别把背景当对话对象
        MESSAGES.append({"role": "user", "content":
            "[INFO] 这一轮没有新的对话。继续她自己的事——按 plan 和 state 行动，"
            "不要把上面的记忆、设定、状态当成对方说的话去回应。"})

    if user_message and user_message.get("content"):
        wrapped = dict(user_message)
        text = content_text(wrapped.get("content"))
        wrapped["content"] = (f"<user>\n{text}\n</user>"
                              if text.strip() else wrapped.get("content"))
        MESSAGES.append(wrapped)             # 本轮真人发言，用 <user> 标出来

    # 本地图片路径 → Files API 的 file_id（会话里存的是路径，发出去必须是 file 块）
    if clean_images is not None and any(isinstance(m.get("content"), list) for m in MESSAGES):
        api = FileAPI(API_KEY, verify_ssl=VERIFY_SSL) if (API_KEY and FileAPI) else None
        MESSAGES, image_issues = clean_images(MESSAGES, api)
        for issue in image_issues:
            ERROR.append(f"[WARN] 图片上传失败 {issue}")

    registry = _tools.load_registry()
    tools = _tools.build_tools(registry)

    assembled = dict(payload)
    assembled["messages"] = MESSAGES
    assembled["tools"] = tools
    if not tools:
        assembled["tool_choice"] = "none"
    return clean_payload(assembled)


VERIFY_SSL = bool(harness_cfg("network.verifySSL", False))   # 本机走 sidecar 会 MITM，默认关校验
LOOP_MAX = _num("loop.maxRounds", 8, int)   # 一轮里最多几次「模型→工具→再模型」


# ---------------- usage ----------------

USAGE_DIR = _paths.data("usage")
COMPRESSION_THRESHOLD = int(
    (harness.get("session") or {}).get("compressionThreshold") or 340000
)




LAST_PROMPT_TOKENS = 0     # 上一次请求的实际 prompt_tokens，压缩提示用




# ---------------- 快照（调试用） ----------------

DEBUG_DUMP = bool(harness_cfg("debug.dump", False))
DEBUG_DUMP_TAIL = bool(harness_cfg("debug.dumpTail", False))
DUMP_DIR = _paths.data("tmp", "payload")
BLOCK_NAMES = {"memory", "lorebook", "skill", "state", "plan"}
