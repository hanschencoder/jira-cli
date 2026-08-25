"""meta 子命令：查 id / 可选值 / 可用流转，带本地缓存。"""

from __future__ import annotations

from typing import Any, Optional

import typer

from ..fields import prune
from ..meta_cache import cached, clear
from ..output import emit, note
from .common import FORMAT_OPTION, check_limit, get_ctx

app = typer.Typer(help="查询元数据（项目/类型/状态/字段/流转）", no_args_is_help=True)


def _rows(items: list[dict], *keys: str) -> list[dict]:
    return [{k: item.get(k) for k in keys if item.get(k) is not None} for item in items]


@app.command("projects")
def projects_cmd(ctx: typer.Context, fmt: str = FORMAT_OPTION, refresh: bool = typer.Option(False, "--refresh")) -> None:
    """列出可访问的项目。"""
    c = get_ctx(ctx)
    data = cached("projects", c.backend.projects, refresh=refresh)
    emit(_rows(data, "key", "name", "id"), fmt)


@app.command("issuetypes")
def issuetypes_cmd(ctx: typer.Context, fmt: str = FORMAT_OPTION, refresh: bool = typer.Option(False, "--refresh")) -> None:
    """列出 issue 类型。"""
    c = get_ctx(ctx)
    data = cached("issuetypes", c.backend.issue_types, refresh=refresh)
    seen: dict[str, dict] = {}
    for item in data:
        seen.setdefault(item.get("name") or "", item)
    emit(_rows(list(seen.values()), "id", "name", "description"), fmt)


@app.command("statuses")
def statuses_cmd(ctx: typer.Context, fmt: str = FORMAT_OPTION, refresh: bool = typer.Option(False, "--refresh")) -> None:
    """列出全部状态。注意：能否流转到某状态由工作流决定，见 meta transitions。"""
    c = get_ctx(ctx)
    data = cached("statuses", c.backend.statuses, refresh=refresh)
    emit(_rows(data, "id", "name"), fmt)


@app.command("priorities")
def priorities_cmd(ctx: typer.Context, fmt: str = FORMAT_OPTION, refresh: bool = typer.Option(False, "--refresh")) -> None:
    """列出优先级。"""
    c = get_ctx(ctx)
    data = cached("priorities", c.backend.priorities, refresh=refresh)
    emit(_rows(data, "id", "name"), fmt)


@app.command("fields")
def fields_cmd(
    ctx: typer.Context,
    keyword: Optional[str] = typer.Argument(None, help="按名称模糊过滤"),
    fmt: str = FORMAT_OPTION,
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """列出字段定义（含自定义字段 id）。"""
    c = get_ctx(ctx)
    data = cached("fields", c.backend.fields, refresh=refresh)
    if keyword:
        needle = keyword.lower()
        data = [f for f in data if needle in (f.get("name") or "").lower() or needle in f.get("id", "")]
    emit(_rows(data, "id", "name", "custom"), fmt)


@app.command("users")
def users_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="姓名 / 登录名 关键词"),
    limit: int = typer.Option(20, "-n", "--limit"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """搜索用户。assignee 要填的是这里的 name（登录名）。"""
    check_limit(limit)
    c = get_ctx(ctx)
    data = c.backend.search_users(query, limit)
    emit(
        [
            {"name": u.get("name"), "displayName": u.get("displayName"), "email": u.get("emailAddress")}
            for u in data
        ],
        fmt,
    )


@app.command("whoami")
def whoami_cmd(ctx: typer.Context, fmt: str = FORMAT_OPTION) -> None:
    """当前 token 对应的用户。"""
    c = get_ctx(ctx)
    me = c.backend.myself()
    emit(
        {
            "name": me.get("name"),
            "displayName": me.get("displayName"),
            "email": me.get("emailAddress"),
            "active": me.get("active"),
            "timeZone": me.get("timeZone"),
        },
        fmt,
    )


def transition_rows(transitions: list[dict]) -> list[dict]:
    """把 transitions 展平成「名称 / id / 目标状态 / 必填字段」。

    必填字段带上 allowedValues，是为了让流转失败时 AI 能一轮补齐，
    不必再发一次探查请求。
    """
    rows = []
    for tr in transitions:
        required = []
        for fid, meta in (tr.get("fields") or {}).items():
            if not meta.get("required"):
                continue
            allowed = [
                v.get("name") or v.get("value") or v.get("id")
                for v in (meta.get("allowedValues") or [])
            ]
            item: dict[str, Any] = {"field": fid, "name": meta.get("name")}
            if allowed:
                item["allowed"] = [a for a in allowed if a]
            required.append(item)
        rows.append(
            prune(
                {
                    "id": tr.get("id"),
                    "name": tr.get("name"),
                    "to": (tr.get("to") or {}).get("name"),
                    "required_fields": required,
                }
            )
        )
    return rows


@app.command("transitions")
def transitions_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key，如 ABC-123"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """列出该 issue 当前可用的流转及各自的必填字段。"""
    c = get_ctx(ctx)
    rows = transition_rows(c.backend.transitions(key))
    if fmt == "table":
        emit(
            [
                {
                    "name": r.get("name"),
                    "id": r.get("id"),
                    "to": r.get("to"),
                    "required": ", ".join(f.get("name") or f.get("field") for f in r.get("required_fields", [])),
                }
                for r in rows
            ],
            fmt,
        )
    else:
        emit({"issue": key, "transitions": rows}, fmt)


@app.command("createmeta")
def createmeta_cmd(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(None, "--project", "-p", help="项目 key"),
    issue_type: Optional[str] = typer.Option(None, "--type", "-t", help="issue 类型名"),
    required_only: bool = typer.Option(True, "--required-only/--all", help="只列必填字段"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """建单时可填/必填的字段及其可选值。建 issue 前先查这个。

    --project 填项目 key 或名称都行，内部统一解析成 key——createmeta
    只认 key，拿到名称会静默返回空列表。
    """
    c = get_ctx(ctx)
    project = c.project_or_default(project)
    if not project:
        raise typer.BadParameter("需要 --project（或先设 default-project）")

    meta = c.backend.create_meta(project, issue_type)
    out = []
    for proj in meta.get("projects") or []:
        for itype in proj.get("issuetypes") or []:
            fields = []
            for fid, fmeta in (itype.get("fields") or {}).items():
                if required_only and not fmeta.get("required"):
                    continue
                allowed = [
                    v.get("name") or v.get("value") or v.get("id")
                    for v in (fmeta.get("allowedValues") or [])
                ]
                fields.append(
                    prune(
                        {
                            "field": fid,
                            "name": fmeta.get("name"),
                            "required": fmeta.get("required"),
                            "type": (fmeta.get("schema") or {}).get("type"),
                            "allowed": [a for a in allowed if a] or None,
                        }
                    )
                )
            out.append({"type": itype.get("name"), "fields": fields})
    emit({"project": project, "issuetypes": out}, fmt if fmt != "table" else "yaml")


@app.command("update")
def update_cmd(ctx: typer.Context) -> None:
    """清空本地元数据缓存，下次调用重新拉取。"""
    removed = clear()
    note(f"已清空 {removed} 个缓存文件")
