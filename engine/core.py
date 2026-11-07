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
import re
import subprocess
import sys
import time

chat_completions_url = "https://api.deepseek.com/chat/completions"

ROOT = Path(__file__).resolve().parents[1]
REALM_ROOT = ROOT / "ConsciousnessRealm"
CFG_PATH = ROOT / "state" / "Philosophical.json"
REGISTRY = ROOT / "function" / "function.json"
SESSION_PATH = ROOT / "state" / "session" / "session.jsonl"
SKILL_PATH = REALM_ROOT / "skill" / "skill.json"

# system 级段落，按顺序拼接
SYSTEM_BLOCKS = ["PERSONA", "AGENT", "CHARACTER", "MEMORY", "LOREBOOK", "USER"]

# 可调参数全部读 harness（state/Philosophical.json），下面的常量在配置加载后定义。

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

LOREBOOK_MAX_ITEMS = _num("retrieval.lorebook.maxItems", 12)
LOREBOOK_BUDGET_CHARS = _num("retrieval.lorebook.budgetChars", 2000)

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


def get_last_turns(session, n=QUERY_TURNS_LOOP):
    """按 user 消息切轮，返回最后 n 轮。"""
    start = None
    count = 0
    for i in range(len(session) - 1, -1, -1):
        if session[i].get("role") == "user":
            count += 1
            if count == n:
                start = i
                break
    if start is None:
        start = next((i for i, m in enumerate(session) if m.get("role") == "user"), None)
        if start is None:
            return []
    return session[start:]


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

def id_sort_key(item):
    raw = str(item.get("id", ""))
    m = re.match(r"^(.*?)-(\d+)$", raw)
    if m:
        return (m.group(1), int(m.group(2)))
    return (raw, -1)


_ROLE_TAG = re.compile(r"<(?:user|assistant|system)><([^>]*)>\s*")
_BARE_TAG = re.compile(r"<[a-z_]+>\s*")


def render_transcript(text):
    """把切片的原始转写改写成干净文本。

    切片里带着 `{...}` 外壳和 `<user><名字>` / `<assistant><名字>` 标记。
    这些尖括号标记与本项目的分区标签同形，混进上下文会让模型误以为
    是 session 里的对话，所以在注入前全部抹掉，改成人名加冒号。
    """
    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    text = _ROLE_TAG.sub(lambda m: (m.group(1) + "：") if m.group(1) else "", text)
    text = _BARE_TAG.sub("", text)
    return text.strip()


def memory_recall(search_text, record=True):
    """返回 <memory_momentary> 块，没有命中返回空串。

    切片与记忆块**分别检索**：混在一起全局排序时，分数低的块会被切片挤掉，
    而块恰恰是最精确的答案（短、密度高）。
    """
    if memory_search is None or not search_text:
        return ""

    try:
        slices = memory_search(search_text, top_k=MEMORY_TOP_K, kind="slice",
                               min_score=SLICE_FLOOR, record=False)
        blocks = memory_search(search_text, top_k=BLOCK_TOP_K, kind="block",
                               min_score=None, record=False)
    except Exception as e:                   # noqa: BLE001
        ERROR.append(f"[ERROR] 记忆检索失败：{type(e).__name__}: {e}")
        return ""

    blocks = [b for b in blocks if b["score"] >= BLOCK_FLOOR]
    if slices:
        # 主题集中时多收几条；主题稀薄时只留最相关的
        best = max(h["score"] for h in slices)
        floor = max(SLICE_FLOOR, best - SLICE_MARGIN)
        slices = [h for h in slices if h["score"] >= floor]

    kept = blocks + slices
    if not kept:
        return ""
    kept.sort(key=lambda h: (h["kind"] != "block", -h["score"]))
    kept = kept[:MEMORY_TOP_K + BLOCK_TOP_K]

    if record and memory_lib is not None:
        try:
            memory_lib().record([{"id": h["id"], "chars": h["chars"]} for h in kept])
        except Exception as e:               # noqa: BLE001
            ERROR.append(f"[WARN] 命中计数失败：{type(e).__name__}: {e}")

    blocks_kept = [h for h in kept if h["kind"] == "block"]
    slices_kept = [h for h in kept if h["kind"] == "slice"]

    parts = []
    if blocks_kept:
        parts.append("## 我记得\n" + "\n".join(f"- {h['text'].strip()}" for h in blocks_kept))
    if slices_kept:
        parts.append("## 当时的原话\n" + "\n\n".join(render_transcript(h["text"]) for h in slices_kept))
    body = "\n\n".join(parts)
    return f"<memory_momentary>\n{body}\n</memory_momentary>"


