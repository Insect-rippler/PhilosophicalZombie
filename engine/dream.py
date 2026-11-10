"""每晚一次的升格流程：切片 → 记忆块 → 长期记忆 → 人格。

三个阶段，门槛在 `state/Philosophical.json` 的 `harness.dream`：
  1. 切片 → 记忆块    按召回字数（recalled_chars），宽松，用来扩大召回面
  2. 记忆块 → MEMORY.md  按召回次数（recalls）+ 规则，严格
  3. MEMORY.md → PERSONA.md  按独立事件数 + 规则，最严

代码里不设「每次最多几条」——收敛靠规则，不靠计数上限。
每次运行都会把候选、通过、跳过的理由写进 `state/dream/YYYY-MM-DD.jsonl`，
否则「宁愿不写」就无从审计。

开发期手动跑：
    python engine/dream.py --stage 1 --dry-run
    python engine/dream.py --stage all
定时触发以后交给 state（睡着时跑），流程本身不依赖触发方式。
"""

import json
import paths as _paths
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

REALM = ROOT / "ConsciousnessRealm"
MEMORY_MD = REALM / "MEMORY.md"
PERSONA_MD = REALM / "PERSONA.md"
PLAN_GLOB = REALM / "plan" / "archive"
DREAM_DIR = _paths.data("dream")

import core
import modelio
import tools  # noqa: E402


# ---------------- 基础设施 ----------------

def dream_cfg(path, default):
    node = core.harness
    for key in ("dream",) + tuple(path.split(".")):
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


SLICE_MIN_CHARS = int(dream_cfg("block.minRecalledChars", 5000))
SLICE_MIN_RECALLS = int(dream_cfg("block.minRecalls", 5))
BLOCK_MIN_RECALLS = int(dream_cfg("memory.minRecalls", 30))
BLOCK_MIN_SOURCES = int(dream_cfg("memory.minSources", 2))
PERSONA_MIN_EVENTS = int(dream_cfg("persona.minEvents", 3))
MAX_MODEL_TOKENS = int(dream_cfg("maxTokens", 3000))


def call_tool(name, args, timeout=300):
    """起子进程调 function/ 下的工具，保证改了脚本立刻生效。"""
    import subprocess
    entry = tools.tool_entry(name)
    if not entry or not entry.get("path"):
        raise RuntimeError(f"工具没有可执行路径：{name}")
    script = (ROOT / entry["path"]).resolve()
    proc = subprocess.run([sys.executable, str(script)],
                          input=json.dumps(args, ensure_ascii=False),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(ROOT), timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:300])
    return json.loads(proc.stdout)


