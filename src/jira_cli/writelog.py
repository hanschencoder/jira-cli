"""写操作留痕。

本工具不做 dry-run、不做批量写，护栏只有这一层：每次写操作追加一行 JSON，
出事能追溯「什么时候、对哪个 issue、改了什么、结果如何」。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import write_log_path


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
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def tail(limit: int = 20) -> list[dict]:
    """回查最近 limit 条留痕，新的在前。"""
    path = write_log_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[-limit:][::-1]
