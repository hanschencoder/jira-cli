"""字段裁剪、展平与名称解析。

省 token 的三处发力，这里占两处：
  1. 源头裁剪 —— LIST_FIELDS 白名单让 /search 只返回需要的字段
  2. 输出展平去噪 —— 嵌套对象压成标量，剔除 null 与内部 URL

第三处（分层展开）在 cli/issue.py 的 show 命令里。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .errors import ResolveError

#: issue list 从服务端拉取的字段白名单。不在这里的字段根本不传输。
LIST_FIELDS = (
    "summary",
    "status",
    "issuetype",
    "priority",
    "assignee",
    "reporter",
    "project",
    "created",
    "updated",
    "resolution",
    "labels",
)

#: issue show 默认拉取的字段（比 list 多描述、附件、组件、版本等）
SHOW_FIELDS = LIST_FIELDS + (
    "description",
    "attachment",
    "components",
    "fixVersions",
    "duedate",
    "parent",
    "subtasks",
    "issuelinks",
)

#: 纯噪音字段：内部 URL、头像、渲染用的 id。对 AI 一律无价值
NOISE_KEYS = frozenset(
    {
        "self",
        "avatarUrls",
        "iconUrl",
        "expand",
        "entityId",
        "hierarchyLevel",
        "statusCategory",
        "accountType",
        "timeZone",
        "active",
        "subtask",
        "avatarId",
        "projectTypeKey",
        "scope",
        "untranslatedName",
        "workRatio",
        "16x16",
        "24x24",
        "32x32",
        "48x48",
    }
)


def prune(value: Any) -> Any:
    """递归剔除 null、空容器与噪音字段。"""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in NOISE_KEYS:
                continue
            item = prune(item)
            if item is None or item == [] or item == {} or item == "":
                continue
            cleaned[key] = item
        return cleaned
    if isinstance(value, list):
        items = [prune(v) for v in value]
        return [v for v in items if v is not None and v != {} and v != []]
    return value


def user_name(user: Any) -> str:
    """用户对象 -> 登录名。

    输出登录名而非 displayName，因为登录名才是可回填给 --assignee 的值，
    而且更短。displayName 只在 issue show 里额外附上。
    """
    if not isinstance(user, dict):
        return ""
    return user.get("name") or user.get("key") or user.get("displayName") or ""


def _named(value: Any) -> Any:
    """{"name": "X", ...} -> "X"，其余原样返回。"""
    if isinstance(value, dict):
        return value.get("name") or value.get("value") or value.get("key")
    return value


def summarize_issue(raw: dict) -> dict:
    """issue list 的行视图：全部展平成标量，不含描述。"""
    f = raw.get("fields") or {}
    row = {
        "key": raw.get("key"),
        "summary": f.get("summary"),
        "type": _named(f.get("issuetype")),
        "status": _named(f.get("status")),
        "priority": _named(f.get("priority")),
        "assignee": user_name(f.get("assignee")),
        "reporter": user_name(f.get("reporter")),
        "project": (f.get("project") or {}).get("key"),
        "resolution": _named(f.get("resolution")),
        "labels": f.get("labels") or [],
        "created": f.get("created"),
        "updated": f.get("updated"),
    }
    return {k: v for k, v in row.items() if v not in (None, "", [])}


def attachment_row(att: dict) -> dict:
    return {
        "id": att.get("id"),
        "filename": att.get("filename"),
        "size": att.get("size"),
        "mime": att.get("mimeType"),
        "author": user_name(att.get("author")),
        "created": att.get("created"),
    }


def comment_row(comment: dict, codec: Any) -> dict:
    return {
        "id": comment.get("id"),
        "author": user_name(comment.get("author")),
        "created": comment.get("created"),
        "updated": comment.get("updated") if comment.get("updated") != comment.get("created") else None,
        "body": codec.from_jira(comment.get("body") or ""),
    }


def detail_issue(
    raw: dict,
    codec: Any,
    field_map: dict[str, str] | None = None,
) -> dict:
    """issue show 的核心视图（不含评论/历史，那些由调用方按需叠加）。"""
    f = raw.get("fields") or {}
    assignee = f.get("assignee") or {}
    reporter = f.get("reporter") or {}

    detail: dict[str, Any] = {
        "key": raw.get("key"),
        "summary": f.get("summary"),
        "type": _named(f.get("issuetype")),
        "status": _named(f.get("status")),
        "priority": _named(f.get("priority")),
        "resolution": _named(f.get("resolution")),
        "project": (f.get("project") or {}).get("key"),
        "assignee": user_name(assignee),
        "assignee_display": assignee.get("displayName"),
        "reporter": user_name(reporter),
        "reporter_display": reporter.get("displayName"),
        "labels": f.get("labels") or [],
        "components": [_named(c) for c in (f.get("components") or [])],
        "fix_versions": [_named(v) for v in (f.get("fixVersions") or [])],
        "due": f.get("duedate"),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "description": codec.from_jira(f.get("description") or ""),
    }

    parent = f.get("parent")
    if parent:
        detail["parent"] = parent.get("key")

    subtasks = f.get("subtasks") or []
    if subtasks:
        detail["subtasks"] = [
            {"key": s.get("key"), "summary": (s.get("fields") or {}).get("summary")}
            for s in subtasks
        ]

    attachments = f.get("attachment") or []
    if attachments:
        detail["attachments"] = [attachment_row(a) for a in attachments]

    customs = custom_fields(f, field_map or {})
    if customs:
        detail["custom_fields"] = customs

    return prune(detail)


def custom_fields(fields: dict, field_map: dict[str, str]) -> dict:
    """把 customfield_10001 这种 id 翻译成人类可读的字段名。

    不翻译的话 AI 完全看不懂这些字段是什么。
    """
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if not key.startswith("customfield_"):
            continue
        value = prune(value)
        if value in (None, "", [], {}):
            continue
        name = field_map.get(key, key)
        if isinstance(value, dict):
            value = _named(value) or value
        elif isinstance(value, list):
            value = [_named(v) for v in value]
        out[name] = value
    return out


def issue_links(fields: dict) -> list[dict]:
    """关联 issue，展平成 {方向描述: KEY, summary}。"""
    rows = []
    for link in fields.get("issuelinks") or []:
        link_type = link.get("type") or {}
        if link.get("outwardIssue"):
            other, label = link["outwardIssue"], link_type.get("outward")
        elif link.get("inwardIssue"):
            other, label = link["inwardIssue"], link_type.get("inward")
        else:
            continue
        rows.append(
            {
                "relation": label,
                "key": other.get("key"),
                "summary": (other.get("fields") or {}).get("summary"),
                "status": _named((other.get("fields") or {}).get("status")),
            }
        )
    return rows


def changelog_rows(raw: dict) -> list[dict]:
    """变更历史展平成一行一次字段变更。"""
    rows = []
    for entry in ((raw.get("changelog") or {}).get("histories") or []):
        author = user_name(entry.get("author"))
        created = entry.get("created")
        for item in entry.get("items") or []:
            rows.append(
                {
                    "at": created,
                    "who": author,
                    "field": item.get("field"),
                    "from": item.get("fromString"),
                    "to": item.get("toString"),
                }
            )
    return rows


# ---------------------------------------------------------------- 名称解析

def build_field_map(fields: Iterable[dict]) -> dict[str, str]:
    """/field 的结果 -> {id: name}。"""
    return {f["id"]: f.get("name") or f["id"] for f in fields if f.get("id")}


def reverse_field_map(fields: Iterable[dict]) -> dict[str, str]:
    """{name 小写: id}，供 -f 按字段名提交自定义字段。"""
    out: dict[str, str] = {}
    for f in fields:
        name = (f.get("name") or "").strip().lower()
        if name and f.get("id"):
            out.setdefault(name, f["id"])
    return out


def resolve_one(
    candidates: Sequence[dict],
    value: str,
    kind: str,
    keys: Sequence[str] = ("name",),
) -> dict:
    """在候选里按 id / key / 名称找一个，找不到就把可选值列出来。

    错误信息里带上可选值，是为了让 AI 一轮自我纠正，不必再发一次探查请求。
    """
    needle = str(value).strip()
    lowered = needle.lower()

    for item in candidates:
        if str(item.get("id")) == needle:
            return item
        for key in keys:
            if str(item.get(key) or "").lower() == lowered:
                return item
    # 退一步做包含匹配，且必须唯一，避免歧义下猜错
    partial = [
        item
        for item in candidates
        if any(lowered in str(item.get(key) or "").lower() for key in keys)
    ]
    if len(partial) == 1:
        return partial[0]

    available = ", ".join(
        sorted({str(item.get(keys[0]) or item.get("id")) for item in candidates})
    )
    extra = "（匹配到多个，需写得更精确）" if len(partial) > 1 else ""
    raise ResolveError(
        f"找不到{kind}：{value}{extra}",
        f"可选值：{available}" if available else f"当前没有可用的{kind}。",
    )


def build_schema_map(fields: Iterable[dict]) -> dict[str, dict]:
    """{field_id: schema}，用于把 -f 的字符串取值适配成 Jira 要的结构。"""
    return {f["id"]: (f.get("schema") or {}) for f in fields if f.get("id")}


#: 这些标准字段的取值要包成 {"name": ...}
_NAME_WRAPPED = {"priority", "resolution", "issuetype", "status", "assignee", "reporter", "parent"}


def coerce_value(field_id: str, value: str, schema: dict | None = None) -> Any:
    """把命令行来的字符串适配成 Jira 字段要的结构。

    Jira 的字段取值结构随类型变化很大：下拉框要 {"value": x}，
    用户字段要 {"name": x}，多选要数组。类型判错会得到一个没有指向性的
    400，所以宁可按 schema 精确适配。
    """
    schema = schema or {}
    stype = schema.get("type")

    if stype == "array":
        items = [v.strip() for v in value.split(",") if v.strip()]
        item_type = schema.get("items")
        if item_type in ("string", None):
            return items
        if item_type == "option":
            return [{"value": v} for v in items]
        return [{"name": v} for v in items]

    if stype == "option":
        return {"value": value}
    if stype in ("user", "priority", "resolution", "issuetype", "status", "project"):
        return {"name": value}
    if stype == "number":
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value
    if stype in ("date", "datetime", "string", None):
        # 没有 schema 信息时，按标准字段名兜底判断
        if field_id in _NAME_WRAPPED:
            return {"name": value}
        if field_id == "labels":
            return [v.strip() for v in value.split(",") if v.strip()]
        return value
    return value
