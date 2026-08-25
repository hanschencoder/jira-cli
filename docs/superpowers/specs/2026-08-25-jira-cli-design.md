# jira-cli 设计文档

日期：2026-08-25

## 1. 目标

命令行工具，调用 Jira REST API 查询、创建、更新 issue，下载附件。**主要消费者是 AI 编码助手**（Claude Code / Cursor / Codex / Gemini CLI / Copilot），因此输出必须结构化、省 token、出错时告诉 AI「怎么修」。配套一套 skill 指导 AI 正确调用。

参考 `~/work/redmine-cli`（同类工具，分发与 skill 结构直接复用）与开源项目 `ankitpokhrel/jira-cli`（借鉴其正文转换手法与 JQL builder，规避其输出模型）。

### 非目标

- **不做 TUI**：没有交互式界面。`rich` 仅用于渲染静态表格和彩色错误信息。
- **不做统计分析**：不实现 redmine-cli 的 `stats count/hours/flow/trend`。
- **不做敏捷功能**：不实现 board / sprint / backlog / epic。
- **不做批量写**：`issue update` / `transition` / `comment` 只接受单个 issue key。
- **不做 dry-run**。
- **不实现 Jira Cloud**：只做架构预留（见 §4.2）。

## 2. 目标实例（已实测确认）

| 项 | 值 | 确认方式 |
|---|---|---|
| URL | `https://jira.example.com/` | — |
| 部署类型 | **Server**（非 Cloud/DC） | `GET /rest/api/2/serverInfo` → `deploymentType: "Server"` |
| 版本 | 8.20.11（buildNumber 820011, 2022-07-19） | 同上 |
| API 版本 | **v2**（Server 无 v3） | — |
| 认证 | **PAT / Bearer** | `GET /rest/pat/latest/tokens` 返回 401（存在但需鉴权，非 404）；实测 `Authorization: Bearer` 调 `/myself` 成功 |
| 当前用户 | 登录名形如 `zhang.san` | `GET /rest/api/2/myself` |
| 可访问项目 | 14 个 | `GET /rest/api/2/project` |
| 规模 | 有描述的 issue 117485 条 | `search` JQL `description IS NOT EMPTY` |
| **description 渲染器** | **Wiki Style Renderer** | 见下 |

### 2.1 渲染器判定证据

对两条真实 issue 取 `?expand=renderedFields`，对比原始与渲染结果：

| 原始 | 渲染后 | 结论 |
|---|---|---|
| `*Preconditions:*` | `<b>Preconditions:</b>` | wiki 粗体生效 |
| `{*}Procedure{*}` | `<b>Procedure</b>` | wiki 粗体转义形式生效 |
| `\|STEP\|Procedure\|` | `<table class='confluenceTable'>` | wiki 表格生效 |
| ` # IOS 平台…` | `<ol><li>…</li></ol>` | wiki 有序列表生效 |

**description 确认走 Wiki Style Renderer。**

`comment` 字段的渲染器**未能实测确认**——采样的 issue 评论均为纯文本，无标记可供判别。Jira 出厂的默认字段配置对 `description` / `comment` / `environment` 使用同一渲染器，故按 wiki 处理。设计上由 §4.3 的 codec 抽象 + `config init` 运行时探测覆盖这个不确定性，探测不出则按 wiki 兜底。

## 3. 需求

1. 查询 issue（封装参数 + 原始 JQL 双通道）
2. 查看 issue 详情（分层输出，默认精简）
3. 创建 issue
4. 更新 issue 字段
5. 按名称流转状态（transition）
6. 添加 / 查看评论
7. 下载附件到本地并输出路径清单
8. 元数据查询 + 本地缓存
9. 写操作本地留痕
10. 配套 skill

## 4. 架构

### 4.1 模块划分

```
src/jira_cli/
  cli/
    __init__.py    typer app 组装；commands 子命令（自省用）
    common.py      全局选项（--url/--token/--insecure/-o）与上下文
    issue.py       issue 子命令组
    meta.py        meta 子命令组
    config_cmd.py  config 子命令组
    log_cmd.py     log 子命令
  client.py        Backend 抽象基类 + ServerBackend（REST v2）
  markup/
    __init__.py    Codec 抽象 + get_codec(renderer)
    wiki.py        WikiCodec：wiki markup ↔ Markdown
    plain.py       PlainCodec：原样透传
  jql.py           JQL 链式 builder + 值转义
  fields.py        字段白名单裁剪、嵌套展平、名称→id 解析
  meta_cache.py    元数据本地缓存
  config.py        配置加载（命令行 > 环境变量 > 配置文件）
  output.py        yaml / json / table / md 渲染
  writelog.py      写操作 JSONL 留痕
  errors.py        Jira 错误响应 → 可自我修复的提示
```

