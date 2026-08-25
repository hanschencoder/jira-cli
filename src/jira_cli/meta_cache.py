"""元数据本地缓存。

项目/类型/状态/优先级/字段这些东西极少变，但每次名称解析都要用。
缓存到本地避免每条命令都多打几个来回。jira-cli meta update 强制刷新。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from .config import config_dir, ensure_private_dir

#: 缓存有效期（秒）。元数据变更频率很低，7 天足够
TTL = 7 * 24 * 3600

#: 当前实例的缓存命名空间，由 set_scope() 设置
_scope = ""


def set_scope(url: str) -> None:
    """按实例隔离缓存。进程级设置，与 timefmt.set_timezone 同理。

    同一份配置目录会先后连不同实例（`--url` / `JIRA_URL` 覆盖，本来就是
    支持的用法）。共用一份缓存的话，项目、字段、状态的名称解析会**静默**
    落到上一个实例的数据上——不报错，只是解析到了别的项目。
    """
    global _scope
    host = (url or "").strip().rstrip("/")
    _scope = hashlib.sha256(host.encode("utf-8")).hexdigest()[:8] if host else ""


def cache_path(name: str) -> Path:
    directory = config_dir() / "cache"
    return directory / (f"{name}.{_scope}.json" if _scope else f"{name}.json")


def read(name: str, ttl: int = TTL) -> Any | None:
    """读缓存。不存在或过期返回 None。"""
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if time.time() - payload.get("at", 0) > ttl:
        return None
    return payload.get("data")


def write(name: str, data: Any) -> None:
    """写缓存。写不进去不该影响主流程——缓存只是省一次往返。"""
    try:
        path = cache_path(name)
        ensure_private_dir(path.parent)
        path.write_text(
            json.dumps({"at": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def cached(name: str, loader: Callable[[], Any], refresh: bool = False) -> Any:
    """取缓存，未命中则调 loader 并写回。"""
    if not refresh:
        hit = read(name)
        if hit is not None:
            return hit
    data = loader()
    write(name, data)
    return data


def clear() -> int:
    """清空缓存，返回删除的文件数。"""
    directory = config_dir() / "cache"
    if not directory.exists():
        return 0
    count = 0
    for path in directory.glob("*.json"):
        path.unlink()
        count += 1
    return count
