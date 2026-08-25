# 输出字段说明

用 `-o yaml`（推荐，省 token）或 `-o json` 输出结构化结果。下面用 JSON 展示各命令的字段结构；`-o yaml` 是同构的 YAML，字段完全一致。

**通用约定**：值为 `null` 或空数组的字段**不会出现**在输出里。看不到某个字段就是它没有值，不用当成异常。

## issue list

```json
{
  "jql": "project = \"ABC\" AND statusCategory != Done ORDER BY updated DESC",
  "returned": 2,
  "total_matched": 17,
  "issues": [
    {
      "key": "ABC-17",
      "summary": "用户反馈需求",
      "type": "Product Requirement",
      "status": "Open",
      "priority": "中",
      "assignee": "zhang.san",
      "reporter": "li.si",
      "project": "ABC",
      "labels": ["regression"],
      "created": "2026-08-25T14:24:42.000+0800",
      "updated": "2026-08-25T14:24:42.000+0800"
    }
  ]
}
```

- `jql` 是本次实际执行的 JQL。查询结果不对时先看它，能立刻判断是参数拼错还是数据本身如此。
- `returned` 本次返回条数；`total_matched` 匹配总数。`returned < total_matched` 说明被截断，调大 `-n`。
- `issues` 是**精简摘要**，不含 description 和自定义字段。嵌套对象（project / status / assignee 等）已展平成名称。
- `assignee` / `reporter` 是**登录名**，可直接回填给 `--assignee`。

## issue show

```json
{
  "key": "ABC-1",
  "summary": "标题",
  "type": "缺陷",
  "status": "Open",
  "priority": "高",
  "resolution": "已完成",
  "project": "ABC",
  "assignee": "zhang.san",
  "assignee_display": "张三",
  "reporter": "li.si",
  "reporter_display": "李四",
  "labels": ["SWIM"],
  "components": ["问题管理"],
  "fix_versions": ["v2.0"],
  "due": "2026-09-01",
  "created": "2026-08-25T14:10:16.000+0800",
  "updated": "2026-08-25T14:13:45.000+0800",
  "description": "## 复现步骤\n\n1. 打开设置页",
  "parent": "ABC-100",
  "attachments": [ {"id": "6055050", "filename": "log.7z", "size": 523239424, "mime": "application/octet-stream", "author": "zhang.san", "created": "..."} ],
  "custom_fields": {"严重程度": "Major", "出现概率": "高概率"},
  "url": "https://jira.example.com/browse/ABC-1"
}
```

- `description` 已转成 **Markdown**。
- **`custom_fields` 默认不输出**，要加 `--custom`。原因：一条 issue 常挂几十个自定义字段，其中绝大多数是建单时预填的**模板占位符**（如 `【前提条件】：`、`Please fill in the template below.`）而不是有人真填的内容——别把它们当成 issue 的实际内容读。加了 `--custom` 后，键是字段**显示名**（已从 `customfield_10001` 翻译过来），可直接用于 `-f 显示名=值`。
- 注意区分：**`description` 才是 issue 的正文**。有些实例还存在一个名叫「描述」的**自定义字段**，那是另一回事，且往往只是模板。
- `assignee` 是登录名（写操作用它），`assignee_display` 是显示名（给人看）。
- 加 `--comments` 多出 `comments` 数组，`--history` 多出 `history`，`--links` 多出 `links`，`--subtasks` 多出 `subtasks`，`--custom` 多出 `custom_fields`。
- `--raw` 返回未经任何裁剪的 Jira 原始 JSON（单个 issue 常 50 KB 以上，慎用）。

### comments

```json
[ {"id": "6456274", "author": "zhang.san", "created": "2026-08-25T14:42:37.000+0800", "body": "已定位：**线程竞争**"} ]
```

`body` 已转成 Markdown。`updated` 只在评论被编辑过时才出现。

### history

```json
[
  {"at": "2026-08-25T11:31:57.000+0800", "who": "zhang.san", "field": "created", "to": "ABC-1"},
  {"at": "2026-08-25T14:43:25.000+0800", "who": "zhang.san", "field": "status", "from": "To Do", "to": "In Progress"},
  {"at": "2026-08-25T14:44:06.000+0800", "who": "zhang.san", "field": "resolution", "from": "Done"}
]
```

一行一次字段变更，**最早的在前**。

