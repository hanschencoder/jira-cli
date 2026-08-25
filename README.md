# jira-cli

命令行工具，调用 Jira REST API **查询、创建、更新** issue，**下载附件**。专为配合 AI 编码助手（Claude Code、Cursor、Codex、Gemini CLI、GitHub Copilot 等）使用而设计——所有命令支持 `-o yaml` / `-o json` 结构化输出，正文统一按 Markdown 读写，错误走 stderr、退出码规范。配套一套 skill 指导这些工具正确调用。

适配 **Jira Server / Data Center**（REST API v2）。

## 为什么不用现成的

[`ankitpokhrel/jira-cli`](https://github.com/ankitpokhrel/jira-cli) 是很成熟的项目，但它服务的是「终端前的人 + shell 脚本」：默认交互式表格，`--plain` 输出给 `awk` 切列的定宽文本，`--raw` 是**未经任何过滤的原始 Jira JSON**（单个 issue 常 50KB+）。没有 YAML、没有字段裁剪、没有附件下载能力。

本项目服务的是**上下文窗口**：

| | ankitpokhrel/jira-cli | 本项目 |
|---|---|---|
| 默认输出 | 交互式 TUI 表格 | 静态表格；AI 用 `-o yaml` |
| 结构化输出 | `--raw` 原始 JSON | 字段白名单裁剪 + 展平 + 去 null |
| issue 详情 | 全量 | 分层，默认精简，按需叠加 |
| 正文格式 | wiki markup 原文 | 统一 Markdown 双向转换 |
| 附件 | 不支持 | 下载落盘 + 输出本地路径清单 |
| 出错时 | 报「失败」 | 回吐可用选项，让 AI 自我纠正 |

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/hanschencoder/jira-cli/main/install.sh | bash
```

脚本会：① 用 uv 安装 `jira-cli` 命令；② 安装配套 skill 到 `~/.agents/skills/jira-cli`，并为已安装的 AI 工具（Claude Code / Cursor / Codex / Gemini / Copilot）软链到各自 skill 目录（重启工具后生效）。

> 适用 **macOS / Linux**（Windows 请在 WSL 或 Git Bash 中运行）。需要 `git` 和 [uv](https://docs.astral.sh/uv/)。
> 装完若提示找不到 `jira-cli`，确认 uv 的 bin 目录在 `PATH` 中（`uv tool update-shell` 可自动配置）。

<details>
<summary>手动安装</summary>

```bash
# 用 uv 装成全局命令（推荐）
uv tool install git+https://github.com/hanschencoder/jira-cli.git

# 或克隆后开发模式
git clone https://github.com/hanschencoder/jira-cli.git && cd jira-cli && uv pip install -e .

# skill 手动安装
cp -r skills/jira-cli ~/.agents/skills/jira-cli
```
</details>

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/hanschencoder/jira-cli/main/uninstall.sh | bash
```

会移除命令、skill 正本与各工具软链，并删除配置（含 token）。

## 配置

```bash
jira-cli config init     # 交互式：填 url + token，自动探测部署类型/渲染器，选默认项目
jira-cli config get      # 查看当前生效参数（token 脱敏）
```

也可手动设置：

```bash
jira-cli config set url https://jira.example.com
jira-cli config set token <你的-PAT>
jira-cli config set default-project ABC
```

**PAT 获取**：Jira 页面右上角头像 →「个人设置」→「Personal Access Tokens」→ Create token。

配置存到平台对应目录（Linux `~/.config/jira-cli`、macOS `~/Library/Application Support/jira-cli`、Windows `%APPDATA%\jira-cli`），文件权限 600；可用 `JIRA_CLI_CONFIG_DIR` 覆盖。

优先级：**命令行参数 > 环境变量（`JIRA_URL` / `JIRA_TOKEN`）> 配置文件**。全局参数还有 `--url`、`--token`、`--insecure/-k`（跳过 TLS 校验）。

## 功能速览

所有命令用 `-o yaml`（推荐，省 token）或 `-o json` 输出结构化结果，可后置在命令末尾；不加则输出人类可读表格。`jira-cli commands` 列出全部子命令。

```bash
# 查询：封装参数
jira-cli issue list --project ABC --assignee me --status Open -o yaml
jira-cli issue list --updated '>=-7d' --sort updated:desc -o yaml

# 查询：复杂条件降级到原始 JQL（两者可共存，AND 合并）
jira-cli issue list --jql 'labels = urgent AND sprint in openSprints()' -o yaml

# 详情：默认精简，按需叠加
jira-cli issue show ABC-123 -o yaml
jira-cli issue show ABC-123 --comments --history -o yaml
jira-cli issue show ABC-123 --custom             # 自定义字段（多是模板默认值，很占 token）
jira-cli issue show ABC-123 --raw            # 原始 JSON 逃生舱

# 创建（描述写 Markdown，自动转 wiki markup）
jira-cli issue create --project ABC --type 任务 \
  --summary "标题" \
  --description '## 复现步骤

1. 打开设置页
2. 点击同步' \
  --attach screencap.png

# 更新 / 评论 / 流转状态（均为单个 issue，不支持批量）
jira-cli issue update ABC-123 --assignee zhang.san -f priority=High
jira-cli issue comment ABC-123 "已定位，是 **线程竞争**"
jira-cli issue transition ABC-123 "已完成" -f resolution=Done

# 附件：列清单 + 下载落盘，输出本地路径供 AI 直接读取
jira-cli issue attachments ABC-123 -o yaml
jira-cli issue download ABC-123 --match '*.log' --dir ./logs

# 元数据（查 id/可选值，默认读本地缓存，jira-cli meta update 刷新）
jira-cli meta projects | issuetypes | statuses | users | fields | whoami
jira-cli meta transitions ABC-123              # 当前可用流转 + 必填字段
jira-cli meta createmeta --project ABC --type Bug   # 建单必填字段 + 可选值

# 写操作留痕回查
jira-cli log -n 20
```

## 重要约定

- **正文统一用 Markdown**。读出来是 Markdown，写进去也传 Markdown，工具内部与 Jira wiki markup 双向转换。转换器出边界情况时，用 `--description-raw`（建单/更新）与 `--raw`（评论） 直接传 wiki 原文。
- **Jira 不能直接「设置状态」**，必须走 transition。`issue transition <KEY> <状态名>` 按名称匹配；匹配不上或缺必填字段时，错误信息会列出当前可用的全部 transition 及其必填字段和可选值。
- **写操作不支持批量、不支持 dry-run**。`update` / `transition` / `comment` 一次只接受一个 issue key。所有写操作记入本地留痕日志，`jira-cli log` 可回查。
- **`--project` 填 KEY 或名称都行**。项目 KEY（`ABC`）是 issue 编号 `ABC-123` 的前缀，是结构性标识；名称（`示例项目`）只是展示名、可随时改。Jira 的 JQL 接受名称但建单接口只认 KEY，本工具统一解析成 KEY，屏蔽这个不一致。
- **先查 meta 再操作**。不确定项目/类型/状态/字段写法时，先 `jira-cli meta …`；建单必填字段用 `meta createmeta` 查。
- **`me` 指当前 token 用户**，用于 `--assignee me`、`--reporter me`。
- **附件必须下载才能读**。Jira 的附件链接要带认证头，直接给 AI 裸链接下不动；用 `issue download` 落盘后读本地路径。

## 架构

```
src/jira_cli/
  cli/         typer 命令层（issue / meta / config / log）
  client.py    Jira REST v2 封装（鉴权、分页、重试、错误展开）
  markup/      正文转换：wiki markup ↔ Markdown
  jql.py       链式 JQL builder（封装参数与 --jql 汇入同一路径）
  fields.py    字段白名单裁剪、展平、名称→id 解析
  meta_cache.py  元数据本地缓存
  config.py    配置加载与优先级
  output.py    yaml / json / table / md 输出
  writelog.py  写操作留痕
  errors.py    Jira 错误 → 可自我修复的提示
skills/jira-cli/   指导 LLM 使用的 skill
install.sh         一键安装脚本
```

Cloud 支持已做架构预留（`Backend` / `Codec` 两个抽象），当前只实现 Server。
