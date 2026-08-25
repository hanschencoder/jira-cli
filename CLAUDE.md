# jira-cli

调用 Jira REST API 查询/创建/更新 issue、下载附件的命令行工具，配套指导 LLM 使用的 skill。

## 命令

```bash
uv pip install -e .                      # 开发模式安装
uv run jira-cli <子命令>                  # 运行（或 .venv/bin/jira-cli）
uv build                                 # 打包
export JIRA_CLI_CONFIG_DIR=/tmp/jiratest # 冒烟前先隔离配置，避免污染本机
uv run jira-cli meta whoami -o yaml      # 冒烟
```

无自动化测试（本项目约定不写 TDD），靠连真实 Jira 冒烟验证。

**写操作冒烟前必须先验项目活跃度**，别只看名字：

```bash
uv run jira-cli issue list --project <KEY> --jql 'ORDER BY created DESC' -n 10
```

近一两年没人写、内容明显是测试垃圾的才可动。名字最像 demo 的未必最安全——该实例的 `REQP`「DEMO演示项目」有 8563 条且仍在日更。Jira 写操作直接进生产、会真的发通知，无法静默撤销。

## 架构

```
src/jira_cli/
  cli/          typer 命令层（issue/meta/config/log）。不含业务逻辑，只做参数解析与编排
  client.py     REST v2 封装（requests 直连，非官方库）：鉴权、分页、错误展开
  markup/       正文转换 wiki ↔ Markdown
  jql.py        链式 JQL builder
  fields.py     字段裁剪、展平、名称→id 解析
  output.py     yaml/json/table/md
  config.py     命令行 > 环境变量 > 配置文件，无多 profile
  timefmt.py    时间戳格式化与时区换算（进程级设置）
  meta_cache.py 元数据缓存    writelog.py 写操作留痕    errors.py 错误→可修复提示
```

**Cloud 只做架构预留，本期不实现。** 差异收敛在 `client.Backend` 与 `markup.Codec` 两个抽象里，加 Cloud = 新增 `CloudBackend` + `AdfCodec`，不动其余代码。别学参考项目 `ankitpokhrel/jira-cli` 那样到处 `if installation == Local`。

## 非目标

不做 TUI（`rich` 只渲染静态表格和彩色错误）、不做批量写、不做 dry-run、不做 stats、不做敏捷（board/sprint/epic）。写操作只接受**单个** issue key，护栏只有写操作留痕。

## 约定

- **输出必须省 token**：`/search` 带 `fields=` 白名单从源头裁剪；展平嵌套对象、剔除 `null` 与 `self`/`avatarUrls`/`iconUrl`；`issue show` 默认精简，`--comments`/`--history`/`--links`/`--subtasks`/`--custom` 按需叠加
- **错误要给「怎么修」**：流转失败列出可用 transition 及必填字段与可选值，建单失败回吐 `allowedValues`——让调用方一轮自我纠正
- **Jira 不能直接设置状态**，必须走 transition
- **给 `get_issue` 传 `fields=` 时当心运算符优先级**：`fields=a + b if cond else None` 会被解析成 `(a + b) if cond else None`，条件不成立时变成 `fields=None`，等于要全部字段。这个 bug 潜伏过一段时间
- **本机 shell 是 zsh，未加引号的变量不做 word splitting**。冒烟脚本写 `for c in "meta whoami"; do jira-cli $c; done` 会把整串当成一个参数，命令全挂却看起来像代码坏了
- `config.toml` 含 PAT，权限 600，已在 `.gitignore`

## 参考文档（按需 Read）

- 改动 `markup/`、正文转换出问题：`docs/claude/markup.md`
- 改动附件下载、缓存或体积闸门：`docs/claude/attachments.md`
- 调用返回成功但结果不对、涉及 Jira 行为怪癖与目标实例实测结论：`docs/claude/jira-quirks.md`
- 架构决策与当初的实测证据：`docs/superpowers/specs/2026-08-25-jira-cli-design.md`
