"""DeepSeek Files API：把本地图片换成 file_id，供 messages 里的 `file` 内容块引用。

上传接口是 multipart，`purpose` 必填，有效期只能通过
`expires_after[anchor]` + `expires_after[seconds]` 两个表单字段给——
嵌套 dict 会被静默忽略，文件就变成永久有效，所以这里显式写成平铺字段。

上传结果按文件内容的 sha1 缓存到 `state/file/hash.json`，
同一张图反复发送不会重复上传；过期前 1 小时内不复用。
"""

from __future__ import annotations

import hashlib
import json
import paths as _paths
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = _paths.data("file", "hash.json")

MAX_BYTES = 64 * 1024 * 1024          # 单文件上限
EXPIRE_SECONDS = 2592000              # 30 天，接口允许的最大值
REUSE_MARGIN = 3600                   # 剩余不足 1 小时就重新上传

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".gif": "image/gif", ".webp": "image/webp"}


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FileAPIError(RuntimeError):
    pass


class FileAPI:
    def __init__(self, api_key: str, *, base: str = "https://api.deepseek.com",
                 verify_ssl: bool = False, cache_path: Path = CACHE_PATH):
        self.api_key = api_key
        self.base = base.rstrip("/")
        self.verify_ssl = verify_ssl
        self.cache_path = Path(cache_path)
        self.cache = self._load()

    # ---------- 缓存 ----------

    def _load(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _cached(self, digest: str) -> str | None:
        rec = self.cache.get(digest)
        if not rec:
            return None
        expires_at = rec.get("expires_at")
        if expires_at and time.time() > float(expires_at) - REUSE_MARGIN:
            return None
        return rec.get("id")

    # ---------- 上传 ----------

    def upload(self, path: str | Path) -> str:
        """上传一张图片，返回 file_id；命中缓存时直接返回。"""
        import requests
        import urllib3
        urllib3.disable_warnings()

        path = Path(path)
        if not path.exists():
            raise FileAPIError(f"文件不存在：{path}")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise FileAPIError(f"不支持的文件格式：{path.suffix}")
        size = path.stat().st_size
        if size > MAX_BYTES:
            raise FileAPIError(f"超过 64 MiB 上限：{size} 字节")

        digest = sha1_of(path)
        hit = self._cached(digest)
        if hit:
            return hit

        with open(path, "rb") as f:
            response = requests.post(
                f"{self.base}/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (path.name, f, _MIME.get(path.suffix.lower(), "application/octet-stream"))},
                data={
                    "purpose": "user_data",
                    "expires_after[anchor]": "created_at",
                    "expires_after[seconds]": str(EXPIRE_SECONDS),
                },
                timeout=(15, 300), verify=self.verify_ssl,
                proxies={"http": None, "https": None},
            )
        if response.status_code != 200:
            raise FileAPIError(f"上传失败 HTTP {response.status_code}: {response.text[:200]}")

        info = response.json()
        file_id = info.get("id")
        if not file_id:
            raise FileAPIError(f"响应里没有 id：{info}")
        self.cache[digest] = {
            "id": file_id, "name": path.name, "bytes": size,
            "expires_at": info.get("expires_at"),
            "uploaded_at": int(time.time()),
        }
        self._save()
        return file_id


# ---------------- 消息清洗 ----------------

def _local_path(part: dict) -> str | None:
    """从内容块里取出本地图片路径；不是本地图片时返回 None。"""
    kind = part.get("type")
    if kind == "image":
        return part.get("path") or part.get("url")
    if kind == "image_url":
        url = (part.get("image_url") or {}).get("url") or part.get("url") or ""
        if url.startswith(("http://", "https://", "data:")):
            return None
        return url
    return None


def clean_messages(messages: list[dict], api: FileAPI | None) -> tuple[list[dict], list[str]]:
    """把所有本地图片路径换成 `{"type": "file", "file_id": ...}`。

    返回（清洗后的消息，问题列表）。上传失败不会中断请求，
    而是在原位写一条文字说明，让模型知道这张图没送到。
    """
    issues: list[str] = []
    out: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue

        parts: list[dict] = []
        changed = False
        for part in content:
            if not isinstance(part, dict):
                parts.append(part)
                continue
            local = _local_path(part)
            if not local:
                parts.append(part)
                continue

            changed = True
            if api is None:
                parts.append({"type": "text", "text": f"[图片未发送：{Path(local).name}，未配置 API key]"})
                continue
            try:
                file_id = api.upload(local)
            except Exception as e:                       # noqa: BLE001
                issues.append(f"{Path(local).name}: {type(e).__name__}: {e}")
                parts.append({"type": "text", "text": f"[图片发送失败：{Path(local).name}]"})
                continue
            parts.append({"type": "file", "file_id": file_id})

        out.append({**msg, "content": parts} if changed else msg)
    return out, issues
