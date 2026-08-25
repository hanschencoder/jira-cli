"""输出渲染：yaml / json / table / md。

约定：
- yaml 是 AI 首选（噪音最少最省 token），json 供 jq 精确提取，
  table 给终端前的人看，md 用于粘进回复或文档。
- table/md 只能渲染「行的列表」，嵌套结构（如 issue show）会自动降级到 yaml。
- 这里不做业务字段裁剪，裁剪在 fields.py 完成。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Sequence

import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .errors import JiraCliError

FORMATS = ("table", "yaml", "json", "md")
DEFAULT_FORMAT = "table"

#: 这些列承载长文本，表格里让它们吃掉剩余宽度
_WIDE_COLUMNS = frozenset({"summary", "description", "name", "body", "path", "filename", "取值"})

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


#: 时间戳形如 2026-08-25 14:24:42.000，表格里显示到秒会把其它列挤没，截到分钟
_ISO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}):\d{2}")


def _stringify(value: Any, *, compact: bool = False) -> str:
    """compact=True 用于表格/md 视图：缩短时间戳等纯展示性内容。

    yaml/json 不走这里，始终保留完整精度。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v, compact=compact) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    if compact:
        matched = _ISO_TS.match(text)
        if matched:
            return f"{matched.group(1)} {matched.group(2)}"
    return text


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
        cells = [
            _stringify(row.get(c), compact=True).replace("|", "\\|").replace("\n", " ")
            for c in cols
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _print_table(rows: Sequence[dict], columns: Sequence[str] | None = None) -> None:
    if not rows:
        _out.print("（无结果）")
        return
    cols = list(columns) if columns else _columns_of(rows)
    table = Table(show_header=True, header_style="bold", show_lines=False, pad_edge=False)
    for col in cols:
        # 标题类长文本列让它占据剩余宽度，标识/枚举类列不折行，
        # 否则窄终端下每列都折行会把表格挤成面条
        if col in _WIDE_COLUMNS:
            table.add_column(col, overflow="fold", ratio=3, min_width=20)
        else:
            table.add_column(col, overflow="ellipsis", no_wrap=True)
    for row in rows:
        table.add_row(*[_stringify(row.get(c), compact=True).replace("\n", " ") for c in cols])
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
    if fmt not in FORMATS:
        # 静默降级最坑调用方：-o josn 会安静地渲染成表格，
        # 而 AI 拿到手还当 JSON 去 parse
        raise JiraCliError(
            f"未知的输出格式：{fmt}",
            f"可用格式：{' / '.join(FORMATS)}",
        )

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


# 消息里的方括号必须转义后再交给 rich：Jira 的错误正文、项目名、附件名
# 里带 [] 很常见，而 [red] / [link] 这种恰好是合法样式名的会被**静默吞掉**，
# [/] 更是直接抛 MarkupError——错误信息自己把自己弄崩了
def note(message: str) -> None:
    """诊断信息走 stderr，不污染 stdout 的结构化输出。"""
    _err.print(f"[dim]{escape(message)}[/dim]")


def warn(message: str) -> None:
    _err.print(f"[yellow]警告：[/yellow]{escape(message)}")


def fail(message: str) -> None:
    _err.print(f"[red]错误：[/red]{escape(message)}")
