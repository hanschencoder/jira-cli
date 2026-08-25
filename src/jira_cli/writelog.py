"""写操作留痕。

本工具不做 dry-run、不做批量写，护栏只有这一层：每次写操作追加一行 JSON，
出事能追溯「什么时候、对哪个 issue、改了什么、结果如何」。
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .config import ensure_private_dir, write_log_path
from .timefmt import format_ts


def record(op: str, key: str, payload: Any = None, ok: bool = True, result: Any = None) -> None:
    """追加一条留痕。留痕本身失败不能影响主流程。"""
    entry = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "op": op,
        "key": key,
        "payload": payload,
        "ok": ok,
    }
    if result is not None:
        entry["result"] = result
    try:
        path = write_log_path()
        ensure_private_dir(path.parent)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        # 留痕里有 issue 正文和评论原文，别按 umask 落成同组可读
        path.chmod(0o600)
    except OSError:
        pass


def tail(limit: int = 20) -> list[dict]:
    """回查最近 limit 条留痕，新的在前。

    日志只追加不轮转，会一直长。用定长队列滚动，内存不随文件大小涨。
    """
    path = write_log_path()
    if not path.exists():
        return []
    kept: deque[str] = deque(maxlen=max(limit, 1))
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                kept.append(line)

    rows: list[dict] = []
    for line in kept:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        entry["ts"] = format_ts(entry.get("ts"))
        rows.append(entry)
    return rows[::-1]
