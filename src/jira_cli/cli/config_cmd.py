"""config 子命令：init / set / get。"""

from __future__ import annotations

import re
from typing import Optional

import typer

from ..client import ServerBackend, detect_deployment
from ..config import Config, load_file, normalize_key, save_file
from ..config import load as load_config
from ..errors import JiraCliError
from ..fields import resolve_one
from ..output import FORMATS, emit, note
from .common import FORMAT_OPTION, get_ctx

app = typer.Typer(help="配置连接参数", no_args_is_help=True)

#: 判定 wiki 渲染器用的「原始标记 -> 渲染后必然出现的 HTML 标签」
_RENDERER_PROBES = (
    (re.compile(r"(?<!\w)\*[^*\n]+\*(?!\w)"), re.compile(r"<b>|<strong>", re.I)),
    (re.compile(r"^h[1-6]\.\s", re.M), re.compile(r"<h[1-6]", re.I)),
    (re.compile(r"^\|.+\|\s*$", re.M), re.compile(r"<table", re.I)),
    (re.compile(r"\{code|\{noformat"), re.compile(r"<pre|class=.code", re.I)),
    (re.compile(r"^[*#]\s", re.M), re.compile(r"<ul|<ol", re.I)),
)


def detect_renderer(backend: ServerBackend, samples: int = 5) -> str:
    """探测 description 字段挂的是 wiki 还是纯文本渲染器。

    做法：取几条有描述的 issue，对比 fields.description（原始）与
    renderedFields.description（渲染后 HTML）。原始里有 wiki 标记、
    渲染后出现了对应的 HTML 标签，就是 wiki 渲染器。

    取多条样本是因为单条可能正好没有任何标记，判不出来。
    """
    try:
        found = backend.search(
            "description IS NOT EMPTY ORDER BY updated DESC",
            limit=samples,
            fields=["key"],
        )
    except JiraCliError:
        return "wiki"

    for issue in found.get("issues") or []:
        key = issue.get("key")
        if not key:
            continue
        try:
            detail = backend.get_issue(
                key, fields=["description"], expand=["renderedFields"]
            )
        except JiraCliError:
            continue
        raw = (detail.get("fields") or {}).get("description") or ""
        rendered = (detail.get("renderedFields") or {}).get("description") or ""
        if not raw or not rendered:
            continue
        for raw_pat, html_pat in _RENDERER_PROBES:
            if raw_pat.search(raw):
                # 原始有标记：渲染后出现对应标签 = wiki，否则 = 纯文本
                return "wiki" if html_pat.search(rendered) else "plain"
    # 样本里没有任何标记可供判别，按 Jira 出厂默认走
    return "wiki"


@app.command("init")
def init_cmd(
    url: Optional[str] = typer.Option(None, "--url", help="Jira 实例地址"),
    token: Optional[str] = typer.Option(None, "--token", help="Personal Access Token"),
    insecure: bool = typer.Option(False, "-k", "--insecure", help="跳过 TLS 校验"),
) -> None:
    """交互式引导：填连接信息，自动探测部署形态与渲染器，选默认项目。"""
    existing = load_file()

    url = url or typer.prompt("Jira 地址", default=str(existing.get("url") or "")).strip()
    if not url.startswith("http"):
        url = "https://" + url

    note("正在探测部署形态 ...")
    info = detect_deployment(url, insecure=insecure)
    deployment = (info.get("deploymentType") or "Server").lower()
    version = info.get("version", "?")
    note(f"检测到 Jira {info.get('deploymentType')} {version}")
    if deployment != "server":
        note("当前版本只实现了 Server；Cloud 仅做了架构预留，部分命令可能不可用。")

    if not token:
        note(f"PAT 创建入口：{url.rstrip('/')}/secure/ViewProfile.jspa →「Personal Access Tokens」")
        token = typer.prompt("Personal Access Token", hide_input=True).strip()

    config = Config(url=url, token=token, insecure=insecure, deployment="server")
    backend = ServerBackend(config)

    me = backend.myself()
    note(f"鉴权成功：{me.get('displayName')}（{me.get('name')}）")

    note("正在探测正文渲染器 ...")
    renderer = detect_renderer(backend)
    note(f"description 字段渲染器：{renderer}")

    projects = backend.projects()
    default_project = str(existing.get("default_project") or "")
    if projects:
        note(f"可访问项目 {len(projects)} 个：")
        note(f"  {'KEY':<16}名称")
        for item in projects[:40]:
            note(f"  {item.get('key'):<16}{item.get('name')}")
        # key 和名称都收，内部统一存 key——建单接口只认 key
        for _ in range(3):
            answer = typer.prompt(
                "默认项目（填 KEY 或名称均可，留空则每条命令都要显式指定 --project）",
                default=default_project,
                show_default=bool(default_project),
            ).strip()
            if not answer:
                default_project = ""
                break
            try:
                default_project = resolve_one(
                    projects, answer, "项目", keys=("key", "name")
                )["key"]
            except JiraCliError as exc:
                note(str(exc.message))
                continue
            if default_project != answer:
                note(f"「{answer}」已解析为项目 KEY：{default_project}")
            break
        else:
            raise JiraCliError("多次未能识别项目，请重新运行 jira-cli config init")

    path = save_file(
        {
            "url": url,
            "token": token,
            "auth_type": "bearer",
            "deployment": "server",
            "renderer": renderer,
            "default_project": default_project,
            "insecure": insecure,
        }
    )
    note(f"已写入 {path}（权限 600）")


@app.command("set")
def set_cmd(
    key: str = typer.Argument(..., help="配置项，如 url / token / default-project"),
    value: str = typer.Argument(..., help="取值"),
) -> None:
    """写入单个配置项。"""
    field = normalize_key(key)
    data = {k.replace("-", "_"): v for k, v in load_file().items()}
    data[field] = value
    path = save_file(data)
    note(f"已更新 {field} → {path}")


@app.command("get")
def get_cmd(
    ctx: typer.Context,
    fmt: str = FORMAT_OPTION,
) -> None:
    """查看当前生效的配置（token 脱敏）。"""
    config = get_ctx(ctx).config
    data = config.masked()
    if fmt == "table":
        emit([{"配置项": k.replace("_", "-"), "取值": v} for k, v in data.items()], fmt)
    else:
        emit(data, fmt)