def lorebook_recall(search_text):
    """返回 <lorebook_momentary> 块，没有命中返回空串。"""
    if lorebook_lookup is None or not search_text:
        return ""
    try:
        hits = lorebook_lookup(search_text, max_items=LOREBOOK_MAX_ITEMS,
                               budget_chars=LOREBOOK_BUDGET_CHARS)
    except Exception as e:                   # noqa: BLE001
        ERROR.append(f"[ERROR] 世界书检索失败：{type(e).__name__}: {e}")
        return ""
    if not hits:
        return ""
    # 按命中标签数排序（lookup 已排序，这里保持稳定），同一 tags 归到一组
    groups = {}
    for h in hits:
        key = (h.get("tags") or ["其他"])[0]
        groups.setdefault(key, []).append(h)
    sections = []
    for key, items in groups.items():
        lines = [f"## {h['title']}\n\n{h['text'].strip()}" for h in items]
        sections.append(f"# {key}\n\n" + "\n\n".join(lines))
    return "<lorebook_momentary>\n" + "\n\n".join(sections) + "\n</lorebook_momentary>"


def skill_recall(skills_data, search_text, limit=2):
    """非常驻技能：关键词命中即召回，命中越多越靠前；只按条数截断，不切字数。"""
    if not skills_data or not search_text:
        return ""
    low = search_text.lower()
    hits = []
    for skill in skills_data:
        if skill.get("constant"):
            continue
        keys = [k for k in (skill.get("keywords") or []) if isinstance(k, str) and len(k) >= 2]
        matched = [k for k in keys if k.lower() in low]
        if not matched:
            continue
        hits.append((len(matched), max(len(k) for k in matched),
                     int(skill.get("timestamp") or 0), skill))

    if not hits:
        return ""
    hits.sort(key=lambda item: (-item[0], -item[1], -item[2]))
    body = "\n\n".join(
        f"## {skill.get('title', '')}\n\n{increase_heading_level(str(skill.get('content') or ''))}"
        for _, _, _, skill in hits[:limit]
    )
    return f"<skill_knowledge>\n{body}\n</skill_knowledge>"


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
            lines.append("该找个地方睡一觉了。")
        elif LAST_PROMPT_TOKENS >= COMPRESSION_THRESHOLD * 0.9:
            lines.append("脑子里堆的东西太多了，睡一觉会清爽些。")
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


VIRTUAL_TOOLS = {"active", "fight"}    # 不需要脚本、由代码直接处理的工具


def load_registry():
    """读 function.json，返回 {name: entry}。"""
    try:
        entries = json.loads(Path(REGISTRY).read_text(encoding="utf-8")).get("entries", [])
    except (OSError, json.JSONDecodeError) as e:
        ERROR.append(f"[ERROR] 工具注册表读取失败：{e}")
        return {}
    registry = {}
    for e in entries:
        name = e.get("name")
        path = e.get("path")
        if not name:
            ERROR.append(f"[WARN] 工具条目缺少 name，已跳过：{e}")
            continue
        if not path and name not in VIRTUAL_TOOLS:
            ERROR.append(f"[WARN] 工具条目缺少 path，已跳过：{name}")
            continue
        registry[name] = e
    return registry


def build_tools(registry):
    """注册表 → chat/completions 的 tools 数组（去掉 path 这类本地字段）。"""
    tools = []
    for name, e in registry.items():
        fn = {"name": name}
        if e.get("description"):
            fn["description"] = e["description"]
        if e.get("parameters"):
            fn["parameters"] = e["parameters"]
        tools.append({"type": "function", "function": fn})
    return tools