def load_json_file(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_md(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_json_answer(text):
    """模型偶尔会裹一层 ```json，剥掉再解析。"""
    if not text:
        return None
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def call_model(system_text, user_text, max_tokens=MAX_MODEL_TOKENS):
    """要一份 JSON 回答。失败返回 None，不抛异常。"""
    body = {
        "model": core.payload.get("model"),
        "messages": [{"role": "system", "content": system_text},
                     {"role": "user", "content": user_text}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    response = modelio.send(body)
    if not response:
        return None
    content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = parse_json_answer(content)
    if data is None:
        log({"stage": "parse", "ok": False, "raw": content[:300]})
    return data


def log(record):
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    path = DREAM_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def plan_material(days=3):
    """读最近的 plan 事件作为原料。归档目录还没有内容时返回空。"""
    if not PLAN_GLOB.exists():
        return []
    cutoff = time.time() - days * 86400
    rows = []
    for path in sorted(PLAN_GLOB.glob("*/events.jsonl")):
        if path.stat().st_mtime < cutoff:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("note"):
                rows.append(f"[计划] {ev.get('action', '')}：{ev.get('note')}")
    return rows


# ---------------- 阶段一：切片 → 记忆块 ----------------

STAGE1_SYSTEM = """你在整理一个角色的记忆。

输入是她这段时间反复回想的对话片段（每条前面有编号），以及她自己的计划变更记录。
任务：把这些材料蒸馏成「记忆块」——她自己的、压缩过的记忆。

规则：
- 每条 30~200 字，第一人称，写她的判断、感受或结论。
- 不要复述对话流程（谁说了什么、场面怎么变化），只写她从这件事里得到什么。
- 一个事件可以拆成多条，但每条只讲一件事，彼此不要重复。
- 只是场面描写、没有她自己的判断的材料，不要写。
- 只能使用材料里出现的信息，不得编造人名、事件、感受。
- 拿不准就不写。
- sources 必填：写清这条来自哪几段材料（编号）。

输出 JSON：{"blocks":[{"text":"...","tag":"涉及的人名或事物","sources":[材料编号]}]}
"""


def stage1(top=0, dry_run=False):
    lib = core.memory_lib()
    items = [it for it in lib.items if it.get("kind") == "slice"]
    if top:
        items.sort(key=lambda it: int(it.get("recalled_chars", 0)), reverse=True)
        candidates = items[:top]
    else:
        candidates = [it for it in items
                      if int(it.get("recalled_chars", 0)) >= SLICE_MIN_CHARS
                      and int(it.get("recalls", 0)) >= SLICE_MIN_RECALLS]

    materials = [f"[{i+1}] {it['text'].strip()}" for i, it in enumerate(candidates)]
    materials += plan_material()
    if not materials:
        log({"stage": 1, "ok": True, "candidates": 0, "blocks": 0})
        return {"stage": 1, "candidates": 0, "blocks": 0}

    answer = call_model(STAGE1_SYSTEM, "\n\n".join(materials))
    blocks = (answer or {}).get("blocks") or []
    written = 0
    if not dry_run and blocks:
        ids = [it["id"] for it in candidates]
        for b in blocks:
            text = (b.get("text") or "").strip()
            if not text:
                continue
            refs = []
            for n in b.get("sources") or []:
                try:
                    i = int(n) - 1
                except (ValueError, TypeError):
                    continue
                refs.append(ids[i] if 0 <= i < len(ids) else "plan")
            if not refs:
                # 没有来源的块无法追溯，也过不了阶段二；直接不入库
                log({"stage": 1, "skip": "无来源", "text": text[:50]})
                continue
            lib.add(text, kind="block", tag=(b.get("tag") or "").strip() or None, sources=refs)
            written += 1

    log({"stage": 1, "ok": True, "candidates": len(materials),
         "blocks": len(blocks), "written": written, "dry_run": dry_run,
         "titles": [b.get("tag") for b in blocks][:20]})
    result = {"stage": 1, "candidates": len(materials), "blocks": len(blocks), "written": written}
    if dry_run:
        result["preview"] = [{"text": b.get("text"), "tag": b.get("tag"), "sources": b.get("sources")} for b in blocks]
    return result


# ---------------- 文件改写（可修改原文，不是只追加） ----------------

def backup(files):
    """改文件之前先留一份，出问题能翻回去看。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = DREAM_DIR / "backup" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    for path in files:
        path = Path(path)
        if path.exists():
            (folder / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return folder


def find_block(text, title, level, near=None):
    """定位 `#* title` 这一段，返回 (start, end)。

    找不到标题时退回按条目找：PERSONA 的主体是 `- ` 开头的条目，
    只认标题的话 stage3 的 update / merge 永远改不到它们。
    """
    tag = "#" * level
    m = re.search(rf"^{tag} {re.escape(title)}[ \t]*$", text, re.M)
    if m:
        tail = text[m.end():]
        nxt = re.search(rf"^#{{1,{level}}} ", tail, re.M)
        return m.start(), m.end() + (nxt.start() if nxt else len(tail))

    needle = (title or "").strip()
    if not needle:
        return None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("- ") and needle in stripped:
            begin = offset
            stop = offset + len(line)
            # 把缩进的续行也算进这一条
            cursor = stop
            for extra in text[stop:].splitlines(keepends=True):
                chunk = extra.strip()
                if not chunk or chunk.startswith("- ") or chunk.startswith("#"):
                    break
                cursor += len(extra)
            return begin, cursor
        offset += len(line)
    return None


def _is_bullet(text, start):
    return text[start:start + 2] == "- "


def replace_block(text, title, level, body):
    span = find_block(text, title, level)
    if not span:
        return text, False
    s, e = span
    if _is_bullet(text, s):
        # 保持条目形态，不把 bullet 抬成标题
        line = "- " + " ".join(body.split())
        suffix = "" if text[e - 1:e] in ("\n", "") else "\n"
        return text[:s] + line + suffix + text[e:], True
    tag = "#" * level
    return text[:s] + f"{tag} {title}\n\n{body.strip()}\n\n" + text[e:], True


def delete_block(text, title, level):
    span = find_block(text, title, level)
    if not span:
        return text, False
    s, e = span
    return text[:s] + text[e:], True


def append_memory_entry(text, title, body):
    return text.rstrip() + f"\n\n## {title}\n\n{body.strip()}\n"


def insert_persona_entry(text, section, title, body):
    marker = f"## {section}"
    idx = text.find(marker)
    if idx < 0:                              # 该节还不存在：新建一节放在末尾
        return text.rstrip() + f"\n\n## {section}\n\n### {title}\n\n{body.strip()}\n", True
    nxt = text.find("\n## ", idx + len(marker))
    at = len(text) if nxt < 0 else nxt
    entry = f"\n### {title}\n\n{body.strip()}\n"
    return text[:at].rstrip() + "\n" + entry + text[at:], True


def apply_ops(text, ops, *, level, allowed_sections=None):
    """按 ops 改写文本。返回（新文本，已应用数，跳过记录）。

    op:
      add    -> 新增条目（PERSONA 需给 section）
      update -> 用新正文覆盖同名条目的原文
      merge  -> 把多条合并成一条（留第一条的标题，其余删除）
    """
    applied, skipped = 0, []
    for op in ops:
        kind = str(op.get("op") or "add").strip().lower()
        title = str(op.get("title") or "").strip()
        body = str(op.get("text") or "").strip()
        if not title or not body:
            skipped.append({"op": kind, "reason": "缺少 title 或 text"})
            continue

        if kind == "add":
            if allowed_sections is None:
                text = append_memory_entry(text, title, body)
                applied += 1
            else:
                section = str(op.get("section") or "").strip()
                if section not in allowed_sections:
                    skipped.append({"op": kind, "title": title,
                                    "reason": f"section 不合法：{section}"})
                    continue
                text, ok = insert_persona_entry(text, section, title, body)
                if ok:
                    applied += 1
                else:
                    skipped.append({"op": kind, "title": title, "reason": "找不到该 section"})

        elif kind == "update":
            text, ok = replace_block(text, title, level, body)
            if ok:
                applied += 1
            else:
                skipped.append({"op": kind, "title": title, "reason": "找不到同名条目"})

        elif kind == "merge":
            titles = [str(t).strip() for t in (op.get("titles") or []) if str(t).strip()]
            if len(titles) < 2:
                skipped.append({"op": kind, "title": title,
                                "reason": "merge 需要至少两个 titles"})
                continue
            merged, ok = replace_block(text, titles[0], level, body)
            if not ok:
                skipped.append({"op": kind, "title": title,
                                "reason": f"找不到条目：{titles[0]}"})
                continue
            text = merged
            for t in titles[1:]:
                text, _ = delete_block(text, t, level)
            if title != titles[0]:           # 合并后的标题要跟着改
                tag = "#" * level
                text = text.replace(f"{tag} {titles[0]}\n", f"{tag} {title}\n", 1)
            applied += 1

        else:
            skipped.append({"op": kind, "reason": "未知操作"})
    return text, applied, skipped


# ---------------- 阶段二：记忆块 → MEMORY.md ----------------

STAGE2_SYSTEM = """MEMORY.md 是角色的长期记忆，写进去的东西会一直带着。

输入是她反复想起的记忆块，以及现有的 MEMORY.md。

任务：决定要不要动 MEMORY.md，以及怎么动。

规则（任何一条不满足就跳过）：
1. 必须能说清「这条会约束她以后的行为或看法」；说不清就不写。
2. 只写经历与结论，不写细节流水。
3. 拿不准就不写，宁可这次不动。

允许三种操作：
- add：这件事是新的，加一条。
- update：现有条目不够准、太窄或写偏了，用新正文覆盖它。
- merge：几条说的是同一件事，合成一条。

重要：不要只往里加。如果新证据说明旧条目写错了或写窄了，
应当用 update 修正原文；否则记忆会朝一个方向越堆越偏。

输出 JSON：
{"ops":[
  {"op":"add","title":"简短标题","text":"150字以内的第一人称叙述","reason":"为什么值得长期保留"},
  {"op":"update","title":"现有条目标题","text":"改写后的完整正文","reason":"为什么要改"},
  {"op":"merge","titles":["标题A","标题B"],"title":"合并后的标题","text":"合并后的正文","reason":"为什么合并"}
 ],
 "skip":[{"text":"被跳过的块（截断即可）","reason":"为什么跳过"}]}
"""


def stage2(dry_run=False):
    lib = core.memory_lib()
    blocks = [it for it in lib.items if it.get("kind") == "block"
              and int(it.get("recalls", 0)) >= BLOCK_MIN_RECALLS
              and len(it.get("sources") or []) >= BLOCK_MIN_SOURCES]
    if not blocks:
        log({"stage": 2, "ok": True, "candidates": 0, "applied": 0})
        return {"stage": 2, "candidates": 0, "applied": 0}

    current = read_md(MEMORY_MD)
    material = "\n\n".join(
        f"[{i+1}] （召回 {it.get('recalls', 0)} 次）{it['text'].strip()}"
        for i, it in enumerate(blocks)
    )
    answer = call_model(STAGE2_SYSTEM,
                        f"## 现有 MEMORY.md\n\n{current}\n\n## 候选记忆块\n\n{material}")
    ops = (answer or {}).get("ops") or []
    skip = (answer or {}).get("skip") or []

    applied = 0
    skipped = []
    if not dry_run and ops:
        backup([MEMORY_MD])
        new_text, applied, skipped = apply_ops(current, ops, level=2)
        if applied:
            MEMORY_MD.write_text(new_text, encoding="utf-8")

    log({"stage": 2, "ok": True, "candidates": len(blocks), "ops": len(ops),
         "applied": applied, "dry_run": dry_run,
         "skips": skipped[:20],
         "model_skips": [{"text": (s.get("text") or "")[:40], "reason": s.get("reason")}
                         for s in skip][:20]})
    result = {"stage": 2, "candidates": len(blocks), "ops": len(ops), "applied": applied}
    if applied and not dry_run and dream_cfg("tidy", True):
        result["tidy"] = tidy_file(MEMORY_MD, "MEMORY")
    if dry_run:
        result["preview"] = ops
    return result


# ---------------- 阶段三：MEMORY.md → PERSONA.md ----------------

STAGE3_SYSTEM = """PERSONA.md 是角色的人格、行为倾向、人生观、价值观、世界观。它比记忆更稳定，改动要非常慎重。

输入是她的长期记忆（MEMORY.md）和她现在的人格文件（PERSONA.md）。

任务：只有被多个独立事件反复印证的东西，才配写进人格。

必须同时满足：
1. 至少有三个不同事件支撑（在 MEMORY.md 里能找到，不是同一件事的重复）。
2. 是稳定的取向，不是一次性反应。
3. 与现有人格不矛盾。矛盾时不要改，把它记进 conflicts。

允许三种操作：
- add：新增一条人格描述。
- update：现有人格条目不准或写偏了，用新正文覆盖原文。
- merge：几条说的是同一取向，合成一条。

重要：人格不是只增不减。如果记忆里出现了足以修正旧描述的证据，
应当 update 原文；否则人格会朝一个方向滑坡，越走越偏。

输出 JSON：
{"ops":[
  {"op":"add","section":"行为倾向|人生观|价值观|世界观","title":"简短标题","text":"100字以内","evidence":["支撑的事件标题"]},
  {"op":"update","title":"现有人格条目标题","text":"改写后的完整正文","reason":"为什么要改"},
  {"op":"merge","titles":["标题A","标题B"],"title":"合并后的标题","text":"合并后的正文","reason":"为什么合并"}
 ],
 "skip":[{"text":"被跳过的取向","reason":"为什么不够格"}],
 "conflicts":[{"text":"观察到的新倾向","with":"与现有人格的哪一条冲突"}]}
"""

PERSONA_SECTIONS = ("行为倾向", "人生观", "价值观", "世界观")


def stage3(dry_run=False):
    memory_text = read_md(MEMORY_MD)
    persona_text = read_md(PERSONA_MD)
    if not memory_text.strip() or not persona_text.strip():
        log({"stage": 3, "ok": True, "skipped": "文件缺失"})
        return {"stage": 3, "skipped": True}

    answer = call_model(STAGE3_SYSTEM,
                        f"## 现有 PERSONA.md\n\n{persona_text}\n\n## MEMORY.md\n\n{memory_text}")
    ops = (answer or {}).get("ops") or []
    skip = (answer or {}).get("skip") or []
    conflicts = (answer or {}).get("conflicts") or []

    # 证据校验：evidence 里的事件标题必须真的在 MEMORY.md 里存在，
    # 凑不够 PERSONA_MIN_EVENTS 个就否决——模型会自我放宽，这里不能只信它。
    mem_titles = {m.strip() for m in re.findall(r"^## (.+?)\s*$", memory_text, re.M)}
    checked, skipped = [], []
    for op in ops:
        if str(op.get("op") or "add").lower() != "add":
            checked.append(op)
            continue
        evidence = [e.strip() for e in (op.get("evidence") or []) if str(e).strip()]
        valid = [e for e in evidence if e in mem_titles]
        if len(valid) < PERSONA_MIN_EVENTS:
            skipped.append({"op": "add", "title": op.get("title"),
                            "reason": f"证据不足：{len(valid)}/{PERSONA_MIN_EVENTS} 个事件（未在 MEMORY.md 找到）"})
            continue
        checked.append(op)
    ops = checked

    applied = 0
    if not dry_run and ops:
        backup([PERSONA_MD])
        new_text, applied, extra_skipped = apply_ops(persona_text, ops, level=3,
                                                     allowed_sections=PERSONA_SECTIONS)
        skipped += extra_skipped
        if applied:
            PERSONA_MD.write_text(new_text, encoding="utf-8")

    log({"stage": 3, "ok": True, "ops": len(ops), "applied": applied,
         "conflicts": len(conflicts), "dry_run": dry_run,
         "skips": skipped[:20],
         "model_skips": [{"text": (s.get("text") or "")[:40], "reason": s.get("reason")}
                         for s in skip][:20],
         "conflict_detail": conflicts[:10]})
    result = {"stage": 3, "ops": len(ops), "applied": applied, "conflicts": len(conflicts)}
    if applied and not dry_run and dream_cfg("tidy", True):
        result["tidy"] = tidy_file(PERSONA_MD, "PERSONA")
    if dry_run:
        result["preview"] = ops
        result["conflict_preview"] = conflicts
    return result


# ---------------- 阶段零：回顾目标（写 plan.log） ----------------

STAGE_PLAN_SYSTEM = """她在睡前回顾自己手上的目标。

输入：现有目标、最近一段对话。任务：判断这些目标还成不成立、走到哪了。

只做四件事，别的不要做：
- advance：这一步做完了，推进到下一步
- revise：目标写得不对或方向变了，改写它
- abandon：这件事不再做了，写清为什么
- keep：还在做，什么都别动

拿不准就 keep。不要为了显得有进展而乱推进。

输出 JSON：
{"ops":[
  {"op":"advance","goal":"g_0001","step":1,"note":"这一步做成了什么"},
  {"op":"revise","goal":"g_0001","objective":"新的说法","note":"为什么改"},
  {"op":"abandon","goal":"g_0001","note":"为什么不做"}
 ]}
"""


# ---------------- 升格之后的整理 ----------------

TIDY_SYSTEM = """你是一位编辑，正在整理一份角色的 Markdown 文档。

任务：把重复的、同一件事说了两遍的内容合并成一处，让结构干净。

规则：
- 只合并重复，不删信息：每一条不同的意思都要留下。
- 不新增任何事实、感受或评价，只改写法与位置。
- 保持原有标题层级（# / ## / ### / - 条目）与第一人称。
- 条目的措辞尽量沿用原文，不要重写成书面语。
- 整理后长度不得少于原文的七成，也不得超过一成半的增长。

输出 JSON：{"text": "整理后的完整文档"}
"""


def tidy_file(path, label, dry_run=False, min_ratio=0.7, max_ratio=1.15):
    """让模型把一份文档去重整理；校验不过就不写。"""
    text = read_md(path)
    if len(text.strip()) < 400:                 # 太短，没什么可整的
        return {"skipped": "文档太短"}
    before = len(text)
    answer = call_model(TIDY_SYSTEM, "## 文档\n\n" + text)
    cleaned = str((answer or {}).get("text") or "").strip()
    if not cleaned.startswith("#"):
        log({"tidy": label, "ok": False, "reason": "输出不像文档"})
        return {"ok": False, "reason": "输出不像文档"}
    after = len(cleaned)
    if not (before * min_ratio <= after <= before * max_ratio):
        log({"tidy": label, "ok": False, "reason": "长度越界",
             "before": before, "after": after})
        return {"ok": False, "reason": "长度越界", "before": before, "after": after}
    if cleaned.strip() == text.strip():
        return {"ok": True, "changed": False, "chars": after}
    if not dry_run:
        backup([path])
        path.write_text(cleaned.rstrip() + "\n", encoding="utf-8")
    log({"tidy": label, "ok": True, "before": before, "after": after,
         "dry_run": dry_run})
    return {"ok": True, "changed": True, "before": before, "after": after}


def stage_plan(dry_run=False):
    """睡前回顾目标：推进、改写或放弃，写进 plan.log。"""
    agenda = REALM / "plan" / "agenda.json"
    data = load_json_file(agenda, {}) or {}
    goals = [g for g in (data.get("goal") or []) if g.get("status") in ("active", "paused")]
    if not goals:
        log({"stage": "plan", "ok": True, "goals": 0})
        return {"stage": "plan", "goals": 0, "applied": 0}

    listing = "\n".join(
        f"- {g.get('id')}（{g.get('status')}）{g.get('objective')}\n" +
        "\n".join(f"    {s.get('id')}. [{s.get('status')}] {s.get('title')}"
                  for s in g.get("steps") or [])
        for g in goals)
    recent = ""
    session = _paths.data("session", "session.jsonl")
    if session.exists():
        lines = [l for l in session.read_text(encoding="utf-8").splitlines() if l.strip()]
        recent = "\n".join(lines[-8:])[:3000]

    answer = call_model(STAGE_PLAN_SYSTEM, f"## 现有目标\n\n{listing}\n\n## 最近的对话\n\n{recent}")
    ops = (answer or {}).get("ops") or []
    applied = 0
    if not dry_run:
        for op in ops:
            kind = str(op.get("op") or "").lower()
            if kind not in ("advance", "revise", "abandon"):
                continue
            args = {"goal": op.get("goal"), "note": op.get("note") or ""}
            if kind == "advance":
                args["action"] = "step_done"
                args["step"] = int(op.get("step") or 0)
            else:
                args["action"] = kind
                if op.get("objective"):
                    args["objective"] = op.get("objective")
            try:
                result = call_tool("plan", args)
                if result and result.get("ok"):
                    applied += 1
            except Exception as e:               # noqa: BLE001
                log({"stage": "plan", "ok": False, "goal": args.get("goal"),
                     "error": f"{type(e).__name__}: {e}"})

    log({"stage": "plan", "ok": True, "goals": len(goals), "ops": len(ops),
         "applied": applied, "dry_run": dry_run,
         "detail": [{"op": o.get("op"), "goal": o.get("goal")} for o in ops]})
    result = {"stage": "plan", "goals": len(goals), "ops": len(ops), "applied": applied}
    if dry_run:
        result["preview"] = ops
    return result


# ---------------- 入口 ----------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    stage = "all"
    top = 0
    dry_run = "--dry-run" in argv
    for i, arg in enumerate(argv):
        if arg == "--stage" and i + 1 < len(argv):
            stage = argv[i + 1]
        elif arg == "--top" and i + 1 < len(argv):
            top = int(argv[i + 1])

    results = []
    if stage in ("all", "plan"):
        results.append(stage_plan(dry_run=dry_run))
    if stage in ("all", "1"):
        results.append(stage1(top=top, dry_run=dry_run))
    if stage in ("all", "2"):
        results.append(stage2(dry_run=dry_run))
    if stage in ("all", "3"):
        results.append(stage3(dry_run=dry_run))

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if core.ERROR:
        for e in core.ERROR:
            print("  " + e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