每个模块单一职责，`cli/` 层不含业务逻辑，只做参数解析与调用编排。

### 4.2 Cloud 预留

不实现 Cloud，但把差异点收敛到两个抽象里，将来加 Cloud 只需新增两个文件、不动其余代码：

- `client.Backend`（抽象基类）—— 本期只有 `ServerBackend`
- `markup.Codec`（抽象基类）—— 本期有 `WikiCodec` / `PlainCodec`

**明确规避 `ankitpokhrel/jira-cli` 的做法**：该项目在 `api/client.go` 里用一串 `ProxyCreate` / `ProxySearch` / `ProxyTransitions` 函数，每个内部 `if installation == Local` 判分支。每加一个 API 就要多一个 proxy 函数，分支逻辑散落各处。本项目用多态替代标志位判断。

从该项目的分派层可读出 Cloud/Server 的**实际差异清单**，`Backend` 抽象需覆盖：

| 差异 | Server (v2) | Cloud (v3) |
|---|---|---|
| 端点版本 | `/rest/api/2/*` | `/rest/api/3/*` |
| 正文格式 | wiki markup 字符串 | ADF（JSON） |
| **用户身份** | `user.name`（登录名） | `user.accountId` |
| search 分页 | `startAt` + `maxResults` | 仅 `maxResults` |

用户身份这条是真正的语义差异而非换端点：`--assignee zhang.san` 在 Server 上可直接使用，Cloud 上必须先解析成 accountId。

### 4.3 正文转换（本项目唯一有技术风险的部分）

统一约定：**AI 读到的和写入的都是 Markdown**，wiki markup 只存在于 `markup/` 内部。

`WikiCodec` 两个方向用不同手段：

| 方向 | 实现 | 理由 |
|---|---|---|
| **wiki → Markdown**（读） | `jira2markdown` 库（0.5.1，pyparsing PEG 文法） | 这是难的一半：要解析任意 wiki 语法。`ankitpokhrel/jira-cli` 为此手写了 597 行 parser，其源码注释 `'*' can be either be bold or an unordered list 🤦` 说明了歧义之多。Python 生态有成熟库，直接省掉这活儿 |
| **Markdown → wiki**（写） | `markdown-it-py` 解析成 AST + 自写 `JiraWikiRenderer` | 这是简单的一半：AI 生成的 Markdown 是可预期的子集。**关键是不用正则**——`ankitpokhrel/jira-cli` 的 `ToJiraMD` 用 `blackfriday`（真 CommonMark 解析器）+ `blackfriday-confluence` 渲染器，本项目照搬这个手法。正则替换会在嵌套列表、表格内联代码、转义上翻车 |

PyPI 上的 `md2jira` 已评估并**排除**：质量差，且硬钉 `mistletoe>=0.8.2,<0.9`，会与其他依赖冲突。

#### Markdown → wiki 映射表

| Markdown | Jira wiki |
|---|---|
| `# H1` … `###### H6` | `h1.` … `h6.` |
| `**粗**` | `*粗*` |
| `*斜*` / `_斜_` | `_斜_` |
| `~~删除~~` | `-删除-` |
| `` `行内` `` | `{{行内}}` |
| ```` ```lang ```` 代码块 | `{code:lang}` … `{code}` |
| 无语言代码块 | `{noformat}` … `{noformat}` |
| `- 项` | `* 项`（嵌套加层 `**`） |
| `1. 项` | `# 项`（嵌套加层 `##`） |
| `[文字](url)` | `[文字\|url]` |
| `> 引用` | `bq. 引用`（多行用 `{quote}`） |
| 表格 | `\|\|表头\|\|` + `\|单元格\|` |
| `---` | `----` |

**转义**：正文中出现的 wiki 元字符（`{} [] | * _ - + ^ ~`）需转义，避免 AI 写的普通文本被误解析成标记。

**逃生舱**：`--description-raw`（建单/更新）与 `--raw`（评论） 直接提交 wiki 原文，绕过转换器。转换器出边界情况时不阻塞流程。

### 4.4 查询：单一 JQL builder，双入口

`jql.py` 提供链式 builder（借鉴 `ankitpokhrel/jira-cli` 的 `pkg/jql`）：

```python
JQL().filter_by("project", "ABC").in_("status", "Open", "In Progress") \
     .gte("updated", "-7d").order_by("updated", "desc").build()
```

封装参数和 `--jql` **汇入同一个 builder**（后者走 `.raw()`），而不是两套代码路径：

