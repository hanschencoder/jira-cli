---
name: jira-cli
description: 用 jira-cli 命令行查询、创建、更新 Jira issue，下载附件。当用户要配置 Jira 连接、查自己或他人或某项目的 issue、看 issue 详情、改状态、加评论、建 issue、下载 issue 附件时使用。
---

# 使用 jira-cli

`jira-cli` 命令封装 Jira REST API（Server / Data Center，REST v2）。错误走 stderr，退出码非 0 即失败。

不确定有哪些命令时，先跑 `jira-cli commands` 列出全部子命令及简介。

## 0. 首次配置（没有 url + token 任何命令都跑不了）

第一步永远是确认连接已配置：`jira-cli config get`（显示当前 url 和脱敏 token）。

未配置时，引导用户设置（二选一）：

```bash
# 方式 A：交互式引导（推荐）。会自动探测部署形态、正文渲染器，并让用户选默认项目
jira-cli config init

# 方式 B：环境变量（临时 / CI）
export JIRA_URL=https://jira.example.com
export JIRA_TOKEN=<用户的 PAT>
```

- **PAT 获取**：Jira 页面右上角头像 →「个人设置」→「Personal Access Tokens」→ Create token。
- 优先级：命令行参数 > 环境变量（`JIRA_URL` / `JIRA_TOKEN`）> 配置文件。
- 全局参数还有 `--url`、`--token`、`-k/--insecure`（跳过 TLS 校验）。
- **不存在多 profile 切换**；临时换实例用全局 `--url` / `--token` 覆盖。
- 时间戳输出为 `2026-08-25 11:31:57.000`，已换算到配置时区（默认东八区，`jira-cli config set timezone +09:00` 可改）。

配置里的 `default-project` 很重要：设了之后 `issue list` / `issue create` 不带 `--project` 就走它。

## 1. 选输出格式（先想清楚「给谁看」）

`-o` 选格式，**可后置**在命令末尾，如 `jira-cli issue list --assignee me -o yaml`。

| 场景 | 优先级 | 说明 |
|---|---|---|
| **展示给用户** | table → md → yaml | 能用表格就用表格：终端直接看用默认 `-o table`，放进回复或文档用 `-o md` |
| **你自己读取** | yaml / json | yaml 噪音最少最省 token；需要按字段精确提取时用 `-o json` 配合 `jq` |

嵌套结构（`issue show`、`meta createmeta`）没有合理的表格投影，指定 `-o table` 会自动降级成 yaml。

`jq` 取字段省 token 示例：

```bash
jira-cli issue list --project ABC -o json | jq '.issues[] | {key, status, summary}'
jira-cli issue show ABC-1 -o json | jq -r '.description'
```

## 2. 核心铁律

1. **先查 meta 再操作**。不确定项目 / 类型 / 状态 / 字段 / 用户的写法时，先跑对应的 `jira-cli meta ...`。建 issue 前必须先 `meta createmeta` 查必填字段。
   - 项目有 **KEY**（`ABC`，issue 编号 `ABC-123` 的前缀，结构性标识）和**名称**（`示例项目`，展示用、可改名）之分。`--project` 两者都收，但**回填给用户看时用 KEY**，它才是稳定的。
2. **Jira 不能直接「设置状态」**，必须走工作流定义的 transition。用 `issue transition`，不要试图 `issue update -f status=...`。
3. **写操作不可批量、不可撤销**。`update` / `transition` / `comment` 一次只接受**一个** issue key。写操作直接进生产、会真的发通知。
4. **正文一律写 Markdown**。工具自动转成 Jira wiki markup，不要自己写 wiki 语法。
5. **附件必须下载才能读**。Jira 的附件链接要带认证头，直接给你裸链接你也下不动。
6. **`me` 指当前 token 用户**，用于 `--assignee me`、`--reporter me`。

## 3. 常用命令速查（照抄即正确）

