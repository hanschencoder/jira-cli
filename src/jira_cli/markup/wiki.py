"""Wiki Style Renderer 对应的编解码器：Jira wiki markup <-> Markdown。

两个方向用不同手段，因为难度不对称：

读（wiki -> Markdown）是难的一半，要解析任意 wiki 语法（`*` 既可能是粗体
也可能是无序列表）。用 jira2markdown（pyparsing PEG 文法）完成，但必须先
做输入归一化，见 normalize_jira()。

写（Markdown -> wiki）是简单的一半，AI 生成的 Markdown 是可预期的子集。
关键是**不用正则替换**——正则会在嵌套列表、表格内联代码、元字符转义上翻车。
这里走 markdown-it-py 解析出语法树，再由 JiraWikiRenderer 遍历生成 wiki。
"""

from __future__ import annotations

import re

from jira2markdown import convert as _wiki_to_md
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from . import Codec

# {*}bold{*} 是 Jira 富文本编辑器产出的转义写法，等价于 *bold*。
# jira2markdown 0.5.1 不认，会原样吐出 {**}bold{**}。
_ESCAPED_MARKER_RE = re.compile(r"\{([*_\-+^~])\}")


def normalize_jira(text: str) -> str:
    """喂给 jira2markdown 之前的输入归一化。

    两处都是在真实实例（Jira Server 8.20.11）上实测踩到的坑：

    1. CRLF —— 该实例返回的正文是 \\r\\n。jira2markdown 的表格解析器不认 \\r，
       残留的 \\r 会变成幻影列，导致表格永不终止、把后续所有段落吞进最后一个
       单元格（数据损坏级）。
    2. {*} 转义写法 —— 见 _ESCAPED_MARKER_RE。
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ESCAPED_MARKER_RE.sub(r"\1", text)


# ---------------------------------------------------------------- Markdown -> wiki

#: 无条件转义：这些字符在 wiki 里含义明确，且在正常散文中罕见
_ALWAYS_ESCAPE = "{}[]|"
#: 条件转义：仅在「可能开启标记」的位置转义，避免把 snake_case、jira-cli
#: 这类正常文本转得满是反斜杠
_BOUNDARY_ESCAPE = "*_+^~-"


def escape_text(text: str) -> str:
    """转义正文中的 wiki 元字符，防止 AI 写的普通文本被误解析成标记。"""
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch in _ALWAYS_ESCAPE:
            out.append("\\" + ch)
        elif ch in _BOUNDARY_ESCAPE:
            prev = text[i - 1] if i else ""
            # 只有前面是空白或行首时才可能开启一段标记
            out.append("\\" + ch if (prev == "" or prev.isspace()) else ch)
        else:
            out.append(ch)
    result = "".join(out)
    # 行首的 # 会被当成有序列表，h1. 之类会被当成标题
    result = re.sub(r"^(#)", r"\\\1", result)
    result = re.sub(r"^(h[1-6]\.|bq\.)", r"\\\1", result)
    return result


class JiraWikiRenderer:
    """遍历 Markdown 语法树，生成 Jira wiki markup。"""

    def render(self, root: SyntaxTreeNode) -> str:
        body = self._children(root, sep="\n\n")
        return re.sub(r"\n{3,}", "\n\n", body).strip()

    # -- 调度 --------------------------------------------------------------
    def _node(self, node: SyntaxTreeNode) -> str:
        handler = getattr(self, f"_r_{node.type}", None)
        if handler is None:
            # 未知节点不丢内容：退回渲染子节点
            return self._children(node)
        return handler(node)

    def _children(self, node: SyntaxTreeNode, sep: str = "") -> str:
        return sep.join(self._node(child) for child in node.children)

    # -- 块级 --------------------------------------------------------------
    def _r_paragraph(self, node: SyntaxTreeNode) -> str:
        return self._children(node)

    def _r_heading(self, node: SyntaxTreeNode) -> str:
        level = int(node.tag[1])
        return f"h{level}. {self._children(node)}"

    def _r_hr(self, node: SyntaxTreeNode) -> str:
        return "----"

    def _r_fence(self, node: SyntaxTreeNode) -> str:
        lang = (node.info or "").strip().split()[0] if (node.info or "").strip() else ""
        content = node.content.rstrip("\n")
        if lang:
            return f"{{code:{lang}}}\n{content}\n{{code}}"
        return f"{{noformat}}\n{content}\n{{noformat}}"

    _r_code_block = _r_fence

    def _r_blockquote(self, node: SyntaxTreeNode) -> str:
        inner = self._children(node, sep="\n\n")
        if "\n" in inner:
            return f"{{quote}}\n{inner}\n{{quote}}"
        return f"bq. {inner}"

    def _r_bullet_list(self, node: SyntaxTreeNode) -> str:
        return self._list(node, "*")

    def _r_ordered_list(self, node: SyntaxTreeNode) -> str:
        return self._list(node, "#")

    def _list(self, node: SyntaxTreeNode, marker: str) -> str:
        prefix = self._marker_prefix(node, marker)
        lines: list[str] = []
        for item in node.children:
            inline_parts: list[str] = []
            nested: list[str] = []
            for child in item.children:
                if child.type in ("bullet_list", "ordered_list"):
                    nested.append(self._node(child))
                else:
                    inline_parts.append(self._node(child))
            lines.append(f"{prefix} {' '.join(p for p in inline_parts if p)}".rstrip())
            lines.extend(nested)
        return "\n".join(lines)

    @staticmethod
    def _marker_prefix(node: SyntaxTreeNode, marker: str) -> str:
        """列表项前缀。

        wiki 用标记序列表示层级，且**序列要反映祖先链的类型**，不是重复当前标记：
        有序列表里嵌无序列表是 `#*`，不是 `**`。
        """
        chain = [marker]
        parent = node.parent
        while parent is not None:
            if parent.type == "bullet_list":
                chain.append("*")
            elif parent.type == "ordered_list":
                chain.append("#")
            parent = parent.parent
        return "".join(reversed(chain))

    def _r_table(self, node: SyntaxTreeNode) -> str:
        lines: list[str] = []
        for section in node.children:
            for row in section.children:
                cells = [self._children(cell).replace("\n", " ") for cell in row.children]
                if section.type == "thead":
                    lines.append("||" + "||".join(cells) + "||")
                else:
                    lines.append("|" + "|".join(c if c else " " for c in cells) + "|")
        return "\n".join(lines)

    # -- 行内 --------------------------------------------------------------
    def _r_inline(self, node: SyntaxTreeNode) -> str:
        return self._children(node)

    def _r_text(self, node: SyntaxTreeNode) -> str:
        return escape_text(node.content)

    def _r_strong(self, node: SyntaxTreeNode) -> str:
        return f"*{self._children(node)}*"

    def _r_em(self, node: SyntaxTreeNode) -> str:
        return f"_{self._children(node)}_"

    def _r_s(self, node: SyntaxTreeNode) -> str:
        return f"-{self._children(node)}-"

    def _r_code_inline(self, node: SyntaxTreeNode) -> str:
        return "{{" + node.content + "}}"

    def _r_link(self, node: SyntaxTreeNode) -> str:
        href = node.attrGet("href") or ""
        label = self._children(node)
        if not label or label == href:
            return f"[{href}]"
        return f"[{label}|{href}]"

    def _r_image(self, node: SyntaxTreeNode) -> str:
        src = node.attrGet("src") or ""
        return f"!{src}!"

    def _r_softbreak(self, node: SyntaxTreeNode) -> str:
        return "\n"

    def _r_hardbreak(self, node: SyntaxTreeNode) -> str:
        return "\n"

    def _r_html_block(self, node: SyntaxTreeNode) -> str:
        return node.content.rstrip("\n")

    def _r_html_inline(self, node: SyntaxTreeNode) -> str:
        return node.content


def _parser() -> MarkdownIt:
    # gfm-like 带表格与删除线；linkify 关掉，避免把裸 URL 悄悄改写成链接
    md = MarkdownIt("gfm-like")
    md.options["linkify"] = False
    return md


class WikiCodec(Codec):
    name = "wiki"

    def from_jira(self, text: str) -> str:
        if not text:
            return ""
        return _wiki_to_md(normalize_jira(text))

    def to_jira(self, markdown: str) -> str:
        if not markdown:
            return ""
        text = markdown.replace("\r\n", "\n").replace("\r", "\n")
        tree = SyntaxTreeNode(_parser().parse(text))
        return JiraWikiRenderer().render(tree)
