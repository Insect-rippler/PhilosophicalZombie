"""印象维护：定期把相处里看见的东西写进 USER.md。

触发：距上次更新超过 6 小时，且期间用户消息不少于阈值（默认 50）。
睡醒维护之后也会检查一次。

更新方式：模型输出一组操作，代码执行；对不上原文的替换只记警告，不硬改。
"""

import json
import sys
import threading
import time
from pathlib import Path

import paths as _paths

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

USER_PATH = _paths.ROOT / "ConsciousnessRealm" / "USER.md"
MARK = _paths.data("realTime", "impression.json")
SESSION = _paths.data("session", "session.jsonl")

SECTIONS = ("怎么认识的", "我知道的", "我不知道的", "我的分寸", "一句")
MIN_GAP_HOURS = 6.0
MIN_USER_MSGS = int(__import__("os").environ.get("PZ_IMPRESSION_MIN", "50"))

_LOCK = threading.Lock()

PROMPT = """你在整理一个角色对某个人的印象。

下面是当前的印象文件，和最近这段时间那个人说过的话。

请输出一组修改操作，只改真正变了的地方；没变就返回空列表。

格式：
{"ops": [
  {"mode": "append", "section": "我知道的", "content": "一条新事实"},
  {"mode": "replace", "section": "我的分寸", "target": "要替换掉的原句", "content": "新的说法"},
  {"mode": "rewrite", "section": "一句", "content": "整节新内容"}
]}

规则：
- section 只能是：怎么认识的 / 我知道的 / 我不知道的 / 我的分寸 / 一句
- append：加一条，不要重复已有的意思
- replace：target 必须是原文里完整的一行或一句，能原样找到
- rewrite：整节替换，旧内容不再保留
- 只写她看见、听见的，不写猜测出来的隐私
- 最多 3 条操作；没有值得改的就返回 {"ops": []}
- 只输出这个 JSON，不要任何解释文字
"""


def _read_mark():
    try:
        return json.loads(Path(MARK).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"lastAt": 0, "userMsgs": 0}


def _write_mark(data):
    path = Path(MARK)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _user_messages_since(ts):
    path = Path(SESSION)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("role") != "user":
            continue
        out.append(str(row.get("content") or "")[:200])
    return out


def due(now=None):
    now = time.time() if now is None else now
    mark = _read_mark()
    gap_h = (now - float(mark.get("lastAt") or 0)) / 3600
    if gap_h < MIN_GAP_HOURS:
        return False
    return len(_user_messages_since(mark.get("lastAt"))) >= MIN_USER_MSGS


def apply_ops(text, ops):
    """把操作落到 markdown 上；返回（新文本, 应用条数, 警告）。"""
    lines = text.splitlines()
    applied, warns = 0, []

    def section_range(name):
        head = f"## {name}"
        start = next((i for i, l in enumerate(lines) if l.strip() == head), None)
        if start is None:
            return None
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        return start, end

    for op in ops or []:
        mode = str(op.get("mode") or "")
        name = str(op.get("section") or "")
        content = str(op.get("content") or "").strip()
        if name not in SECTIONS or not content:
            warns.append(f"跳过非法操作：{mode}/{name}")
            continue
        rng = section_range(name)
        if rng is None:
            warns.append(f"找不到小节：{name}")
            continue
        start, end = rng
        body = [l for l in lines[start + 1:end]]
        if mode == "append":
            while body and not body[-1].strip():
                body.pop()
            body += ["", f"- {content}"]
        elif mode == "replace":
            target = str(op.get("target") or "").strip()
            hit = next((i for i, l in enumerate(body) if target and target in l), None)
            if hit is None:
                warns.append(f"替换目标未找到：{target[:20]}")
                continue
            body[hit] = l if False else body[hit].replace(target, content)
        elif mode == "rewrite":
            body = ["", content]
        else:
            warns.append(f"未知模式：{mode}")
            continue
        lines = lines[:start + 1] + body + lines[end:]
        applied += 1
    return "\n".join(lines).rstrip() + "\n", applied, warns


def run(dry_run=False):
    if not USER_PATH.exists():
        return {"ok": False, "reason": "USER.md 不存在"}
    import dream

    current = USER_PATH.read_text(encoding="utf-8")
    recent = _user_messages_since(_read_mark().get("lastAt"))
    body = "## 当前印象\n" + current + "\n\n## 最近他说过的话\n" + "\n".join(recent[-80:])
    answer = dream.call_model(PROMPT, body[:6000], max_tokens=1200)
    if not isinstance(answer, dict):
        return {"ok": False, "reason": "模型未返回结果"}
    ops = answer.get("ops") or []
    new_text, applied, warns = apply_ops(current, ops)
    if not dry_run and applied:
        USER_PATH.write_text(new_text, encoding="utf-8")
    if not dry_run:
        _write_mark({"lastAt": time.time(), "userMsgs": len(recent)})
    return {"ok": True, "applied": applied, "warns": warns, "ops": len(ops), "dry_run": dry_run}


def maybe_update(force=False):
    """在 idle_tick 里调用：条件满足就后台更新一次。"""
    if not (force or due()):
        return False
    if not _LOCK.acquire(blocking=False):
        return False

    def job():
        try:
            run()
        finally:
            _LOCK.release()

    threading.Thread(target=job, daemon=True).start()
    return True


if __name__ == "__main__":
    print(json.dumps(run(dry_run="--dry-run" in sys.argv), ensure_ascii=False, indent=2))