| 任务 | 命令 |
|---|---|
| 列全部命令 | `jira-cli commands` |
| 查配置 | `jira-cli config get` |
| 查我名下未完成 issue | `jira-cli issue list --assignee me --status open -n 30 -o yaml` |
| 查项目全部 issue | `jira-cli issue list --project ABC -o yaml` |
| 按更新时间倒序 | `jira-cli issue list --project ABC --sort updated:desc -o yaml` |
| 最近 7 天有更新的 | `jira-cli issue list --project ABC --updated '>=-7d' -o yaml` |
| 复杂条件（降级到 JQL） | `jira-cli issue list --jql 'labels = urgent AND sprint in openSprints()' -o yaml` |
| 看 issue 详情 | `jira-cli issue show ABC-1 -o yaml` |
| 详情 + 评论 + 历史 | `jira-cli issue show ABC-1 --comments --history -o yaml` |
| 详情 + 自定义字段 | `jira-cli issue show ABC-1 --custom -o yaml` |
| 只要某几个字段 | `jira-cli issue show ABC-1 --fields status,assignee -o yaml` |
| 原始 JSON 逃生舱 | `jira-cli issue show ABC-1 --raw -o json` |
| 加评论 | `jira-cli issue comment ABC-1 '已定位：**线程竞争**'` |
| 看全部评论 | `jira-cli issue comments ABC-1 -o yaml` |
| 改字段 | `jira-cli issue update ABC-1 --assignee zhang.san --priority High` |
| 改状态（见第 5 节） | `jira-cli issue transition ABC-1 '完成'` |
| 建 issue（见第 6 节） | `jira-cli issue create --project ABC --type 任务 --summary '标题'` |
| 列附件 | `jira-cli issue attachments ABC-1 -o yaml` |
| 下载附件 | `jira-cli issue download ABC-1 --match '*.log'` |
| 上传附件 | `jira-cli issue update ABC-1 --attach ./log.zip` |
| 查该 issue 能流转到哪 | `jira-cli meta transitions ABC-1 -o yaml` |
| 查建单必填字段 | `jira-cli meta createmeta --project ABC --type Bug -o yaml` |
| 查项目 / 类型 / 状态 / 优先级 | `jira-cli meta projects\|issuetypes\|statuses\|priorities -o yaml` |
| 查用户登录名 | `jira-cli meta users 张三 -o yaml` |
| 查自定义字段 id | `jira-cli meta fields 严重程度 -o yaml` |
| 回查自己做过的写操作 | `jira-cli log -n 20` |

## 4. 筛选参数（`issue list`）

- `--project` / `-p` 项目 **KEY 或名称均可**（如 `ABC` 或 `示例项目`），内部统一解析成 key；不给则用配置里的 `default-project`。用名称或前缀匹配上时会在 stderr 说明落到了哪个项目
- `--assignee` / `-a` 经办人**登录名**（不是显示名），`me` 表示自己。登录名用 `meta users` 查
- `--reporter` 报告人，同上
- `--status` / `-s` 状态名；`open` / `closed` 是简写（映射到 `statusCategory`），`*` 表示不过滤
- `--type` / `-t` issue 类型名
- `--priority` 优先级名
- `--label` / `-l` 标签，可多次传（多个之间是 OR）
- `--summary` 标题包含关键词
- `--created` / `--updated` 时间区间，三种写法：`>=-7d`、`<=2026-05-31`、`2026-05-01|2026-05-31`
- `--jql` 原始 JQL，与上面的参数 **AND 合并**，不是二选一
- `--sort` 排序，如 `updated:desc`（默认）、`priority:desc`
- `--limit` / `-n` 最多返回条数，默认 50

**至少给一个筛选条件**，否则会报错拒绝执行（防止扫描全站）。

JQL 写法见 `references/jql.md`。

## 5. 改状态

Jira 的状态由工作流控制，能从当前状态走到哪些状态是**固定的**，不能任意设置。

```bash
# 1. 先看能流转到哪（也可以直接跳到第 2 步，失败时错误信息会告诉你）
jira-cli meta transitions ABC-1 -o yaml

# 2. 按名称流转。流转名和目标状态名都能匹配，支持唯一子串
jira-cli issue transition ABC-1 '完成'

# 3. 带必填字段
jira-cli issue transition ABC-1 '完成' -f resolution=Done

# 4. 流转同时加评论
jira-cli issue transition ABC-1 '完成' --comment '已验证通过'
```

**匹配不上或缺必填字段时，错误信息会直接列出当前可用的全部流转、各自的必填字段及可选值。** 照着补一次即可，不需要再单独跑 `meta transitions`。

## 6. 建 issue

```bash
# 1. 先查该项目 + 该类型的必填字段和可选值
jira-cli meta createmeta --project ABC --type Bug -o yaml

# 2. 建单。描述写 Markdown，工具自动转成 wiki markup
jira-cli issue create \
  --project ABC \
  --type Bug \
  --summary '登录页在弱网下白屏' \
  --description '## 复现步骤

1. 打开 **登录页**
2. 限速到 100 Kbps

## 期望 vs 实际

| 项 | 期望 | 实际 |
| --- | --- | --- |
| 首屏 | 3s 内 | 白屏 |
' \
  --assignee zhang.san \
  --priority High \
  --label regression \
  -f 严重程度=Major \
  --attach ./screenshot.png \
  -o yaml
```

- `-f name=value` 可多次传，`name` 用字段显示名或字段 id（`customfield_10001`）都行。
- 多选字段的多个取值用逗号分隔：`-f 影响模块=登录,支付`。
- 列表型字段的取值必须和 `createmeta` 里的 `allowed` **逐字一致**。
- `--attach` 可多次传。

