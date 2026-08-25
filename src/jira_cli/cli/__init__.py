"""jira-cli 命令行入口。"""

from __future__ import annotations

import sys
from typing import Optional

import typer

from ..errors import JiraCliError
from ..output import emit, fail
from . import config_cmd, issue, log_cmd, meta
from .common import FORMAT_OPTION, make_ctx

app = typer.Typer(
    help="调用 Jira REST API 查询/创建/更新 issue、下载附件。输出对 AI 友好。",
    no_args_is_help=True,
    add_completion=False,
    # -h 与 --help 等价。只需在顶层声明：click 的子 Context 会从父 Context
    # 继承 help_option_names，各子命令组和子命令都跟着生效
    context_settings={"help_option_names": ["-h", "--help"]},
)

app.add_typer(issue.app, name="issue")
app.add_typer(meta.app, name="meta")
app.add_typer(config_cmd.app, name="config")
app.add_typer(log_cmd.app, name="log")


@app.callback()
def root(
    ctx: typer.Context,
    url: Optional[str] = typer.Option(None, "--url", help="Jira 地址，覆盖配置文件"),
    token: Optional[str] = typer.Option(None, "--token", help="PAT，覆盖配置文件"),
    insecure: Optional[bool] = typer.Option(None, "-k", "--insecure", help="跳过 TLS 校验"),
) -> None:
    """全局参数在子命令之前给出，如 jira-cli --url ... issue list。"""
    ctx.obj = make_ctx(url=url, token=token, insecure=insecure)


@app.command("commands")
def commands_cmd(fmt: str = FORMAT_OPTION) -> None:
    """列出全部子命令及简介。不确定有什么命令时先跑这个。"""
    rows: list[dict] = []

    def describe(callback, explicit_help: Optional[str]) -> str:
        text = explicit_help or (callback.__doc__ or "")
        return text.strip().split("\n")[0]

    def walk(instance: typer.Typer, prefix: str) -> None:
        # 走 Typer 自己的注册表，不依赖 click——typer 0.27 已把 click vendored
        for info in instance.registered_commands:
            name = info.name or (info.callback.__name__.replace("_", "-") if info.callback else "")
            rows.append(
                {
                    "command": f"jira-cli {prefix} {name}".replace("  ", " ").strip(),
                    "description": describe(info.callback, info.help) if info.callback else "",
                }
            )
        for group in instance.registered_groups:
            sub = group.typer_instance
            if sub is None:
                continue
            group_prefix = f"{prefix} {group.name or ''}".strip()
            # 用 callback 承载行为的组（如 log）自身就是一条命令
            if sub.registered_callback and sub.registered_callback.callback and not sub.registered_commands:
                rows.append(
                    {
                        "command": f"jira-cli {group_prefix}",
                        "description": describe(sub.registered_callback.callback, None),
                    }
                )
            walk(sub, group_prefix)

    walk(app, "")
    rows.sort(key=lambda r: r["command"])
    emit(rows, fmt)


def main() -> None:
    """入口。把本工具主动抛出的错误转成 stderr + 规范退出码。

    Ctrl-C 不在这里处理：click 在 standalone 模式下已经把 KeyboardInterrupt
    捕获成 Abort 并自行退出，外层这一层根本收不到。
    """
    try:
        app()
    except JiraCliError as exc:
        fail(exc.render())
        sys.exit(exc.exit_code)
