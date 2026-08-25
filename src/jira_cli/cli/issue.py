"""issue 子命令：查询、详情、创建、更新、评论、流转、附件。

写操作一律**单个 issue key**，不支持批量；也不提供 dry-run。
护栏只有写操作留痕（writelog）。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Optional

import typer

from ..errors import JiraCliError, TransitionError
from ..fields import (
    LIST_FIELDS,
    SHOW_FIELDS,
    attachment_row,
    build_schema_map,
    changelog_rows,
    coerce_value,
    comment_row,
    detail_issue,
    issue_links,
    summarize_issue,
)
from ..jql import JQL, normalize_user
from ..meta_cache import cached
from ..output import emit, note
from .. import writelog
from .common import FORMAT_OPTION, get_ctx, parse_field_args
from .meta import transition_rows

app = typer.Typer(help="查询与操作 issue", no_args_is_help=True)

#: issue list 表格视图的列，控制在一屏内
TABLE_COLUMNS = ("key", "type", "status", "priority", "assignee", "updated", "summary")


def _resolve_fields(c: Any, pairs: list[str]) -> dict[str, Any]:
    """-f name=value -> Jira 的 fields 结构。

    name 可以是字段 id（customfield_10001 / priority），也可以是字段显示名
    （如「严重程度」）。显示名走 /field 的反查表。
    """
    raw = parse_field_args(pairs)
    if not raw:
        return {}
    schemas = build_schema_map(cached("fields", c.backend.fields))
    rev = c.rev_field_map
    out: dict[str, Any] = {}
    for name, value in raw.items():
        field_id = name if name in schemas else rev.get(name.strip().lower(), name)
        out[field_id] = coerce_value(field_id, value, schemas.get(field_id))
    return out


def _attach(c: Any, key: str, files: list[Path]) -> list[str]:
    if not files:
        return []
    missing = [str(p) for p in files if not p.exists()]
    if missing:
        raise JiraCliError(f"附件文件不存在：{', '.join(missing)}")
    uploaded = c.backend.upload_attachments(key, files)
    return [a.get("filename") for a in uploaded]


# ------------------------------------------------------------------ 查询

@app.command("list")
def list_cmd(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(None, "--project", "-p", help="项目 key，默认取 default-project"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="经办人登录名，me 表示自己"),
    reporter: Optional[str] = typer.Option(None, "--reporter", help="报告人，me 表示自己"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="状态名；open/closed 是简写，* 表示不过滤"),
    issue_type: Optional[str] = typer.Option(None, "--type", "-t", help="issue 类型"),
    priority: Optional[str] = typer.Option(None, "--priority", help="优先级"),
    labels: Optional[list[str]] = typer.Option(None, "--label", "-l", help="标签，可多次传"),
    summary: Optional[str] = typer.Option(None, "--summary", help="标题包含关键词"),
    created: Optional[str] = typer.Option(None, "--created", help="创建时间，如 '>=-7d' 或 '2026-05-01|2026-05-31'"),
    updated: Optional[str] = typer.Option(None, "--updated", help="更新时间，写法同 --created"),
    jql: Optional[str] = typer.Option(None, "--jql", help="原始 JQL，与上面的参数 AND 合并"),
    sort: str = typer.Option("updated:desc", "--sort", help="排序，如 updated:desc"),
    limit: int = typer.Option(50, "-n", "--limit", help="最多返回条数"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """按条件查询 issue。封装参数与 --jql 可同时使用。"""
    c = get_ctx(ctx)
    query = (
        JQL()
        .filter_by("project", c.project_or_default(project))
        .filter_by("assignee", normalize_user(assignee) if assignee else None)
        .filter_by("reporter", normalize_user(reporter) if reporter else None)
        .status(status)
        .filter_by("issuetype", issue_type)
        .filter_by("priority", priority)
        .in_("labels", list(labels) if labels else None)
        .contains("summary", summary)
        .date("created", created)
        .date("updated", updated)
        .raw(jql)
        .order_by(sort)
        .build()
    )
    if not query.strip() or query.strip().startswith("ORDER BY"):
        raise JiraCliError(
            "查询条件为空，会扫描全站。",
            "至少给一个条件，如 --project ABC 或 --assignee me；"
            "确实要全站查请用 --jql 显式表达。",
        )

    result = c.backend.search(query, limit=limit, fields=list(LIST_FIELDS))
    rows = [summarize_issue(i) for i in result["issues"]]
    payload = {
        "jql": query,
        "returned": len(rows),
        "total_matched": result["total"],
        "issues": rows,
    }
    if len(rows) < result["total"]:
        note(f"匹配 {result['total']} 条，本次返回 {len(rows)} 条（用 -n 调大上限）")
    emit(payload, fmt, rows=rows, columns=TABLE_COLUMNS)


@app.command("show")
def show_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key，如 ABC-123"),
    comments: bool = typer.Option(False, "--comments", help="附上评论"),
    history: bool = typer.Option(False, "--history", help="附上变更历史"),
    links: bool = typer.Option(False, "--links", help="附上关联 issue"),
    subtasks: bool = typer.Option(False, "--subtasks", help="附上子任务"),
    only: Optional[str] = typer.Option(None, "--fields", help="只要这些字段，逗号分隔"),
    raw: bool = typer.Option(False, "--raw", help="原始 JSON 逃生舱，不做任何裁剪"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """查看 issue 详情。默认精简，其余按需叠加。"""
    c = get_ctx(ctx)

    if raw:
        emit(c.backend.get_issue(key, expand=["changelog", "renderedFields"]), fmt if fmt != "table" else "yaml")
        return

    expand = ["changelog"] if history else None
    wanted = list(SHOW_FIELDS)
    if only:
        wanted = [f.strip() for f in only.split(",") if f.strip()]
    detail_raw = c.backend.get_issue(key, fields=wanted + ["*navigable"] if only else None, expand=expand)

    data = detail_issue(detail_raw, c.codec, c.field_map)
    data["url"] = c.issue_url(key)

    if only:
        keep = {f.strip() for f in only.split(",")} | {"key", "url"}
        data = {k: v for k, v in data.items() if k in keep}

    if links:
        rows = issue_links(detail_raw.get("fields") or {})
        if rows:
            data["links"] = rows
    if not subtasks:
        data.pop("subtasks", None)
    if comments:
        data["comments"] = [comment_row(cm, c.codec) for cm in c.backend.comments(key)]
    if history:
        data["history"] = changelog_rows(detail_raw)

    emit(data, fmt if fmt != "table" else "yaml")


@app.command("comments")
def comments_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """列出 issue 的全部评论（正文转成 Markdown）。"""
    c = get_ctx(ctx)
    rows = [comment_row(cm, c.codec) for cm in c.backend.comments(key)]
    emit({"issue": key, "total": len(rows), "comments": rows}, fmt if fmt != "table" else "yaml")


# ------------------------------------------------------------------ 写

@app.command("create")
def create_cmd(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(None, "--project", "-p", help="项目 key"),
    issue_type: str = typer.Option(..., "--type", "-t", help="issue 类型，如 任务 / Bug"),
    summary: str = typer.Option(..., "--summary", "-s", help="标题"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="描述，写 Markdown"),
    description_raw: Optional[str] = typer.Option(None, "--description-raw", help="描述，直接传 wiki 原文（绕过转换器）"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="经办人登录名"),
    priority: Optional[str] = typer.Option(None, "--priority", help="优先级"),
    labels: Optional[list[str]] = typer.Option(None, "--label", "-l", help="标签，可多次传"),
    field: Optional[list[str]] = typer.Option(None, "-f", "--field", help="其它字段 name=value，可多次传"),
    attach: Optional[list[Path]] = typer.Option(None, "--attach", help="附件文件路径，可多次传"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """创建 issue。描述写 Markdown，工具自动转成 wiki markup。"""
    c = get_ctx(ctx)
    project = c.project_or_default(project)
    if not project:
        raise typer.BadParameter("需要 --project（或先设 default-project）")

    payload_fields: dict[str, Any] = {
        "project": {"key": project},
        "issuetype": {"name": issue_type},
        "summary": summary,
    }
    if description_raw is not None:
        payload_fields["description"] = description_raw
    elif description:
        payload_fields["description"] = c.codec.to_jira(description)
    if assignee:
        payload_fields["assignee"] = {"name": assignee}
    if priority:
        payload_fields["priority"] = {"name": priority}
    if labels:
        payload_fields["labels"] = list(labels)
    payload_fields.update(_resolve_fields(c, list(field or [])))

    payload = {"fields": payload_fields}
    try:
        created = c.backend.create_issue(payload)
    except JiraCliError as exc:
        writelog.record("create", project, payload, ok=False)
        exc.hint = (exc.hint + "\n\n" if exc.hint else "") + (
            f"必填字段与可选值可用这条命令查：\n"
            f"  jira-cli meta createmeta --project {project} --type {issue_type!r} -o yaml"
        )
        raise

    key = created.get("key", "")
    attached = _attach(c, key, list(attach or [])) if attach else []
    writelog.record("create", key, payload, ok=True, result={"key": key})

    emit({"key": key, "url": c.issue_url(key), "attached": attached or None}, fmt if fmt != "table" else "yaml")


@app.command("update")
def update_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key（单个，不支持批量）"),
    summary: Optional[str] = typer.Option(None, "--summary", "-s", help="改标题"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="改描述，写 Markdown"),
    description_raw: Optional[str] = typer.Option(None, "--description-raw", help="改描述，直接传 wiki 原文"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="改经办人登录名"),
    priority: Optional[str] = typer.Option(None, "--priority", help="改优先级"),
    labels: Optional[list[str]] = typer.Option(None, "--label", "-l", help="覆盖标签，可多次传"),
    field: Optional[list[str]] = typer.Option(None, "-f", "--field", help="其它字段 name=value"),
    attach: Optional[list[Path]] = typer.Option(None, "--attach", help="追加附件"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """更新单个 issue 的字段。"""
    c = get_ctx(ctx)
    payload_fields: dict[str, Any] = {}
    if summary is not None:
        payload_fields["summary"] = summary
    if description_raw is not None:
        payload_fields["description"] = description_raw
    elif description is not None:
        payload_fields["description"] = c.codec.to_jira(description)
    if assignee:
        payload_fields["assignee"] = {"name": assignee}
    if priority:
        payload_fields["priority"] = {"name": priority}
    if labels:
        payload_fields["labels"] = list(labels)
    payload_fields.update(_resolve_fields(c, list(field or [])))

    attached: list[str] = []
    if payload_fields:
        payload = {"fields": payload_fields}
        try:
            c.backend.update_issue(key, payload)
        except JiraCliError:
            writelog.record("update", key, payload, ok=False)
            raise
        writelog.record("update", key, payload, ok=True)
    elif not attach:
        raise JiraCliError("没有要更新的内容", "至少给一个 --summary / --assignee / -f / --attach。")

    if attach:
        attached = _attach(c, key, list(attach))
        writelog.record("attach", key, {"files": [str(p) for p in attach]}, ok=True)

    emit(
        {"key": key, "updated": sorted(payload_fields), "attached": attached or None, "url": c.issue_url(key)},
        fmt if fmt != "table" else "yaml",
    )


@app.command("comment")
def comment_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key"),
    body: str = typer.Argument(..., help="评论正文，写 Markdown"),
    raw: bool = typer.Option(False, "--raw", help="直接传 wiki 原文（绕过转换器）"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """给 issue 添加评论。"""
    c = get_ctx(ctx)
    text = body if raw else c.codec.to_jira(body)
    try:
        created = c.backend.add_comment(key, text)
    except JiraCliError:
        writelog.record("comment", key, {"body": text}, ok=False)
        raise
    writelog.record("comment", key, {"body": text}, ok=True, result={"id": created.get("id")})
    emit(
        {"key": key, "comment_id": created.get("id"), "url": c.issue_url(key)},
        fmt if fmt != "table" else "yaml",
    )


@app.command("transition")
def transition_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key"),
    name: str = typer.Argument(..., help="目标流转名称，如「完成」"),
    field: Optional[list[str]] = typer.Option(None, "-f", "--field", help="流转必填字段 name=value"),
    comment: Optional[str] = typer.Option(None, "--comment", help="流转的同时加一条评论"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """流转 issue 状态。

    Jira 不能直接「设置状态」，必须走工作流定义的 transition。
    """
    c = get_ctx(ctx)
    available = c.backend.transitions(key)
    if not available:
        raise TransitionError(
            f"{key} 当前没有任何可用流转",
            "可能是权限不足，或该 issue 已处于工作流终态。",
        )

    target = _match_transition(available, name, key)

    payload: dict[str, Any] = {"transition": {"id": target["id"]}}
    extra = _resolve_fields(c, list(field or []))
    if extra:
        payload["fields"] = extra
    if comment:
        payload["update"] = {"comment": [{"add": {"body": c.codec.to_jira(comment)}}]}

    try:
        c.backend.do_transition(key, payload)
    except JiraCliError as exc:
        writelog.record("transition", key, payload, ok=False)
        exc.hint = (exc.hint + "\n\n" if exc.hint else "") + _transition_hint(available)
        raise
    writelog.record("transition", key, payload, ok=True, result={"to": target.get("to", {}).get("name")})

    emit(
        {
            "key": key,
            "transition": target.get("name"),
            "status": (target.get("to") or {}).get("name"),
            "url": c.issue_url(key),
        },
        fmt if fmt != "table" else "yaml",
    )


def _match_transition(available: list[dict], name: str, key: str) -> dict:
    """按名称匹配 transition：先精确（流转名或目标状态名），再唯一子串。"""
    needle = name.strip().lower()
    for tr in available:
        if str(tr.get("id")) == name.strip():
            return tr
        if (tr.get("name") or "").lower() == needle:
            return tr
        if ((tr.get("to") or {}).get("name") or "").lower() == needle:
            return tr
    partial = [
        tr
        for tr in available
        if needle in (tr.get("name") or "").lower()
        or needle in ((tr.get("to") or {}).get("name") or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]

    reason = "匹配到多个" if len(partial) > 1 else "没有匹配的流转"
    raise TransitionError(f"{key} 无法流转到「{name}」：{reason}", _transition_hint(available))


def _transition_hint(available: list[dict]) -> str:
    """把可用流转和必填字段摊开，让 AI 一轮就能自我纠正。"""
    lines = ["当前可用的流转："]
    for row in transition_rows(available):
        line = f"  · {row.get('name')} → {row.get('to')}（id={row.get('id')}）"
        required = row.get("required_fields") or []
        if required:
            parts = []
            for item in required:
                label = item.get("name") or item.get("field")
                allowed = item.get("allowed")
                parts.append(f"{item.get('field')}（{label}{'，可选：' + '/'.join(map(str, allowed)) if allowed else ''}）")
            line += "\n      必填：" + "；".join(parts)
        lines.append(line)
    lines.append("\n补必填字段的写法：-f <field>=<值>")
    return "\n".join(lines)


# ------------------------------------------------------------------ 附件

@app.command("attachments")
def attachments_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """列出 issue 的附件清单。下载用 issue download。"""
    c = get_ctx(ctx)
    raw = c.backend.get_issue(key, fields=["attachment"])
    rows = [attachment_row(a) for a in ((raw.get("fields") or {}).get("attachment") or [])]
    emit({"issue": key, "total": len(rows), "attachments": rows}, fmt, rows=rows)


@app.command("download")
def download_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="issue key"),
    ids: Optional[list[str]] = typer.Option(None, "--id", help="只下载指定附件 id，可多次传"),
    match: Optional[str] = typer.Option(None, "--match", help="按文件名 glob 过滤，如 '*.log'"),
    directory: Optional[Path] = typer.Option(None, "--dir", help="下载目录，默认 ./jira-attachments/<KEY>/"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """下载附件到本地，输出本地绝对路径清单。

    Jira 的附件链接必须带认证头才能取，所以只能落盘，不能把裸链接交给调用方。
    """
    c = get_ctx(ctx)
    raw = c.backend.get_issue(key, fields=["attachment"])
    attachments = (raw.get("fields") or {}).get("attachment") or []

    if ids:
        wanted = {str(i) for i in ids}
        attachments = [a for a in attachments if str(a.get("id")) in wanted]
    if match:
        attachments = [a for a in attachments if fnmatch.fnmatch(a.get("filename") or "", match)]

    if not attachments:
        emit({"issue": key, "downloaded": 0, "files": []}, fmt if fmt != "table" else "yaml")
        note("没有匹配的附件")
        return

    target_dir = (directory or Path("jira-attachments") / key).resolve()
    files = []
    for att in attachments:
        dest = target_dir / (att.get("filename") or f"attachment-{att.get('id')}")
        size = c.backend.download_attachment(att["content"], dest)
        files.append(
            {
                "id": att.get("id"),
                "filename": att.get("filename"),
                "path": str(dest),
                "size": size,
                "mime": att.get("mimeType"),
            }
        )

    emit(
        {"issue": key, "dir": str(target_dir), "downloaded": len(files), "files": files},
        fmt,
        rows=files,
    )
