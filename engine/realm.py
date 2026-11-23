# -*- coding: utf-8 -*-
"""识域结构化：YAML front-matter 字段 + prose，按固定顺序渲染。

设计稿见 docs/realm-schema.md。这里只实现最小可用版：
- load(path)  → {"schema":1, 字段..., "prose": "..."}
- render(name, data) → 固定顺序的 markdown 文本
- validate(name, data) → 缺失/多余字段列表
"""

from pathlib import Path

import yaml

# 每个文件的字段顺序，渲染与校验都按它来
FIELDS = {
    "PERSONA": ["self", "temperament", "attachment", "personality", "values",
                "regulation"],
    "CHARACTER": ["name", "aliases", "origin", "identity", "facts",
                  "timeline", "body", "prose"],
    "MEMORY": ["anchors", "prose"],
    "LOREBOOK": ["entries", "prose"],
    "USER": ["met", "knows", "unsure", "stance", "line", "prose"],
}

DEFAULTS = {
    "PERSONA": {"self": [], "temperament": [], "attachment": [],
                "personality": {}, "values": [], "regulation": []},
    "CHARACTER": {"name": "", "aliases": [], "origin": "", "identity": [],
                  "facts": [], "timeline": [], "body": {}, "prose": ""},
    "MEMORY": {"anchors": [], "prose": ""},
    "LOREBOOK": {"entries": [], "prose": ""},
    "USER": {"met": "", "knows": [], "unsure": [], "stance": [],
             "line": "", "prose": ""},
}


def load(path, name):
    text = Path(path).read_text(encoding="utf-8")
    fields = dict(DEFAULTS.get(name, {}))
    fields["schema"] = 1
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            data = yaml.safe_load(text[3:end]) or {}
            if isinstance(data, dict):
                fields.update(data)
            body = text[end + 4:].strip()
            if body:
                fields["prose"] = (fields.get("prose") or "") + "\n" + body
    else:
        fields["prose"] = text.strip()
    for key in FIELDS.get(name, []):
        fields.setdefault(key, DEFAULTS.get(name, {}).get(key))
    return fields


def _lines(items, fmt):
    if not items:
        return ""
    return "\n".join(fmt(i) for i in items if i)


def render(name, data):
    """按字段表顺序拼成纯文本：不带字段名、不带小标题。

    字段怎么分是给人看的；模型只需要按固定顺序读到内容。
    空字段整段省略，顺序永远一致。
    """
    d = dict(DEFAULTS.get(name, {}))
    d.update(data or {})
    out = []
    if name == "PERSONA":
        out.extend(d.get("self") or [])
        out.extend(d.get("temperament") or [])
        out.extend(d.get("attachment") or [])
        persona = d.get("personality") or {}
        for dim in ("开放性", "尽责性", "外向性", "宜人性", "神经质", "诚实谦逊"):
            lines = persona.get(dim) or []
            if lines:
                out.append(f"{dim}：\n" + "\n".join(f"- {x}" for x in lines))
        out.extend(d.get("values") or [])
        out.extend(d.get("regulation") or [])
    elif name == "CHARACTER":
        head = []
        if d.get("name"):
            head.append(str(d["name"]))
        if d.get("aliases"):
            head.append("、".join(d["aliases"]))
        if d.get("origin"):
            head.append(str(d["origin"]))
        if head:
            out.append(" · ".join(head))
        out.append(_lines(d.get("identity"), lambda x: f"- {x}"))
        out.append(_lines(d.get("facts"), lambda x: f"- {x}"))
        out.append(_lines(d.get("timeline"),
                          lambda t: f"- {t.get('when')}：{t.get('what')}"))
        body = d.get("body") or {}
        if body:
            out.append("\n".join(f"- {k}：{v}" for k, v in body.items()))
    elif name == "MEMORY":
        out.append(_lines(d.get("anchors"),
                          lambda a: f"- {a.get('text')}"))
    elif name == "LOREBOOK":
        out.append(_lines(d.get("entries"),
                          lambda e: f"- {e.get('title')}：{e.get('text')}"))
    elif name == "USER":
        if d.get("met"):
            out.append(str(d["met"]))
        out.append(_lines(d.get("knows"), lambda x: f"- {x}"))
        out.append(_lines(d.get("unsure"), lambda x: f"- 不确定：{x}"))
        out.append(_lines(d.get("stance"), lambda x: f"- {x}"))
        if d.get("line"):
            out.append(str(d["line"]))
    prose = (d.get("prose") or "").strip()
    if prose:
        out.append(prose)
    return "\n\n".join(x for x in out if x).strip() + "\n"


def validate(name, data):
    """返回（缺失字段, 多余字段）。"""
    known = set(FIELDS.get(name, [])) | {"schema"}
    missing = [k for k in FIELDS.get(name, []) if k not in (data or {})]
    extra = [k for k in (data or {}) if k not in known]
    return missing, extra