- `--project` / `--assignee` / `--status` / `--type` / `--priority` / `--reporter` / `--label` / `--created` / `--updated` / `--summary` → builder 方法
- `--jql "..."` → `.raw()`
- 两者可共存：`--project X --jql "labels = urgent"` 用 AND 合并

**值转义**：字符串值中的 `"` 与 `\` 必须转义，防止 JQL 注入与语法错误。`me` → `currentUser()`。

### 4.5 输出：token 节省的三处发力

**这是本项目相对 `ankitpokhrel/jira-cli` 的核心差异化。** 该项目的输出是 TUI-first：默认交互式表格，`--plain` 输出给 `awk` 切列的定宽文本，`--raw` 是**未经任何过滤的原始 Jira JSON**。没有 YAML、没有字段裁剪、list 视图不转换描述格式。它服务的是「终端前的人 + shell 脚本」，不是上下文窗口。

本项目：

1. **源头裁剪**：`issue list` 调 `/search` 时带 `fields=` 白名单，不需要的字段根本不从服务端拉取。
2. **输出展平 + 去噪**：`{"status":{"name":"进行中","id":"3","iconUrl":"…","statusCategory":{…}}}` → `status: 进行中`。剔除所有 `null` 值、`self` / `avatarUrls` / `iconUrl` 等内部 URL 字段。
3. **分层展开**：`issue show` 默认只给核心字段 + Markdown 描述；`--comments` / `--history` / `--links` / `--subtasks` / `--fields a,b` 按需叠加；`--raw` 是完整原始 JSON 的逃生舱。

格式选项 `-o`，可后置在命令末尾：

| 格式 | 用途 |
|---|---|
| `table`（默认） | 静态文本表格，给终端前的人看。非交互 |
| `yaml` | **AI 首选**，噪音最少最省 token |
| `json` | 需要 `jq` 精确提取字段时 |
| `md` | Markdown 表格，放进回复或文档 |

### 4.6 错误设计：输出「怎么修」而非只说「错了」

对 AI 最有价值的部分。错误走 stderr，退出码非 0。`errors.py` 把 Jira 的 `errorMessages` / `errors` 展开成可操作提示：

- **transition 名称匹配不上或缺必填字段** → 直接列出该 issue 当前可用的全部 transition 名称、各自的必填字段及可选值。AI 一轮即可自我纠正，无需再调 `meta transitions`。
- **create/update 校验失败（400/422）** → 回吐 createmeta 里该字段的 `allowedValues`。
- **401/403** → 提示检查 PAT 是否过期或权限不足。

### 4.7 附件

Jira Server 的附件 `content` URL 必须带认证头才能取，AI 拿到裸链接下载不了。因此必须落盘：

- `issue attachments <KEY>` —— 列清单（id / 文件名 / 大小 / MIME / 上传者 / 时间）
- `issue download <KEY> [--id N] [--match '*.log'] [--dir ./x]` —— 下载，默认落到 `./jira-attachments/<KEY>/`，输出结构化的**本地绝对路径**清单，AI 直接拿路径去 Read/grep

> `ankitpokhrel/jira-cli` 完全没有附件能力（全仓库唯一提及 attachment 之处是 ADF 的一个节点类型），此项无可参考。

### 4.8 配置

优先级：**命令行参数 > 环境变量（`JIRA_URL` / `JIRA_TOKEN`）> 配置文件**。

配置文件位置由 `platformdirs` 决定（Linux `~/.config/jira-cli/config.toml`），`JIRA_CLI_CONFIG_DIR` 可覆盖。含 token，权限 `600`，已在 `.gitignore`。

| 键 | 说明 |
|---|---|
| `url` | 实例地址 |
| `token` | PAT |
| `auth_type` | `bearer`（默认）/ `basic` |
| `deployment` | `server` / `cloud`，`config init` 自动探测 |
| `renderer` | `wiki` / `plain`，`config init` 自动探测，可手动覆盖 |
| `default_project` | 默认项目，省去每条命令都敲 `--project` |

`config init` 交互式引导：填 url + token → 打 `/serverInfo` 探测 deployment → 拉 `/project` 列表让用户选默认项目 → 取一条有描述的 issue 比对 `renderedFields` 探测 renderer。

> `default_project` 借鉴自 `ankitpokhrel/jira-cli`（其 config 存 Project 与 Board）。该实例有 14 个项目，无默认值时每条命令都要显式指定，很烦。

不支持多 profile 切换（与 redmine-cli 一致）；临时换实例用全局 `--url` / `--token` 覆盖。

### 4.9 写操作留痕

无 dry-run、无批量写，护栏只有留痕。每次写操作（create / update / transition / comment / 上传附件）追加一行 JSON 到 `~/.config/jira-cli/write-log.jsonl`：

```json
{"ts":"2026-08-25T14:30:00+08:00","op":"transition","key":"ABC-123","payload":{...},"ok":true,"result":{...}}
```

`jira-cli log [-n 20]` 回查。出事能追溯。

## 5. 命令树

```
jira-cli
  commands                            列出全部子命令及简介（AI 自省入口）
  config      init | set | get
  issue
    list        --project/--assignee/--status/--type/--updated/… 或 --jql
    show        <KEY> [--comments --history --links --subtasks --fields a,b --raw]
    create      --project --type --summary --description [-f name=value] [--attach 文件]
    update      <KEY> [--summary --assignee --priority -f name=value] [--attach 文件]
    comment     <KEY> "正文"
    comments    <KEY>
    transition  <KEY> "已完成" [-f resolution=Done]
    attachments <KEY>
    download    <KEY> [--id N] [--match '*.log'] [--dir ./x]
  meta
    projects | issuetypes | statuses | priorities | users | fields
    transitions <KEY>                 该 issue 当前可用流转 + 必填字段
    createmeta  --project X --type Bug 建单必填字段 + 可选值
    whoami | update                   update = 刷新本地缓存
  log         [-n 20]                 写操作留痕回查
