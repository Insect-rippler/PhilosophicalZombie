"""睡着时的维护：压缩 session + 升格记忆。

概念上这是她的梦——睡够了自然整理好，没整理完就是头昏眼花。
所以这个 runner 只在后台跑，不参与对话回合。

流程：
    1. 读 state/realTime/maintenance.json 的 due
    2. 让模型写一份会话提要
    3. 调 sessionSummary.compress(提要)  —— 归档旧轮次、切片入库
    4. 跑 dream 的三个阶段           —— 切片→块→记忆→人格
    5. 全部成功才写 doneFor = sleepStartedAt；任何一步失败就不写，
       她醒来就带着「头昏眼花」的负面状态

用法：
    python engine/maintain.py            # 按标记跑
    python engine/maintain.py --dry-run  # 只报告要做什么
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

MARK = ROOT / "state" / "realTime" / "maintenance.json"
SESSION = ROOT / "state" / "session" / "session.jsonl"

import core                      # noqa: E402
import dream                     # noqa: E402

import json as _json
import subprocess


def call_tool(name, args, timeout=600):
    """和工具层用同一套调用方式：起子进程，stdin 传 JSON，stdout 收结果。

    不 import 具体实现——这样改了 function/ 下的脚本立刻生效，不用重启。
    """
    entry = core.load_registry().get(name)
    if not entry or not entry.get("path"):
        return None
    script = (ROOT / entry["path"]).resolve()
    if not script.exists():
        return None
    proc = subprocess.run([sys.executable, str(script)],
                          input=_json.dumps(args, ensure_ascii=False),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(ROOT), timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:300])
    try:
        return _json.loads(proc.stdout)
    except _json.JSONDecodeError:
        return {"raw": proc.stdout}

SUMMARY_PROMPT = """你在整理一个角色最近这段时间的对话。

把下面这些轮次压缩成一段提要，供她以后回看。

规则：
- 150~300 字，第一人称，写在"我"的视角。
- 只写：发生了什么、达成了什么、还欠着什么。
- 不写细节流水，不复述原话，不加评论。
- 输出 JSON：{"summary": "提要正文"}
"""


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def pending_mark():
    mark = _read(MARK, {}) or {}
    if mark.get("due"):
        return mark
    return None


def make_summary(limit=12):
    """读最近几轮，让模型写提要。失败返回 None。"""
    if not SESSION.exists():
        return None
    lines = [l for l in SESSION.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    recent = lines[-limit:]
    body = "\n".join(recent)[:6000]
    answer = dream.call_model(SUMMARY_PROMPT, body, max_tokens=800)
    if not isinstance(answer, dict):
        return None
    text = str(answer.get("summary") or "").strip()
    return text or None


def run(dry_run=False):
    mark = pending_mark()
    if not mark:
        return {"ok": True, "skipped": "没有待处理的维护"}

    started = float(mark.get("sleepStartedAt") or 0)
    plan = {"sleepStartedAt": started, "dry_run": dry_run}

    summary = "(dry-run)" if dry_run else make_summary()
    if not summary:
        return {"ok": False, "reason": "提要生成失败", **plan}
    plan["summary_chars"] = len(summary)

    if dry_run:
        plan["compressed"] = "（dry-run 跳过子进程调用）"
    else:
        try:
            compressed = call_tool("sessionSummary", {"content": summary})
        except Exception as e:               # noqa: BLE001
            return {"ok": False, "reason": f"压缩失败：{type(e).__name__}: {e}", **plan}
        plan["compressed"] = compressed

    stages = []
    try:
        stages.append(dream.stage_plan(dry_run=dry_run))   # 先回顾目标，写 plan.log
    except Exception as e:                   # noqa: BLE001
        return {"ok": False, "reason": f"目标回顾失败：{type(e).__name__}: {e}", **plan}
    for stage in ("1", "2", "3"):
        try:
            if stage == "1":
                stages.append(dream.stage1(dry_run=dry_run))
            elif stage == "2":
                stages.append(dream.stage2(dry_run=dry_run))
            else:
                stages.append(dream.stage3(dry_run=dry_run))
        except Exception as e:               # noqa: BLE001
            return {"ok": False, "reason": f"升格 stage{stage} 失败：{type(e).__name__}: {e}",
                    **plan, "stages": stages}
    plan["stages"] = stages

    if not dry_run:
        _write(MARK, {"due": False, "doneFor": started,
                      "finishedAt": time.time(), "summary_chars": len(summary)})
    return {"ok": True, **plan}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    result = run(dry_run="--dry-run" in argv)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if core.ERROR:
        for item in core.ERROR:
            print("  " + item, file=sys.stderr)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