def virtual_fight(raw_args):
    """回合制战斗。action=start 开打，action=act 走一回合。"""
    try:
        from engine import combat
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"战斗模块不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)

    action = str(args.get("action") or "").lower()
    if action == "start":
        from engine import commissions as commissions_mod
        cid = str(args.get("commission") or "").strip()
        enemy_id = str(args.get("enemy") or "").strip()
        if cid and not enemy_id:
            item = next((c for c in commissions_mod.active_commissions()
                         if c.get("id") == cid), None)
            if not item:
                # 也可能是委托模板 id：现接现打，顺手把单子记下来
                item = commissions_mod.accept(cid)
                if item:
                    cid = item["id"]
            if not item:
                return json.dumps({"ok": False, "reason": f"没有这张委托：{cid}"},
                                  ensure_ascii=False)
            enemy_id = item.get("enemy") or ""
        enemies = combat.load_enemies()
        if enemy_id not in enemies:
            return json.dumps({"ok": False, "reason": f"没有这个敌人：{enemy_id}",
                               "known": list(enemies)}, ensure_ascii=False)
        battle = combat.new_battle(combat.load_character_pack(), enemies[enemy_id])
        if cid:
            battle["commission"] = cid
        combat.save(battle)
        return json.dumps({"ok": True, "commission": cid or None,
                           **combat.status(battle)}, ensure_ascii=False)

    battle = combat.load()
    if not battle or battle.get("over"):
        return json.dumps({"ok": False, "reason": "现在没有进行中的战斗"}, ensure_ascii=False)

    if action == "heal":
        log, ok = combat.heal(battle)
        combat.save(battle)
        return json.dumps({"ok": ok, "log": log, **combat.status(battle)}, ensure_ascii=False)

    if action == "rescue":
        battle = combat.rescue(battle)
        applied = combat.apply_result(battle)
        combat.clear()
        return json.dumps({"ok": True, "result": battle["result"], "log": battle["log"][-1:],
                           "applied": applied, **combat.status(battle)}, ensure_ascii=False)

    if combat.is_critical(battle):
        return json.dumps({"ok": False, "critical": True,
                           "reason": "她伤得太重，撑不住了——先处理伤口（heal）或者撤（rescue）",
                           **combat.status(battle)}, ensure_ascii=False)

    skill = str(args.get("skill") or "").strip() or None
    battle, log = combat.act(battle, skill)
    result = {"ok": True, "log": log, **combat.status(battle)}
    if battle["over"]:
        result["applied"] = combat.apply_result(battle)
        if battle["result"] == "win":
            try:
                from engine import commissions as commissions_mod
                cid = str(battle.get("commission") or "")
                item = next((c for c in commissions_mod.active_commissions()
                             if c.get("id") == cid), None) or commissions_mod.current()
                if item:
                    commissions_mod.finish(item["id"])
                    result["commission"] = {"id": item["id"], "title": item.get("title"),
                                            "done": True,
                                            "rewardText": item.get("rewardText") or ""}
            except Exception as e:           # noqa: BLE001
                ERROR.append(f"[WARN] 委托结算失败：{type(e).__name__}: {e}")
        combat.clear()
    else:
        combat.save(battle)
    return json.dumps(result, ensure_ascii=False)


def virtual_active(raw_args):
    """主代理声明一个持续动作：交给 body 判定并挂计时器。"""
    try:
        import engine.body as body
    except Exception as e:                   # noqa: BLE001
        return json.dumps({"ok": False, "reason": f"body 不可用：{type(e).__name__}"},
                          ensure_ascii=False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "reason": "参数不是合法 JSON"}, ensure_ascii=False)

    description = str(args.get("description") or "").strip()
    if not description:
        return json.dumps({"ok": False, "reason": "缺少 description"}, ensure_ascii=False)
    slot = "long" if str(args.get("slot") or "short").lower() == "long" else "short"
    force = bool(args.get("force"))

    receipt = body.commit(description, slot, force=force, appeal=args.get("appeal"))
    if not receipt.get("ok") and receipt.get("degraded"):
        ERROR.append(f"[WARN] body 判定失败：{receipt.get('reason')}")
    return json.dumps(receipt, ensure_ascii=False)


