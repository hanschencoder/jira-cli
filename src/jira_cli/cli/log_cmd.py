"""log 子命令：回查写操作留痕。"""

from __future__ import annotations

import typer

from ..output import emit
from ..writelog import tail
from .common import FORMAT_OPTION

app = typer.Typer(help="回查写操作留痕")


@app.callback(invoke_without_command=True)
def log_cmd(
    ctx: typer.Context,
    limit: int = typer.Option(20, "-n", "--limit", help="最多回查条数"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """列出最近的写操作（新的在前）。"""
    if ctx.invoked_subcommand:
        return
    rows = tail(limit)
    emit(
        {"total": len(rows), "entries": rows},
        fmt,
        rows=[
            {
                "ts": r.get("ts"),
                "op": r.get("op"),
                "key": r.get("key"),
                "ok": r.get("ok"),
            }
            for r in rows
        ],
    )
