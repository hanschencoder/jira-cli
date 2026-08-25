"""正文格式转换。

对外约定：AI 读到的和写入的都是 Markdown，wiki markup 只存在于本包内部。

Codec 是 Cloud 预留的插拔点之一（另一个是 client.Backend）：
Server 的正文是 wiki markup 字符串，Cloud 是 ADF（JSON）。
将来加 Cloud 只需新增 AdfCodec，不动其余代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Codec(ABC):
    """正文编解码器。"""

    name: str

    @abstractmethod
    def from_jira(self, text: str) -> str:
        """Jira 原生正文 -> Markdown（供 AI 阅读）。"""

    @abstractmethod
    def to_jira(self, markdown: str) -> str:
        """Markdown -> Jira 原生正文（供提交）。"""


def get_codec(renderer: str) -> Codec:
    """按配置里的 renderer 取编解码器。

    renderer 由 config init 探测得到：
      wiki  —— Wiki Style Renderer（Jira 出厂默认）
      plain —— Default Text Renderer，标记不解析，原样透传
    """
    from .plain import PlainCodec
    from .wiki import WikiCodec

    if (renderer or "").lower() == "plain":
        return PlainCodec()
    return WikiCodec()