### 铁律：绝不允许带占位符执行

**summary、description、每个 `-f` 的值必须是用户确认过的真实内容。** 写操作直接进生产、指派通知真的会发出，无法静默撤销。不确定就先问用户，或据同类 issue 生成草稿供 review。

给用户展示命令模板时，占位符要用明显非法的写法（如 `『在这里填』`），防止被整段复制执行。

## 7. 附件（两步：先看清单，再下载）

`attachments` 只列清单不下载，`download` 才落盘。**永远先跑 `attachments`。**

### 7.1 第一步：看清单

```bash
jira-cli issue attachments ABC-123 -o yaml
```

```yaml
issue: ABC-123
total: 2
attachments:
- id: '6055369'          # download --id 用这个
  filename: data.csv
  size: 14               # 字节。决定要不要下的关键
  mime: text/csv
  author: zhang.san
  created: '2026-08-25 14:42:38.000'
- id: '6055368'
  filename: smoke.log
  size: 55
  mime: text/plain
```

**这一步的意义是看 `size`。** 生产 issue 上挂几百 MB 的日志包很常见（实测见过单个附件 523 MB 的 `.7z`）。不看大小直接全下会拖垮磁盘和时间。

### 7.2 第二步：下载

```bash
# 全下，落到当前目录下的 ./jira-attachments/ABC-123/
jira-cli issue download ABC-123 -o yaml

# 按文件名 glob 过滤——排查日志时最常用
jira-cli issue download ABC-123 --match '*.log' -o yaml

# 按 id 精确要某一个（id 来自第一步的清单）
jira-cli issue download ABC-123 --id 6055369 -o yaml

# 指定目录（平铺放置，不再套 jira-attachments/<KEY>/ 那层）
jira-cli issue download ABC-123 --dir ./logs -o yaml
```

`--match` 与 `--id` 可叠加。输出：

```yaml
issue: ABC-123
dir: /abs/path/jira-attachments/ABC-123
downloaded: 2
files:
- id: '6055369'
  filename: data.csv
  path: /abs/path/jira-attachments/ABC-123/data.csv   # 本地绝对路径
  size: 14
  mime: text/csv
```

`path` 是**本地绝对路径**，直接拿去 Read / grep，不需要再拼。

没有匹配时**不报错**，正常返回 `downloaded: 0` 且 `files: []`，stderr 提示「没有匹配的附件」——脚本里不会因为空结果中断。

### 7.3 铁律：不要试图用 URL 下载

`attachments` 的输出里**故意不含下载链接**。Jira Server 的附件地址必须带 `Authorization: Bearer` 头才能取，浏览器能下是因为有 session cookie。

所以：**不要去 `issue show --raw` 里翻 `content` 字段然后 `curl` 或 WebFetch**——只会拿到 401 或登录页，白白浪费一轮。附件只能通过 `issue download` 拿。

### 7.4 拿到文件之后

- 文本 / 日志：直接 Read；**超过几 MB 先 `grep -n` 定位行号再局部读**，别整个塞进上下文
- 图片：Read 可以直接看
- 压缩包：先 `unzip -l` / `7z l` 看清单，再解需要的那部分
- 下载目录默认在**当前工作目录**下，跑命令前先确认自己在哪，别把文件撒到用户的代码仓库里

### 7.5 上传附件

```bash
jira-cli issue update ABC-123 --attach ./report.html --attach ./screenshot.png
```

`--attach` 可多次传。注意 **`-a` 是 `--assignee` 的短参**，不是附件。

## 8. 正文格式

读出来的 `description` 和评论正文都已经转成 **Markdown**；写进去也传 Markdown，工具负责转成 Jira wiki markup。

支持的元素：标题、粗体、斜体、删除线、行内代码、代码块（带语言）、有序 / 无序 / 嵌套列表、表格、引用、链接、图片、分隔线。

转换器在边界情况出问题时，有逃生舱直接传 wiki 原文：

```bash
jira-cli issue create ... --description-raw 'h2. 标题
{code:java}int a = 1;{code}'
jira-cli issue comment ABC-1 'bq. 引用' --raw
```

## 参考文档

- `references/workflows.md`：典型多步工作流（分析 issue、建单、改状态、下附件排查）
- `references/output-format.md`：各命令输出字段说明
- `references/jql.md`：JQL 速查，降级到 `--jql` 时用

## 注意

- `meta projects|issuetypes|statuses|priorities|fields` 默认读本地缓存（7 天有效期），需要最新数据加 `--refresh` 或跑 `jira-cli meta update` 清缓存。
- `issue list` 返回被截断时会在 stderr 提示匹配总数，按需调大 `-n`。
- 所有写操作都记在本地留痕日志里，`jira-cli log` 可回查。