def execute_tool_call(call, registry):
    """执行一次 tool_call，返回回灌给模型的文本。

    工具约定：stdin 收 JSON 参数，stdout 打 JSON 结果，非 0 退出视为失败。
    失败信息也要回灌——让模型知道发生了什么，而不是静默重试。
    """
    fn = call.get("function") or {}
    name = fn.get("name") or ""
    raw_args = fn.get("arguments") or "{}"

    if name == "active":                     # virtual：交给 body 判定，不执行脚本
        return virtual_active(raw_args)
    if name == "fight":                      # virtual：回合制战斗
        return virtual_fight(raw_args)

    entry = registry.get(name)
    if not entry:
        return f"[工具不存在] {name}"

    script = (ROOT / entry["path"]).resolve()
    if not script.exists():
        return f"[工具脚本缺失] {script}"
    if ROOT not in script.parents:
        return f"[拒绝执行] 脚本不在项目内：{script}"

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=raw_args, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(ROOT), timeout=TOOL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] {name} 超过 {TOOL_TIMEOUT} 秒未返回"
    except OSError as e:
        return f"[启动失败] {name}: {e}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        text = err or out or f"exit {proc.returncode}"
        return f"[失败 exit={proc.returncode}] {text}"
    return (out or err or "(无输出)")[:MAX_TOOL_OUTPUT]


def tool_messages(tool_calls, registry):
    """把 tool_calls 逐个执行，组装成 role=tool 的消息列表。"""
    results = []
    for call in tool_calls:
        content = execute_tool_call(call, registry)
        results.append({
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "content": content[:MAX_TOOL_OUTPUT],
        })
    return results


SUMMARY_TOOL = "sessionSummary"
SUMMARY_PLACEHOLDER = "（摘要已写入 session 首条提要，此处不再重复）"


def strip_summary_args(calls):
    """压缩工具的 arguments 就是整份摘要，落盘前换成占位符，避免与提要重复。"""
    out = []
    for call in calls:
        call = dict(call)
        fn = dict(call.get("function") or {})
        if fn.get("name") == SUMMARY_TOOL:
            fn["arguments"] = json.dumps({"content": SUMMARY_PLACEHOLDER}, ensure_ascii=False)
        call["function"] = fn
        out.append(call)
    return out


def should_loop(response):
    """这一轮要不要继续：以有 tool_calls 为准，finish_reason 兜底。"""
    choice = ((response or {}).get("choices") or [{}])[0]
    if (choice.get("message") or {}).get("tool_calls"):
        return True
    return choice.get("finish_reason") == "tool_calls"


