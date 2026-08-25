"""输出渲染：yaml / json / table / md。

约定：
- yaml 是 AI 首选（噪音最少最省 token），json 供 jq 精确提取，
  table 给终端前的人看，md 用于粘进回复或文档。
- table/md 只能渲染「行的列表」，嵌套结构（如 issue show）会自动降级到 yaml。
- 这里不做业务字段裁剪，裁剪在 fields.py 完成。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Sequence

import yaml
from rich.console import Console
from rich.table import Table

FORMATS = ("table", "yaml", "json", "md")
DEFAULT_FORMAT = "table"

# stdout 给数据，stderr 给诊断信息，两者不能混
_out = Console(file=sys.stdout, soft_wrap=True)
_err = Console(file=sys.stderr, soft_wrap=True)


def _yaml_str_presenter(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    """多行字符串用块字面量（|），比一行里塞满 \\n 可读且省 token。"""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _yaml_str_presenter)


def to_yaml(data: Any) -> str:
    return yaml.dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    ).rstrip("\n")


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _columns_of(rows: Sequence[dict]) -> list[str]:
    """并集，保持首次出现顺序——让列顺序跟着数据结构走而非字母序。"""
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def to_md(rows: Sequence[dict], columns: Sequence[str] | None = None) -> str:
    """Markdown 表格。"""
    if not rows:
        return "_（无结果）_"
    cols = list(columns) if columns else _columns_of(rows)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in rows:
        cells = [_stringify(row.get(c)).replace("|", "\\|").replace("\n", " ") for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _print_table(rows: Sequence[dict], columns: Sequence[str] | None = None) -> None:
    if not rows:
        _out.print("（无结果）")
        return
    cols = list(columns) if columns else _columns_of(rows)
    table = Table(show_header=True, header_style="bold", show_lines=False)
    for col in cols:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[_stringify(row.get(c)).replace("\n", " ") for c in cols])
    _out.print(table)


def emit(
    data: Any,
    fmt: str = DEFAULT_FORMAT,
    *,
    rows: Sequence[dict] | None = None,
    columns: Sequence[str] | None = None,
) -> None:
    """把结果写到 stdout。

    data  结构化结果，yaml/json 直接序列化它
    rows  可选的「行视图」，table/md 用它渲染。为 None 时：
          data 本身是行列表就用 data，否则降级到 yaml（嵌套结构画不成表）
    """
    fmt = (fmt or DEFAULT_FORMAT).lower()

    if fmt == "yaml":
        _out.print(to_yaml(data), markup=False, highlight=False)
        return
    if fmt == "json":
        _out.print(to_json(data), markup=False, highlight=False)
        return

    table_rows = rows
    if table_rows is None:
        table_rows = data if _is_row_list(data) else None
    if table_rows is None:
        # 嵌套结构没有合理的表格投影，给 yaml 而不是硬画
        _out.print(to_yaml(data), markup=False, highlight=False)
        return

    if fmt == "md":
        _out.print(to_md(table_rows, columns), markup=False, highlight=False)
        return
    _print_table(table_rows, columns)


def _is_row_list(data: Any) -> bool:
    return isinstance(data, list) and all(isinstance(item, dict) for item in data)


def note(message: str) -> None:
    """诊断信息走 stderr，不污染 stdout 的结构化输出。"""
    _err.print(f"[dim]{message}[/dim]")


def warn(message: str) -> None:
    _err.print(f"[yellow]警告：[/yellow]{message}")


def fail(message: str) -> None:
    _err.print(f"[red]错误：[/red]{message}")
