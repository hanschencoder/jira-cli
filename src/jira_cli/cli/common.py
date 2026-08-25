"""CLI 层共享上下文与通用选项。

本层不含业务逻辑，只做参数解析与调用编排。
"""

from __future__ import annotations

from typing import Any, Optional

import typer

from ..client import Backend, build_backend
from ..config import Config
from ..config import load as load_config
from ..errors import JiraCliError
from ..fields import build_field_map, reverse_field_map
from ..markup import Codec, get_codec
from ..meta_cache import cached

#: -o 的取值。声明在各子命令上，因此可以后置在命令末尾
FORMAT_OPTION = typer.Option(
    "table", "-o", "--output", help="输出格式：table（默认）/ yaml / json / md"
)


class Ctx:
    """一次调用的运行时上下文。backend / codec 都是懒加载。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._backend: Optional[Backend] = None
        self._codec: Optional[Codec] = None
        self._field_map: Optional[dict[str, str]] = None
        self._rev_field_map: Optional[dict[str, str]] = None

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = build_backend(self.config)
        return self._backend

    @property
    def codec(self) -> Codec:
        if self._codec is None:
            self._codec = get_codec(self.config.renderer)
        return self._codec

    def _fields(self, refresh: bool = False) -> list[dict]:
        return cached("fields", self.backend.fields, refresh=refresh)

    @property
    def field_map(self) -> dict[str, str]:
        """{customfield_id: 字段名}，用于展示时翻译。"""
        if self._field_map is None:
            self._field_map = build_field_map(self._fields())
        return self._field_map

    @property
    def rev_field_map(self) -> dict[str, str]:
        """{字段名小写: id}，用于 -f 按名称提交。"""
        if self._rev_field_map is None:
            self._rev_field_map = reverse_field_map(self._fields())
        return self._rev_field_map

    def project_or_default(self, project: Optional[str]) -> Optional[str]:
        return project or self.config.default_project or None

    def issue_url(self, key: str) -> str:
        return f"{self.config.base_url}/browse/{key}"


def get_ctx(ctx: typer.Context) -> Ctx:
    return ctx.obj  # type: ignore[return-value]


def make_ctx(
    url: Optional[str] = None,
    token: Optional[str] = None,
    insecure: Optional[bool] = None,
) -> Ctx:
    return Ctx(load_config(url=url, token=token, insecure=insecure))


def parse_field_args(pairs: list[str]) -> dict[str, str]:
    """解析 -f name=value，保持传入顺序。"""
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise JiraCliError(
                f"-f 的写法是 name=value，收到：{pair}",
                '示例：-f priority=High -f "严重程度=Major"',
            )
        name, _, value = pair.partition("=")
        out[name.strip()] = value
    return out
