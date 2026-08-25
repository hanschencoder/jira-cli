"""issue 子命令：查询、详情、创建、更新、评论、流转、附件。

写操作一律**单个 issue key**，不支持批量；也不提供 dry-run。
护栏只有写操作留痕（writelog）。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import typer

from ..config import default_download_dir
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
    prune,
    summarize_issue,
    to_jira_field,
)
from ..jql import JQL, normalize_user
from ..meta_cache import cached
from ..output import emit, note
from .. import writelog
from .common import FORMAT_OPTION, check_limit, get_ctx, parse_field_args
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
    sort: Optional[str] = typer.Option(None, "--sort", help="排序，如 updated:desc。不给且 --jql 里也没 ORDER BY 时默认 updated:desc"),
    limit: int = typer.Option(50, "-n", "--limit", help="最多返回条数"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """按条件查询 issue。封装参数与 --jql 可同时使用。"""
    check_limit(limit)
    c = get_ctx(ctx)
    builder = (
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
    )
    # --sort 显式给了就用它；否则保留 --jql 自带的 ORDER BY；都没有才兜底
    if sort:
        builder.order_by(sort)
    elif not builder.has_order():
        builder.order_by("updated:desc")
    query = builder.build()
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
    custom: bool = typer.Option(False, "--custom", help="附上自定义字段（多数是未填写的模板默认值，很占 token）"),
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
    if only:
        # --fields 收的是输出里的字段名，发给服务端前要翻回 Jira 的叫法
        wanted = [to_jira_field(f.strip()) for f in only.split(",") if f.strip()]
    elif custom:
        # 自定义字段没法按名字点名要，只能全量拉回来再筛
        wanted = None
    else:
        wanted = list(SHOW_FIELDS)
    detail_raw = c.backend.get_issue(key, fields=wanted, expand=expand)

    data = detail_issue(detail_raw, c.codec, c.field_map, with_custom=custom)
    data["url"] = c.issue_url(key)

    if only:
        keep = {f.strip() for f in only.split(",")} | {"key", "url"}
        data = {k: v for k, v in data.items() if k in keep}
        # --fields 限定了服务端返回的字段，这几个开关就没有数据可依附了。
        # 静默忽略会让人以为「这条 issue 没有子任务/关联」
        ignored = [
            flag
            for flag, on in (("--custom", custom), ("--links", links), ("--subtasks", subtasks))
            if on
        ]
        if ignored:
            note(f"--fields 已限定返回字段，{' / '.join(ignored)} 本次不生效")

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

    # 建单一成功就留痕，再去传附件。反过来的话，附件上传失败会让整条
    # create 完全没有记录——issue 已经真建出来了，唯一的护栏里却查不到
    key = created.get("key", "")
    writelog.record("create", key, payload, ok=True, result={"key": key})
    attached = _attach(c, key, list(attach or [])) if attach else []

    emit(prune({"key": key, "url": c.issue_url(key), "attached": attached or None}), fmt if fmt != "table" else "yaml")


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
        prune(
            {
                "key": key,
                "updated": sorted(payload_fields) or None,
                "attached": attached or None,
                "url": c.issue_url(key),
            }
        ),
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
        prune({"key": key, "comment_id": created.get("id"), "url": c.issue_url(key)}),
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

    try:
        c.backend.do_transition(key, payload)
    except JiraCliError as exc:
        writelog.record("transition", key, payload, ok=False)
        exc.hint = (exc.hint + "\n\n" if exc.hint else "") + _transition_hint(available)
        raise
    writelog.record("transition", key, payload, ok=True, result={"to": target.get("to", {}).get("name")})

    # 评论单独发一次请求，不塞进 transition 的 update.comment。
    # 实测：transition 界面若没配「评论」字段，Jira 会静默丢弃 update.comment
    # ——返回成功但评论根本没写入。静默失败对调用方最致命，宁可多一次请求。
    comment_id = None
    if comment:
        body = c.codec.to_jira(comment)
        created = c.backend.add_comment(key, body)
        comment_id = created.get("id")
        writelog.record("comment", key, {"body": body}, ok=True, result={"id": comment_id})

    emit(
        prune(
            {
                "key": key,
                "transition": target.get("name"),
                "status": (target.get("to") or {}).get("name"),
                "comment_id": comment_id,
                "url": c.issue_url(key),
            }
        ),
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
    directory: Optional[Path] = typer.Option(None, "--dir", help="下载目录，默认 <缓存目录>/attachments/<KEY>/"),
    force: bool = typer.Option(False, "--force", help="忽略本地缓存，强制重新下载"),
    yes: bool = typer.Option(False, "-y", "--yes", help="确认下载超过体积上限的附件"),
    fmt: str = FORMAT_OPTION,
) -> None:
    """下载附件到本地，输出本地绝对路径清单。

    Jira 的附件链接必须带认证头才能取，所以只能落盘，不能把裸链接交给调用方。

    已经下过的文件（同路径且大小一致）默认跳过，输出里标 `cached: true`；
    要重下加 --force。附件在 Jira 里是不可变的（改内容只能删了重传，会得到
    新 id），所以「路径存在 + 大小一致」足以判定是同一个文件。

    本次实际要拉的字节数超过上限（默认 200 MB）时会拒绝执行并列出清单，
    需显式加 -y/--yes，或用 --match / --id 缩小范围。
    """
    c = get_ctx(ctx)
    raw = c.backend.get_issue(key, fields=["attachment"])
    all_attachments = (raw.get("fields") or {}).get("attachment") or []

    selected = list(all_attachments)
    if ids:
        wanted = {str(i) for i in ids}
        selected = [a for a in selected if str(a.get("id")) in wanted]
    if match:
        selected = [a for a in selected if fnmatch.fnmatch(a.get("filename") or "", match)]

    if not selected:
        emit({"issue": key, "downloaded": 0, "cached": 0, "files": []}, fmt if fmt != "table" else "yaml")
        note("没有匹配的附件")
        return

    target_dir = _download_dir(c, key, directory)
    # 落盘名基于**该 issue 的全部附件**判重，而不是本次筛选后的子集，
    # 这样加不加 --match 得到的路径一致，缓存才命中得上
    names = _dest_names(all_attachments)

    # 先分出「要拉的」和「能复用的」，再决定放不放行——体积闸门只该拦真正
    # 要走网络的部分，全部命中缓存时不该被拦
    plan = []
    for att in selected:
        dest = target_dir / names[str(att["id"])]
        # 纵深防御：_safe_name 已经保证是纯基名，这里再确认一次落点没被穿出去
        if dest.parent.resolve() != target_dir:
            raise JiraCliError(f"附件落点异常，拒绝写入：{dest}")
        size = att.get("size") or 0
        cached = (
            not force and dest.exists() and dest.stat().st_size == size
        )
        plan.append((att, dest, size, cached))

    pending = [item for item in plan if not item[3]]
    pending_bytes = sum(item[2] for item in pending)
    limit = c.config.download_limit_mb * 1024 * 1024
    if pending and limit > 0 and pending_bytes > limit and not yes:
        raise JiraCliError(
            f"本次需要下载 {_human_size(pending_bytes)}，超过上限 {c.config.download_limit_mb} MB",
            _oversize_hint(key, pending, c.config.download_limit_mb),
        )

    files = []
    fetched = reused = 0
    for att, dest, size, cached in plan:
        if cached:
            reused += 1
        else:
            content_url = att.get("content")
            if not content_url:
                raise JiraCliError(
                    f"附件 {att.get('filename')}（id={att.get('id')}）没有下载地址",
                    "该实例可能限制了附件访问，用 issue show --raw 看原始响应确认。",
                )
            size = c.backend.download_attachment(content_url, dest)
            fetched += 1
        files.append(
            {
                "id": att.get("id"),
                "filename": att.get("filename"),
                "path": str(dest),
                "size": size,
                "mime": att.get("mimeType"),
                "cached": cached,
            }
        )

    if reused:
        note(f"复用本地缓存 {reused} 个，新下载 {fetched} 个（加 --force 可强制重下）")

    emit(
        {
            "issue": key,
            "dir": str(target_dir),
            "downloaded": fetched,
            "cached": reused,
            "total_size": sum(f["size"] for f in files),
            "files": files,
        },
        fmt,
        rows=files,
    )


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def _oversize_hint(key: str, pending: list, limit_mb: int) -> str:
    """超限时把清单摊开，让调用方能直接决定怎么缩小范围。"""
    lines = ["本次要下载的附件（大的在前）："]
    for att, _dest, size, _cached in sorted(pending, key=lambda i: i[2], reverse=True)[:15]:
        lines.append(f"  {_human_size(size):>10}  {att.get('filename')}  (id={att.get('id')})")
    if len(pending) > 15:
        lines.append(f"  … 另有 {len(pending) - 15} 个")
    lines.append("")
    lines.append("三种处理方式：")
    lines.append(f"  1. 缩小范围：jira-cli issue download {key} --match '*.log'")
    lines.append(f"  2. 只要某个：jira-cli issue download {key} --id <上面的 id>")
    lines.append(f"  3. 确实都要：jira-cli issue download {key} -y")
    lines.append(f"（上限可改：jira-cli config set download-limit-mb <数字>，设 0 关闭）")
    return "\n".join(lines)


def _download_dir(c: Any, key: str, directory: Optional[Path]) -> Path:
    """决定落点。

    --dir 给了就用它（平铺，不再套一层 KEY）；否则用配置的 download_dir
    或缓存目录，并在其下按 issue key 分目录。
    """
    if directory:
        return Path(directory).expanduser().resolve()
    configured = (c.config.download_dir or "").strip()
    root = Path(configured).expanduser() if configured else default_download_dir()
    return (root / key).resolve()


def _safe_name(raw: str, att_id: str) -> str:
    """把 Jira 返回的 filename 收敛成一个纯基名。

    **附件名由上传者控制，不能直接拿来拼路径**：`Path(dir) / "../../x"`
    会逃出落点，`Path(dir) / "/etc/cron.d/x"` 更是直接丢弃落点、按绝对
    路径写。本工具是给 AI 自动调用的，一句 issue download 就落盘，
    落点必须封死——只取基名，目录成分一律丢掉。
    """
    name = (raw or "").replace("\\", "/").strip()
    name = PurePosixPath(name).name if name else ""
    if name in ("", ".", ".."):
        return f"attachment-{att_id}"
    return name


def _dest_names(attachments: list[dict]) -> dict[str, str]:
    """{附件 id: 落盘文件名}。

    **Jira 允许同一 issue 挂同名附件**（实测：两个 same-name.txt，
    id 不同、大小不同）。直接用 filename 落盘会互相覆盖——报告下载 N 个，
    磁盘上只有 1 个，且大小与报告对不上。同名的一律加 id 区分。

    判重在**净化之后**做：`a/x.txt` 与 `b/x.txt` 净化后同名，必须一起
    加 id 区分，否则又回到互相覆盖。
    """
    safe = {str(att.get("id")): _safe_name(att.get("filename"), str(att.get("id"))) for att in attachments}

    counts: dict[str, int] = {}
    for name in safe.values():
        counts[name] = counts.get(name, 0) + 1

    out: dict[str, str] = {}
    for att_id, name in safe.items():
        if counts.get(name, 0) > 1:
            stem = PurePosixPath(name)
            out[att_id] = f"{stem.stem}.{att_id}{stem.suffix}"
        else:
            out[att_id] = name
    return out