```

`meta createmeta` 走 Jira 原生的 `/issue/createmeta?…&expand=projects.issuetypes.fields`，能直接拿到必填字段与 `allowedValues`——这块 Jira 比 Redmine 强，redmine-cli 里那套「找一条同类 issue 照抄字段」的迂回办法在这里不需要。

## 6. 依赖

| 包 | 用途 |
|---|---|
| `typer` | 命令行框架 |
| `rich` | 静态表格渲染、彩色错误。**不用于任何交互式界面** |
| `requests` | HTTP |
| `pyyaml` | YAML 输出 |
| `jira2markdown` | wiki → Markdown |
| `markdown-it-py` | Markdown → AST（配自写 wiki renderer） |
| `platformdirs` | 跨平台配置目录 |
| `tomli-w` | 写配置 |
| `tomli` | 读配置（Python < 3.11） |

Python ≥ 3.9。

## 7. 测试策略

**不写自动化测试**，与 redmine-cli 惯例一致，靠连真实 Jira 冒烟验证。

冒烟时用 `JIRA_CLI_CONFIG_DIR` 指向临时目录，避免污染本机配置：

```bash
export JIRA_CLI_CONFIG_DIR=/tmp/jiratest
uv run jira-cli meta whoami -o yaml
```

写操作冒烟只在 `SCRUM` / `REQP`（Demo 性质项目）上做，不碰生产项目。

## 8. 配套 skill

`skills/jira-cli/`，结构对齐 redmine-cli：

- `SKILL.md` —— 配置引导、输出格式选择、核心铁律（先查 meta 再操作 / 写操作不可逆不可批量 / 状态必须走 transition 不能直接设置）、命令速查表
- `references/workflows.md` —— 分析 issue、建单、改状态、下附件排查四条完整工作流
- `references/output-format.md` —— 各命令输出字段说明
- `references/jql.md` —— JQL 速查，供 AI 降级到 `--jql` 时使用

## 9. 交付物

```
src/jira_cli/          CLI 实现
skills/jira-cli/       配套 skill
install.sh             uv tool install + skill 装到 ~/.agents/skills/jira-cli
                       并软链到 Claude/Cursor/Codex/Gemini/Copilot 的 skill 目录
uninstall.sh           移除命令、skill 正本、各工具软链、配置
README.md              面向用户
CLAUDE.md              面向 AI 的项目说明
pyproject.toml
```

install/uninstall 脚本完整复制 redmine-cli 的方案。git 远端地址待定，先留占位符。

## 10. 已知风险

| 风险 | 缓解 |
|---|---|
| Markdown → wiki 转换器边界情况 | 用真 AST 解析而非正则；提供 `--description-raw`（建单/更新）与 `--raw`（评论） 逃生舱 |
| `comment` 字段渲染器未实测确认 | codec 抽象 + `config init` 运行时探测，兜底按 wiki |
| `jira2markdown` 对本实例的 wiki 方言覆盖不全 | `issue show --raw` 可拿原始 wiki 文本 |
| 无自动化测试，转换器回归靠人盯 | 冒烟脚本覆盖典型正文样本 |
| PAT 过期 | 错误提示明确指向 PAT 续期页面 |
