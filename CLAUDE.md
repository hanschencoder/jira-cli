# jira-cli

调用 Jira REST API 查询/创建/更新 issue、下载附件的命令行工具，配套指导 LLM 使用的 skill。

设计文档：`docs/superpowers/specs/2026-08-25-jira-cli-design.md`（决策与实测证据都在里面，改架构前先读）。

## 命令

```bash
# 开发模式：uv pip install -e .    运行：uv run jira-cli <子命令>（或 .venv/bin/jira-cli）
# 冒烟测试：连真实 Jira，先指向临时配置目录避免污染本机配置
export JIRA_CLI_CONFIG_DIR=/tmp/jiratest && uv run jira-cli meta whoami -o yaml
```

无自动化测试（本项目约定不写 TDD），靠连真实 Jira 冒烟验证。**写操作冒烟只在 Demo 性质的项目上做，不碰生产项目**——Jira 写操作直接进生产、会真的发通知，无法静默撤销。

## 目标实例（已实测，勿凭 Jira 通用知识臆断）

实例地址与 PAT 只存在于本地配置（`~/.config/jira-cli/config.toml`），不入库。下面是实测得到的**行为结论**：

- **Jira Server 8.20.11**，`deploymentType: "Server"`
- **REST API v2**（Server 没有 v3，别写 `/rest/api/3/`）
- 认证走 **PAT / Bearer**（`Authorization: Bearer <token>`），实例已启用
- `description` 字段确认走 **Wiki Style Renderer**（原始 `*粗*` 渲染成 `<b>`、`|a|b|` 渲染成 `<table>`）
- 规模参考：14 个可访问项目，11.7 万条有描述的 issue

## 架构

- `client.py` 用 **requests 直接封装** Jira REST v2（非 `jira` 官方库），自管鉴权/分页/错误展开
- **Cloud 支持只做架构预留，本期不实现**。差异收敛在两个抽象里：`client.Backend`（端点版本、用户身份模型、分页参数）和 `markup.Codec`（wiki/ADF）。加 Cloud = 新增 `CloudBackend` + `AdfCodec` 两个文件，不动其余代码
- `markup/` 正文转换：**读**用 `jira2markdown` 库（wiki→md），**写**用 `markdown-it-py` 出 AST + 自写 `JiraWikiRenderer`（md→wiki）
- `jql.py` 链式 JQL builder；封装参数与 `--jql` 汇入同一 builder（后者走 `.raw()`），不是两套代码
- `fields.py` 字段白名单裁剪/展平/名称→id 解析；`output.py` yaml/json/table/md；`meta_cache.py` 元数据缓存
- `config.py` 配置加载（命令行 > 环境变量 `JIRA_URL`/`JIRA_TOKEN` > 配置文件，无多 profile）
- `writelog.py` 写操作 JSONL 留痕；`errors.py` Jira 错误 → 可自我修复的提示
- `cli/` typer 命令层：`issue` / `meta` / `config` / `log`，入口 `cli/__init__.py:app`。**该层不含业务逻辑**，只做参数解析与调用编排

## 约定与踩坑

- **不做 TUI**。`rich` 只用于渲染静态表格和彩色错误，不要引入任何交互式界面。参考项目 `ankitpokhrel/jira-cli` 是 TUI-first 的，别照抄它的输出层
- **不做批量写**：`issue update` / `transition` / `comment` 只接受单个 issue key。**不做 dry-run**。护栏只有写操作留痕
- **不做统计（stats）、不做敏捷（board/sprint/epic）**
- **Jira 不能直接「设置状态」**，必须走 transition。`issue transition <KEY> <状态名>` 按名称模糊匹配 transition id；匹配失败或缺必填字段时，错误信息必须**列出当前可用的全部 transition 及其必填字段和可选值**——让 AI 一轮自我纠正，别只报「失败」
- **Jira 不强制项目名唯一**，且允许一个项目的名称等于另一个项目的 KEY（JRASERVER-69362，2025-03 以 Low Engagement 关闭，不会修）。所以 `resolve_one` 必须**按 id → key → name 逐字段整轮扫描**，不能逐条候选依次比对各字段——后者的命中结果取决于列表顺序，`--project FOO` 可能落到不同项目且毫无提示。同一轮里撞到多个必须报错
- **项目标识必须归一化成 key**。JQL 的 `project` 字段接受项目名，但 `/issue`（建单）和 `/issue/createmeta` 只认 key——**createmeta 拿到项目名时静默返回空列表**，看起来像「该项目没有 issue 类型」而不是报错。`Ctx.resolve_project()` 统一把 key/名称/id 解析成 key，所有吃 `--project` 的命令都要走它
- **流转带评论必须单独发一次评论请求**，不要塞进 transition 的 `update.comment`。实测：该 transition 的界面若没配「评论」字段，Jira **返回成功但静默丢弃评论**，不报任何错。静默失败对调用方最致命，宁可多一次请求
- **md→wiki 绝不用正则替换**。必须走 Markdown AST + renderer。正则会在嵌套列表、表格内联代码、元字符转义上翻车。参考 `ankitpokhrel/jira-cli` 源码注释：`'*' can be either be bold or an unordered list`
- **正文中的 wiki 元字符（`{} [] | * _ - + ^ ~`）必须转义**，否则 AI 写的普通文本会被误解析成标记
- 转换器有逃生舱：`--description-raw`（建单/更新）与 `--raw`（评论） 直接提交 wiki 原文
- **用户身份模型**：Server 用 `user.name`（登录名，如 `zhang.san`），Cloud 用 `user.accountId`。写 `Backend` 抽象时这是真正的语义差异，不是换个端点
- **Jira 的 changelog 不含创建事件**，只记录变更。`changelog_rows` 会用 `fields.created` + `creator`/`reporter` 合成一条 `field: created` 补上时间线第一格，否则看不出这条 issue 是谁开的
- **自定义字段默认不输出**。该实例一条 issue 挂 60+ 个自定义字段，其中绝大多数是**建单时预填的模板占位符**（`【前提条件】：`、`Please fill in the template below.`）而非有人写的内容，还混着插件塞的 Java 对象 toString。全吐出来是 4600 字符 vs 精简后 365 字符。要看时用 `issue show --custom`
- **给 `get_issue` 传 `fields=` 时当心运算符优先级**：`fields=a + b if cond else None` 会被解析成 `(a + b) if cond else None`，条件不成立时变成 `fields=None`，等于向服务端要**全部字段**，白名单形同虚设。这个 bug 潜伏过一段时间
- **输出必须省 token**：`/search` 带 `fields=` 白名单从源头裁剪；输出展平嵌套对象、剔除 `null` 和 `self`/`avatarUrls`/`iconUrl` 等内部 URL；`issue show` 默认精简，`--comments`/`--history`/`--links`/`--subtasks` 按需叠加
- **附件必须落盘**：Jira Server 的附件 `content` URL 要带认证头，AI 拿裸链接下不动。下载后输出**本地绝对路径**清单
- **本机 shell 是 zsh，未加引号的变量不做 word splitting**。冒烟脚本里写 `for c in "meta whoami" ...; do jira-cli $c; done` 会把整串当成**一个**参数传进去，命令全部失败而看起来像代码坏了。要循环测多个子命令，用数组或直接写全字面量
- `config.toml` 含 PAT，权限 600，已在 `.gitignore`；测试务必用 `JIRA_CLI_CONFIG_DIR` 隔离
- `comment` 字段的渲染器未实测确认（采样到的评论都是纯文本无从判别），按 wiki 处理，由 `config init` 运行时探测兜底
