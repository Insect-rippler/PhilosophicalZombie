"""升格第三步：常驻记忆 → persona 微调。

规则写在 prompts/persona.md 里；这里只做校验与落盘。
任何不满足护栏的改动整条丢弃。
"""

import json
import os
import time
from pathlib import Path

import realm

PROMPT = Path(__file__).resolve().parent / "prompts" / "persona.md"
MAX_CHANGES = 2


def _log(row):
    try:
        path = Path(os.environ["PZ_DATA"]) / "dream.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                       # noqa: BLE001
        pass


def _persona_path():
    import core
    return Path(core.REALM_ROOT) / "PERSONA.md"


def load_persona():
    path = _persona_path()
    return path, realm.load(path, "PERSONA")


def _payload(persona, residents):
    import score as scorer
    system = {"role": "system", "content": PROMPT.read_text(encoding="utf-8")}
    lines = []
    for dim, facets in (persona.get("personality") or {}).items():
        lines.append(f"## {dim}")
        lines.extend(f"- {x}" for x in facets)
    parts = ["## 当前人格\n\n" + "\n".join(lines)]
    parts.append("## 常驻记忆\n\n" + "\n\n".join(
        f"### {r['id']}\n{r['text']}" for r in residents[:20]))
    try:
        background = scorer._background()
    except Exception:                       # noqa: BLE001
        background = []
    import core
    return {
        "model": (core.harness.get("model") or "deepseek-flash"),
        "messages": [system] + background + [
            {"role": "user", "content": "\n\n".join(parts)}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def _call(persona, residents):
    import modelio
    reply = modelio.send(_payload(persona, residents)) or {}
    text = (((reply.get("choices") or [{}])[0].get("message") or {})
            .get("content") or "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def validate(changes, persona, residents):
    """返回 (通过列表, 拒绝原因列表)。"""
    known = {r["id"] for r in residents}
    dims = persona.get("personality") or {}
    passed, rejected = [], []
    for row in changes:
        dim = str(row.get("dim") or "")
        facet = str(row.get("facet") or "")
        text = str(row.get("text") or "").strip()
        sources = [str(x) for x in (row.get("sources") or []) if str(x) in known]
        if dim not in dims:
            rejected.append(f"{dim}/{facet}: 维度不存在")
            continue
        old = next((x for x in dims[dim] if str(x).split("：")[0].strip() == facet), None)
        if old is None:
            rejected.append(f"{dim}/{facet}: facet 不存在")
            continue
        if "：" not in text or text.split("：")[0].strip() != facet:
            rejected.append(f"{dim}/{facet}: 新描述格式不对")
            continue
        if len(text) > 60:
            rejected.append(f"{dim}/{facet}: 描述太长")
            continue
        if len(sources) < 2:
            rejected.append(f"{dim}/{facet}: 依据不足 2 条")
            continue
        passed.append(dict(row, dim=dim, facet=facet, text=text, sources=sources))
    return passed[:MAX_CHANGES], rejected


def apply(passed, dry_run=False):
    if not passed or dry_run:
        return []
    path, persona = load_persona()
    dims = persona.setdefault("personality", {})
    applied = []
    for row in passed:
        facets = dims.get(row["dim"]) or []
        for i, old in enumerate(facets):
            if str(old).split("：")[0].strip() == row["facet"]:
                facets[i] = row["text"]
                applied.append({"dim": row["dim"], "facet": row["facet"],
                                "from": old, "to": row["text"]})
                break
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("---\n" + __import__("yaml").safe_dump(
        persona, allow_unicode=True, sort_keys=False, width=1000) + "---\n",
        encoding="utf-8")
    tmp.replace(path)
    return applied


def run(residents, dry_run=True):
    path, persona = load_persona()
    if not residents:
        return {"called": False, "changes": []}
    data = _call(persona, residents)
    if data is None:
        return {"called": True, "changes": [], "error": "输出不是 JSON"}
    passed, rejected = validate(data.get("changes") or [], persona, residents)
    applied = apply(passed, dry_run=dry_run)
    _log({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "persona",
          "proposed": len(data.get("changes") or []),
          "passed": [{"dim": r["dim"], "facet": r["facet"]} for r in passed],
          "rejected": rejected, "applied": applied, "dry_run": dry_run})
    return {"called": True, "proposed": data.get("changes") or [],
            "passed": passed, "rejected": rejected, "applied": applied}
