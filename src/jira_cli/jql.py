"""JQL 链式构造器。

设计要点：封装过滤参数与用户直接给的 --jql **汇入同一个 builder**
（后者走 .raw()），而不是维护两套代码路径。两者可以共存，用 AND 合并。
"""

from __future__ import annotations

import re

from .errors import JiraCliError

#: 不加引号、原样进 JQL 的函数式取值
FUNCTION_VALUES = {
    "currentUser()",
    "openSprints()",
    "closedSprints()",
    "futureSprints()",
    "unreleasedVersions()",
    "releasedVersions()",
    "membersOf",
    "EMPTY",
    "NULL",
}

#: 相对日期，如 -7d / -2w / 1h；JQL 原生支持，不能加引号
_RELATIVE_DATE_RE = re.compile(r"^-?\d+[mhdwMy]$")

#: --updated '>=-7d' 这种带比较符的写法
_OP_RE = re.compile(r"^\s*(>=|<=|!=|>|<|=)\s*(.+)$")


def escape_value(value: str) -> str:
    """转义 JQL 字符串字面量。

    JQL 用双引号包裹字符串，反斜杠与双引号必须转义，
    否则含引号的取值会截断语句（注入风险）。
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def quote(value: str) -> str:
    """按需加引号。函数式取值、相对日期、纯数字保持裸值。"""
    value = str(value).strip()
    if not value:
        return '""'
    if value in FUNCTION_VALUES or value.endswith("()"):
        return value
    if value.startswith("membersOf("):
        return value
    if _RELATIVE_DATE_RE.match(value):
        return value
    if value.isdigit():
        return value
    return f'"{escape_value(value)}"'


def normalize_user(value: str) -> str:
    """me 是当前 token 用户的简写。"""
    if value.strip().lower() == "me":
        return "currentUser()"
    return value


def split_order_by(expression: str) -> tuple[str, str]:
    """把尾部的 ORDER BY 子句从条件里拆出来。

    只认**顶层**（不在引号内、不在括号内）的 ORDER BY——
    `summary ~ "ORDER BY"` 这种取值里的关键字不能误伤。
    """
    text = expression.strip()
    upper = text.upper()
    quote: str | None = None
    depth = 0
    found: int | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and upper.startswith("ORDER", index):
            after = upper[index + 5 :]
            stripped = after.lstrip()
            starts_word = index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_")
            if starts_word and len(after) != len(stripped) and stripped.startswith("BY"):
                found = index
        index += 1
    if found is None:
        return text, ""
    return text[:found].strip(), text[found:].strip()


class JQL:
    """链式构造 JQL。所有 filter 方法返回 self，便于串联。"""

    def __init__(self) -> None:
        self._clauses: list[str] = []
        self._order: str = ""

    # -- 基础子句 ----------------------------------------------------------
    def filter_by(self, field: str, value: str | None, op: str = "=") -> "JQL":
        if value is None or value == "":
            return self
        self._clauses.append(f"{field} {op} {quote(value)}")
        return self

    def in_(self, field: str, values: list[str] | None) -> "JQL":
        """多值用 IN；单值退化成 = ，语句更短也更省 token。"""
        if not values:
            return self
        if len(values) == 1:
            return self.filter_by(field, values[0])
        joined = ", ".join(quote(v) for v in values)
        self._clauses.append(f"{field} in ({joined})")
        return self

    def contains(self, field: str, value: str | None) -> "JQL":
        """~ 是 JQL 的文本包含操作符。"""
        return self.filter_by(field, value, op="~")

    def raw(self, expression: str | None) -> "JQL":
        """原样并入一段 JQL（--jql 的入口）。

        条件部分要包进括号才能安全地和其它子句 AND 合并，但 **ORDER BY
        不能出现在括号里**——`(a = 1 ORDER BY b)` 不是合法 JQL。所以先把
        尾部的 ORDER BY 拆出来单独安置。
        """
        condition, order = split_order_by(expression or "")
        if condition:
            self._clauses.append(f"({condition})")
        if order:
            self._order = order
        return self

    def has_order(self) -> bool:
        return bool(self._order)

    def date(self, field: str, expr: str | None) -> "JQL":
        """日期过滤。

        支持三种写法：
          >=2026-05-01 / <=-7d   带比较符
          2026-05-01|2026-05-31  闭区间
          2026-05-01             等价于 >= 当天
        """
        expr = (expr or "").strip()
        if not expr:
            return self
        if "|" in expr:
            start, _, end = expr.partition("|")
            start, end = start.strip(), end.strip()
            if start:
                self._clauses.append(f"{field} >= {quote(start)}")
            if end:
                self._clauses.append(f"{field} <= {quote(end)}")
            return self
        match = _OP_RE.match(expr)
        if match:
            op, value = match.group(1), match.group(2).strip()
            self._clauses.append(f"{field} {op} {quote(value)}")
            return self
        self._clauses.append(f"{field} >= {quote(expr)}")
        return self

    def status(self, value: str | None) -> "JQL":
        """状态过滤，带三个约定俗成的简写。

        open   未完成（statusCategory != Done）
        closed 已完成
        *      不过滤（Jira 本身不像 Redmine 那样默认只返回未关闭，
               但保留这个写法以便和 redmine-cli 的习惯对齐）
        """
        value = (value or "").strip()
        if not value or value == "*":
            return self
        lowered = value.lower()
        if lowered == "open":
            self._clauses.append("statusCategory != Done")
            return self
        if lowered in ("closed", "done"):
            self._clauses.append("statusCategory = Done")
            return self
        return self.filter_by("status", value)

    def order_by(self, field: str | None, direction: str = "DESC") -> "JQL":
        if not field:
            return self
        if ":" in field:
            field, _, direction = field.partition(":")
        direction = (direction or "DESC").upper()
        if direction not in ("ASC", "DESC"):
            raise JiraCliError(
                f"排序方向只能是 asc 或 desc，收到：{direction}",
                "写法示例：--sort updated:desc",
            )
        self._order = f"ORDER BY {field.strip()} {direction}"
        return self

    # -- 产出 --------------------------------------------------------------
    def build(self) -> str:
        query = " AND ".join(self._clauses)
        if self._order:
            query = f"{query} {self._order}".strip()
        return query.strip()

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.build()
