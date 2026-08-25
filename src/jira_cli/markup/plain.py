"""Default Text Renderer 对应的编解码器：不做任何转换。

字段挂的是纯文本渲染器时，任何标记都不会被解析，转换只会帮倒忙。
"""

from __future__ import annotations

from . import Codec


class PlainCodec(Codec):
    name = "plain"

    def from_jira(self, text: str) -> str:
        return (text or "").replace("\r\n", "\n").replace("\r", "\n")

    def to_jira(self, markdown: str) -> str:
        return markdown or ""