def tool_turn_messages(response, registry):
    """把这一轮的工具调用执行掉，返回要写进 session 的消息（assistant + 每条 tool）。"""
    choice = ((response or {}).get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return [{"role": "assistant", "content": message.get("content") or ""}]
    results = tool_messages(calls, registry)          # 先按原文执行
    assistant = {"role": "assistant", "content": message.get("content") or "",
                 "tool_calls": strip_summary_args(calls)}   # 再按占位符落盘
    return [assistant] + results


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


def build_system(skills_data):
    """六个 system 段落 + 常驻技能，拼成一条消息。"""
    parts = []
    for filename in SYSTEM_BLOCKS:
        item_path = REALM_ROOT / f"{filename}.md"
        if not item_path.exists():
            ERROR.append(f"[ERROR] 系统文件不存在：{item_path}")
            continue
        content = increase_heading_level(item_path.read_text(encoding="utf-8")).strip()
        if content:
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
    blocks = []
    for name, block in (
        ("memory", memory_recall(search_text, record=record)),
        ("lorebook", lorebook_recall(search_text)),
        ("skill", skill_recall(skills_data, search_text)),
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
    MESSAGES = [system]
    MESSAGES.extend(session)                 # session 历史

    MESSAGES.extend(blocks)                  # 检索块原样复用，不重新算

    if user_message and user_message.get("content"):
        MESSAGES.append(user_message)        # 本轮 message，永远排在最后

    # 本地图片路径 → Files API 的 file_id（会话里存的是路径，发出去必须是 file 块）
    if clean_images is not None and any(isinstance(m.get("content"), list) for m in MESSAGES):
        api = FileAPI(API_KEY, verify_ssl=VERIFY_SSL) if (API_KEY and FileAPI) else None
        MESSAGES, image_issues = clean_images(MESSAGES, api)
        for issue in image_issues:
            ERROR.append(f"[WARN] 图片上传失败 {issue}")

    registry = load_registry()
    tools = build_tools(registry)

    assembled = dict(payload)
    assembled["messages"] = MESSAGES
    assembled["tools"] = tools
    if not tools:
        assembled["tool_choice"] = "none"
    return clean_payload(assembled)


VERIFY_SSL = bool(harness_cfg("network.verifySSL", False))   # 本机走 sidecar 会 MITM，默认关校验
LOOP_MAX = _num("loop.maxRounds", 8, int)   # 一轮里最多几次「模型→工具→再模型」


# ---------------- usage ----------------

USAGE_DIR = ROOT / "state" / "usage"
COMPRESSION_THRESHOLD = int(
    (harness.get("session") or {}).get("compressionThreshold") or 340000
)


def usage_numbers(usage):
    """从服务端返回的 usage 里取实数，不做任何估算。

    chat 接口：prompt_tokens / completion_tokens / total_tokens
              prompt_cache_hit_tokens / prompt_cache_miss_tokens
    兼容 responses 接口的 input_tokens / output_tokens / input_tokens_details。
    """
    usage = usage or {}
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
               or (usage.get("input_tokens_details") or {}).get("cached_tokens"))
    hit = int(hit or 0)
    prompt = int(prompt or 0)
    completion = int(completion or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "cache_hit": hit,
        "cache_miss": int(usage.get("prompt_cache_miss_tokens") or max(prompt - hit, 0)),
        "cache_rate": round(100 * hit / prompt, 2) if prompt else 0.0,
    }


LAST_PROMPT_TOKENS = 0     # 上一次请求的实际 prompt_tokens，压缩提示用


def record_usage(usage, *, turn_id="", round_no=0, speaker=""):
    """追加一条 usage 记录到 state/usage/YYYY-MM-DD.jsonl，返回这条记录。"""
    global LAST_PROMPT_TOKENS
    n = usage_numbers(usage)
    if n["prompt"]:
        LAST_PROMPT_TOKENS = n["prompt"]
    rec = {
        "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "turn": turn_id,
        "round": round_no,
        "model": payload.get("model", ""),
        "speaker": speaker,
        **n,
    }
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = USAGE_DIR / (__import__("time").strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        ERROR.append(f"[WARN] usage 落盘失败：{e}")
    return rec


# ---------------- 快照（调试用） ----------------

DEBUG_DUMP = bool(harness_cfg("debug.dump", False))
DEBUG_DUMP_TAIL = bool(harness_cfg("debug.dumpTail", False))
DUMP_DIR = ROOT / "state" / "tmp" / "payload"
BLOCK_NAMES = {"memory", "lorebook", "skill", "state", "plan"}


def dump_payload(assembled, turn_id, round_no):
    """发送前落一份快照，用来核对上下文组装是否正确。

    debug.dump     → 完整 payload
    debug.dumpTail → 只展开易变的部分（检索块 + 本轮消息），system 与 session 只留摘要
    返回快照路径；两个开关都关时返回 None。
    """
    if not (DEBUG_DUMP or DEBUG_DUMP_TAIL):
        return None
    messages = assembled.get("messages") or []

    if DEBUG_DUMP and not DEBUG_DUMP_TAIL:
        data = dict(assembled)
    else:
        last = len(messages) - 1
        rows = []
        for i, m in enumerate(messages):
            content = m.get("content") or ""
            expand = i == last or (m.get("name") in BLOCK_NAMES)
            row = {"i": i, "role": m.get("role", "")}
            if m.get("name"):
                row["name"] = m["name"]
            if m.get("tool_calls"):
                row["tool_calls"] = [c.get("function", {}).get("name") for c in m["tool_calls"]]
            if m.get("tool_call_id"):
                row["tool_call_id"] = m["tool_call_id"]
            row["chars"] = len(content)
            if expand:
                row["content"] = content
            else:
                row["head"] = content[:80].replace("\n", " ")
            rows.append(row)
        data = {
            "turn": turn_id,
            "round": round_no,
            "model": assembled.get("model"),
            "tools": [t.get("function", {}).get("name") for t in assembled.get("tools") or []],
            "total_messages": len(messages),
            "total_chars": sum(len(m.get("content") or "") for m in messages),
            "messages": rows,
        }

    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = DUMP_DIR / f"{turn_id}-r{round_no}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def send(body, timeout=(15, 600)):
    """发一轮请求，返回解析后的 JSON；失败记 error 并返回 None。"""
    import requests
    import urllib3
    urllib3.disable_warnings()
    try:
        r = requests.post(
            chat_completions_url,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=body, timeout=timeout, verify=VERIFY_SSL,
            proxies={"http": None, "https": None},
        )
    except Exception as e:                       # noqa: BLE001
        ERROR.append(f"[ERROR] 请求失败：{type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        ERROR.append(f"[ERROR] HTTP {r.status_code}: {r.text[:300]}")
        return None
    return r.json()


def main(argv=None):
    """一轮对话：检索一次 → 写 user 进 session → 循环组装/发送 → 有工具就执行并写回。"""
    argv = sys.argv[1:] if argv is None else argv
    message = next((a for a in argv if not a.startswith("-")), None)
    record = "--no-record" not in argv      # 默认累计命中次数；调试时用 --no-record
    registry = load_registry()

    # 检索一轮只做一次：工具循环的后续轮次复用同一份块，
    # 否则每轮都会重新检索，命中次数会被重复累加。
    session = loadSession()
    pending = None
    if message:
        pending = {"role": "user", "name": payload.get("user_id", ""), "content": message}
        query = search_text_make(session, pending)
    else:
        query = search_text_make(session, None)
    blocks = build_blocks(query, load_skills(), record=record)

    if pending:
        append_session([pending])
        print(f"user: {message}")

    import time as _time
    turn_id = _time.strftime("%Y%m%d-%H%M%S")
    turn_records = []

    for round_no in range(1, LOOP_MAX + 1):
        assembled = context_assemble(record=False, blocks=blocks)
        if round_no == LOOP_MAX:            # 最后一轮摘掉工具，逼它把话说出来
            assembled["tools"] = []
            assembled["tool_choice"] = "none"
        if ERROR:
            for e in ERROR:
                print("  " + e, file=sys.stderr)
            ERROR.clear()

        print(f"\n--- 第 {round_no} 轮：{len(assembled['messages'])} 条消息，"
              f"{sum(len(m.get('content', '')) for m in assembled['messages'])} 字 ---")
        snap = dump_payload(assembled, turn_id, round_no)
        if snap:
            print(f"  快照 → {snap.relative_to(ROOT)}")

        response = send(assembled)
        if response is None:
            for e in ERROR:
                print("  " + e, file=sys.stderr)
            return 1

        rec = record_usage(response.get("usage"), turn_id=turn_id, round_no=round_no,
                           speaker=payload.get("user_id", ""))
        turn_records.append(rec)
        print(f"  usage: prompt={rec['prompt']} completion={rec['completion']} "
              f"cache_hit={rec['cache_hit']} ({rec['cache_rate']}%)")

        choice = (response.get("choices") or [{}])[0]
        reply = (choice.get("message") or {}).get("content") or ""

        if should_loop(response):
            calls = (choice.get("message") or {}).get("tool_calls") or []
            print(f"finish_reason={choice.get('finish_reason')}  工具调用："
                  f"{[c['function']['name'] for c in calls]}")
            wrote = tool_turn_messages(response, registry)
            append_session(wrote)
            for m in wrote[1:]:
                head = str(m.get("content", ""))[:100].replace("\n", " ")
                print(f"  ← {m.get('tool_call_id', '')[:14]} {head}")
            print(f"  已写回 session {len(wrote)} 条，重新组装…")
            continue

        append_session([{"role": "assistant", "content": reply}])
        print(f"assistant: {reply}")

        prompt_sum = sum(r["prompt"] for r in turn_records)
        completion_sum = sum(r["completion"] for r in turn_records)
        cache_sum = sum(r["cache_hit"] for r in turn_records)
        context = turn_records[-1]["prompt"] if turn_records else 0
        ratio = 100 * context / COMPRESSION_THRESHOLD if COMPRESSION_THRESHOLD else 0
        print(f"\n本轮：{len(turn_records)} 次请求 | prompt 合计 {prompt_sum} "
              f"(缓存命中 {cache_sum}) | completion 合计 {completion_sum}")
        print(f"上下文：{context} / 压缩阈值 {COMPRESSION_THRESHOLD}（{ratio:.1f}%）")
        if context >= COMPRESSION_THRESHOLD:
            print(f"[WARN] 上下文已达压缩阈值，下一轮该压缩会话了", file=sys.stderr)
        return 0

    print(f"[WARN] 达到工具循环上限 {LOOP_MAX} 轮，本轮中止", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