- 第一条 `field: "created"` 是**本工具合成**的创建事件。Jira 的 changelog 只记录*变更*、不含创建，直接输出会缺时间线的第一格。`who` 取 `creator`，取不到则退到 `reporter`。
- **`from` / `to` 缺失表示那一侧为空**：只有 `to` = 从无到有（如首次指派），只有 `from` = 被清空（如上例中 resolution 被清掉）。这是全局「null 不输出」约定的延续。
- 一次操作可能产生多行：一次状态流转常同时改 `status` 和 `resolution`，它们的 `at` 相同。

### links

```json
[ {"relation": "blocks", "key": "ABC-9", "summary": "被阻塞的需求", "status": "Open"} ]
```

## issue attachments

```json
{
  "issue": "ABC-1",
  "total": 2,
  "attachments": [
    {"id": "6055369", "filename": "data.csv", "size": 14, "mime": "text/csv", "author": "zhang.san", "created": "..."}
  ]
}
```

这里**没有**下载链接——Jira 的附件 URL 必须带认证头，给了也下不动。用 `issue download`。

## issue download

```json
{
  "issue": "ABC-1",
  "dir": "/abs/path/jira-attachments/ABC-1",
  "downloaded": 2,
  "files": [
    {"id": "6055369", "filename": "data.csv", "path": "/abs/path/jira-attachments/ABC-1/data.csv", "size": 14, "mime": "text/csv"}
  ]
}
```

`path` 是**本地绝对路径**，直接拿去 Read / grep。

## issue create

```json
{"key": "ABC-123", "url": "https://jira.example.com/browse/ABC-123", "attached": ["log.zip"]}
```

## issue update

```json
{"key": "ABC-1", "updated": ["assignee", "priority"], "attached": ["log.zip"], "url": "..."}
```

`updated` 是本次实际提交的字段名列表。

## issue comment

```json
{"key": "ABC-1", "comment_id": "6456274", "url": "..."}
```

## issue transition

```json
{"key": "ABC-1", "transition": "完成", "status": "Done", "comment_id": "6456183", "url": "..."}
```

`status` 是流转后的状态。带 `--comment` 时才有 `comment_id`。

## meta transitions

```json
{
  "issue": "ABC-1",
  "transitions": [
    {"id": "11", "name": "待办", "to": "To Do"},
    {"id": "31", "name": "完成", "to": "Done",
     "required_fields": [{"field": "resolution", "name": "解决结果", "allowed": ["已完成", "无法复现"]}]}
  ]
}
```

`name` 是流转动作名，`to` 是流转后的状态名。`issue transition` 两者都能匹配。

## meta createmeta

```json
{
  "project": "ABC",
  "issuetypes": [
    {"type": "Bug",
     "fields": [
       {"field": "summary", "name": "概要", "required": true, "type": "string"},
       {"field": "customfield_10100", "name": "严重程度", "required": true, "type": "option",
        "allowed": ["Blocker", "Major", "Minor"]}
     ]}
  ]
}
```

默认只列必填字段，`--all` 列全部。`allowed` 里的值必须**逐字照抄**给 `-f`。

## meta projects / issuetypes / statuses / priorities / fields

分别是 `{key, name, id}` / `{id, name, description}` / `{id, name}` / `{id, name}` / `{id, name, custom}` 的数组。

## meta users

```json
[ {"name": "zhang.san", "displayName": "张三", "email": "zhang.san@example.com"} ]
```

`name` 才是 `--assignee` 要填的值。

## meta whoami

```json
{"name": "zhang.san", "displayName": "张三", "email": "...", "active": true, "timeZone": "Asia/Shanghai"}
```

## log

```json
{"total": 3, "entries": [
  {"ts": "2026-08-25T14:43:26+08:00", "op": "transition", "key": "ABC-1", "payload": {...}, "ok": true, "result": {"to": "Done"}}
]}
```

`op` 取值：`create` / `update` / `comment` / `transition` / `attach`。新的在前。

## 错误

错误信息输出到 **stderr**，stdout 无输出，退出码非 0：

| 退出码 | 含义 |
|---|---|
| 1 | 通用错误（参数不合法、查询条件为空等） |
| 2 | 缺配置（url / token） |
| 3 | 鉴权失败（PAT 过期或权限不足） |
| 4 | Jira 返回非 2xx |
| 5 | 名称解析失败（项目 / 类型 / 状态 / 用户找不到） |
| 6 | 状态流转失败（名称匹配不上或缺必填字段） |

退出码 5 和 6 的错误信息里**会带上可选值清单**，照着修正一次即可，不需要再发探查请求。
