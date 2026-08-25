# JQL 速查

`jira-cli issue list` 的封装参数覆盖不了的场景，用 `--jql` 直通。两者**可以同时用**，会以 AND 合并：

```bash
jira-cli issue list --project ABC --jql 'sprint in openSprints()' -o yaml
```

输出里的 `jql` 字段是本次实际执行的完整语句，查询结果不对时先看它。

## 基本语法

```
<字段> <操作符> <值> [AND|OR] ... [ORDER BY <字段> ASC|DESC]
```

- 字符串值加双引号：`status = "In Progress"`。纯数字、函数、相对日期不加。
- 字段名不区分大小写，值区分。
- 自定义字段用 `cf[10001]` 或字段名加引号：`"严重程度" = Major`。字段 id 用 `jira-cli meta fields <关键词>` 查。

## 操作符

| 操作符 | 说明 | 示例 |
|---|---|---|
| `=` `!=` | 等于 / 不等于 | `status != Done` |
| `>` `>=` `<` `<=` | 比较（日期、数字） | `created >= -7d` |
| `IN` `NOT IN` | 多值 | `status in (Open, "In Progress")` |
| `~` `!~` | 文本包含 / 不包含 | `summary ~ "登录"` |
| `IS` `IS NOT` | 空值判断 | `assignee IS EMPTY` |
| `WAS` `WAS NOT` | 历史上曾经是 | `status WAS "In Progress"` |
| `CHANGED` | 发生过变更 | `status CHANGED AFTER -7d` |

`~` 走全文索引，是分词匹配不是子串匹配，短词或中文可能匹配不到。要精确匹配子串用 `summary ~ "\"完整短语\""`。

## 常用字段

| 字段 | 说明 |
|---|---|
| `project` | 项目 key |
| `issuetype` | issue 类型名 |
| `status` | 状态名 |
| `statusCategory` | 状态大类：`"To Do"` / `"In Progress"` / `Done`。跨工作流统计时用它比 `status` 稳 |
| `assignee` `reporter` `creator` | 用户，用登录名或 `currentUser()` |
| `priority` | 优先级名 |
| `resolution` | 解决结果。未解决是 `resolution IS EMPTY` 或 `resolution = Unresolved` |
| `labels` | 标签 |
| `component` | 模块 |
| `fixVersion` `affectedVersion` | 修复 / 影响版本 |
| `created` `updated` `resolved` `duedate` | 时间 |
| `parent` | 父 issue |
| `text` | 全文（标题 + 描述 + 评论 + 自定义字段） |

## 时间写法

- 绝对：`created >= "2026-05-01"`、`created >= "2026-05-01 09:00"`
- 相对：`-7d`（7 天前）、`-2w`、`-1M`、`-3h`、`-30m`。单位 `m` 分钟、`h` 小时、`d` 天、`w` 周、`M` 月、`y` 年
- 区间：`created >= "2026-05-01" AND created <= "2026-05-31"`
- 函数：`startOfDay()`、`startOfWeek()`、`startOfMonth()`、`endOfDay(-1)`

## 常用函数

| 函数 | 说明 |
|---|---|
| `currentUser()` | 当前 token 用户 |
| `membersOf("组名")` | 某用户组的成员 |
| `openSprints()` `closedSprints()` `futureSprints()` | sprint 状态（需 Jira Software） |
| `unreleasedVersions()` `releasedVersions()` | 版本状态 |
| `EMPTY` / `NULL` | 空值 |

## 排序

```
ORDER BY updated DESC, priority ASC
```

用 `--sort updated:desc` 更简单，只有多字段排序才需要写进 `--jql`：

```bash
jira-cli issue list --project ABC --jql 'ORDER BY priority DESC, created ASC'
```

优先级：**显式给的 `--sort` > `--jql` 里的 `ORDER BY` > 默认 `updated:desc`**。两者别同时写。

## 常用查询模板

```bash
# 我名下未解决的
--jql 'assignee = currentUser() AND resolution IS EMPTY'

# 某人最近两周解决的
--jql 'assignee = "zhang.san" AND resolved >= -2w'

# 无人认领的高优先级
--jql 'assignee IS EMPTY AND priority in (Highest, High)'

# 最近 7 天状态变过的
--jql 'status CHANGED AFTER -7d'

# 曾经打回过的（进过某状态）
--jql 'status WAS "Reopened"'

# 某版本待修的缺陷
--jql 'fixVersion = "v2.0" AND issuetype = Bug AND statusCategory != Done'

# 长期没动静的未完成项
--jql 'statusCategory != Done AND updated <= -30d'

# 全文搜关键词
--jql 'text ~ "白屏"'

# 自定义字段
--jql '"严重程度" = Major'
--jql 'cf[10100] = Major'

# 某 epic 下的（Jira Software）
--jql '"Epic Link" = ABC-100'
```

## 排错

| 报错 | 原因 |
|---|---|
| `Field 'xxx' does not exist` | 字段名拼错，或该字段在当前项目不可见。用 `meta fields` 查 |
| `The value 'xxx' does not exist for the field 'yyy'` | 取值不存在。用 `meta statuses` / `meta priorities` / `createmeta` 查可选值 |
| `Unable to parse the query` | 语法错。常见是漏引号，或值里带了未转义的双引号 |
| 结果为空但预期有数据 | 先看输出里的 `jql` 字段确认语句；再确认当前账号对该项目有浏览权限 |

shell 里 JQL 用**单引号**包裹，里面的字符串值用双引号，能避开绝大多数转义问题。
