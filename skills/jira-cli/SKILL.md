---
name: jira-cli
description: 用 jira-cli 命令行查询、创建、更新 Jira issue，下载附件。当用户要配置 Jira 连接、查自己或他人或某项目的 issue、看 issue 详情、改状态、加评论、建 issue、下载 issue 附件时使用。
---

# 使用 jira-cli

`jira-cli` 封装 Jira REST API（Server / Data Center，REST v2）。结构化结果走 stdout，提示与错误走 stderr，两者不混。

不确定有哪些命令时先跑 `jira-cli commands`；任何命令加 `-h` 看参数。

## 失败时先看退出码

退出码决定下一步该做什么，不要一律重试：

| 码 | 含义 | 你该做的 |
|---|---|---|
| 2 | 缺 url / token，或配置文件损坏 | 引导用户配置（见第 0 节），重试无用 |
| 3 | 鉴权失败（401/403） | PAT 过期或无权限，让用户换 token，重试无用 |
| 4 | Jira 返回其它错误 | 读 stderr，多半是字段值不合法 |
| 5 | 项目 / 类型 / 状态 / 用户的名称解析不到 | stderr 列出了可选值，照着改一次 |
| 6 | 流转失败 | stderr 列出了可用流转及必填字段，照着补一次 |

**退出码 5 和 6 的 stderr 自带可选值清单，够你一轮改对，不必再发一次探查请求。**

## 0. 先确认连接

没有 url + token 时任何命令都跑不了。第一步永远是 `jira-cli config get`（显示当前 url 和脱敏 token）。

未配置时引导用户二选一：

```bash
# 方式 A：交互式引导（推荐）。自动探测部署形态与正文渲染器，并让用户选默认项目
jira-cli config init

# 方式 B：环境变量（临时 / CI）
export JIRA_URL=https://jira.example.com
export JIRA_TOKEN=<用户的 PAT>
```

- **PAT 获取**：Jira 页面右上角头像 →「个人设置」→「Personal Access Tokens」→ Create token
- 优先级：命令行参数 > 环境变量 > 配置文件；无多 profile，临时换实例用 `--url` / `--token` 覆盖
- 配置里的 `default-project` 很关键：设了之后 `issue list` / `issue create` 不带 `--project` 就走它
- 时间戳输出形如 `2026-08-25 11:31:57.000`，已换算到配置时区（默认东八区）

## 1. 选输出格式

`-o` 可后置在命令末尾，如 `jira-cli issue list --assignee me -o yaml`。

| 场景 | 用什么 |
|---|---|
| **你自己读** | `-o yaml`（噪音最少最省 token）；要按字段精确提取时 `-o json` 配 `jq` |
| **展示给用户** | 终端直接看用默认 `-o table`；放进回复或文档用 `-o md` |

格式名拼错会直接报错，不会静默降级。嵌套结构（`issue show`、`meta createmeta`）指定 `-o table` 会自动降级成 yaml。

```bash
jira-cli issue list --project ABC -o json | jq '.issues[] | {key, status, summary}'
jira-cli issue show ABC-1 -o json | jq -r '.description'
```

## 2. 铁律

1. **先查 meta 再操作**。不确定项目 / 类型 / 状态 / 字段 / 用户的写法时，先跑对应的 `jira-cli meta ...`；建 issue 前必须先 `meta createmeta` 查必填字段。
2. **项目 KEY 才是稳定标识**。KEY（`ABC`，即 issue 编号 `ABC-123` 的前缀）不变，名称（`示例项目`）可随时改。`--project` 两者都收，但**回填给用户看时用 KEY**。
3. **不能直接「设置状态」**，必须走工作流定义的 transition。用 `issue transition`，不要试图 `issue update -f status=...`。
4. **写操作不可批量、不可撤销**。`update` / `transition` / `comment` 一次只接受**一个** issue key。写操作直接进生产、会真的发通知。
5. **绝不带占位符执行写操作**。summary、description、每个 `-f` 的值都必须是用户确认过的真实内容；不确定就先问，或据同类 issue 生成草稿供 review。给用户展示命令模板时，占位符用明显非法的写法（如 `『在这里填』`），防止被整段复制执行。
6. **正文一律写 Markdown**，工具自动转成 wiki markup，不要自己写 wiki 语法。
7. **附件只能用 `issue download` 拿**，不要去翻 `content` 字段然后 `curl` / WebFetch（见第 7 节）。
8. **`me` 指当前 token 用户**，用于 `--assignee me`、`--reporter me`。

## 3. 常用命令速查（照抄即正确）

| 任务 | 命令 |
|---|---|
| 列全部命令 | `jira-cli commands` |
| 查配置 | `jira-cli config get` |
| 查我名下未完成 issue | `jira-cli issue list --assignee me --status open -n 30 -o yaml` |
| 查项目全部 issue | `jira-cli issue list --project ABC -o yaml` |
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
| 查项目列表 | `jira-cli meta projects -o yaml` |
| 查类型 / 状态 / 优先级 | `jira-cli meta issuetypes`、`jira-cli meta statuses`、`jira-cli meta priorities` |
| 查用户登录名 | `jira-cli meta users 张三 -o yaml` |
| 查自定义字段 id | `jira-cli meta fields 严重程度 -o yaml` |
| 回查自己做过的写操作 | `jira-cli log -n 20` |

`--fields` 用的是**输出里看到的字段名**（`type`、`due`、`fix_versions`），不是 Jira 内部名。先跑一次不带 `--fields` 的 `issue show` 看有哪些 key，再挑。

## 4. `issue list` 的筛选参数

- `--project` / `-p` 项目 KEY 或名称均可，不给则用 `default-project`。用名称匹配上时 stderr 会说明落到了哪个项目
- `--assignee` / `-a` 经办人**登录名**（不是显示名），`me` 表示自己。登录名用 `meta users` 查
- `--reporter` 报告人，同上
- `--status` / `-s` 状态名；`open` / `closed` 是简写，`*` 表示不过滤
- `--type` / `-t` 类型名，`--priority` 优先级名
- `--label` / `-l` 标签，可多次传（多个之间是 OR）
- `--summary` 标题包含关键词
- `--created` / `--updated` 三种写法：`>=-7d`、`<=2026-05-31`、`2026-05-01|2026-05-31`
- `--jql` 原始 JQL，与上面的参数 **AND 合并**，不是二选一
- `--sort` 如 `updated:desc`。不给且 `--jql` 里也没 `ORDER BY` 时默认 `updated:desc`
- `--limit` / `-n` 最多返回条数，默认 50，**最小 1**

两个硬性约束：

- **至少给一个筛选条件**，否则报错拒绝执行（防止扫描全站）
- **含 `>` `<` `|` `*` 的取值必须套单引号**，否则会被 shell 当成重定向或通配符：`--updated '>=-7d'`、`--match '*.log'`

结果被截断时 stderr 会提示匹配总数，按需调大 `-n`。JQL 写法见 `references/jql.md`。

## 5. 改状态

能从当前状态走到哪些状态由工作流固定，不能任意设置。

```bash
# 1. 先看能流转到哪（也可直接跳到第 2 步，失败时错误信息会告诉你）
jira-cli meta transitions ABC-1 -o yaml

# 2. 按名称流转。流转名和目标状态名都能匹配，支持唯一子串
jira-cli issue transition ABC-1 '完成'

# 3. 带必填字段 / 同时加评论
jira-cli issue transition ABC-1 '完成' -f resolution=Done --comment '已验证通过'
```

**匹配不上或缺必填字段时（退出码 6），stderr 会列出当前可用的全部流转、各自的必填字段及可选值**，照着补一次即可，不需要再单独跑 `meta transitions`。

## 6. 建 issue

```bash
# 1. 先查该项目 + 该类型的必填字段和可选值
jira-cli meta createmeta --project ABC --type Bug -o yaml

# 2. 建单。描述写 Markdown，工具自动转成 wiki markup
jira-cli issue create --project ABC --type Bug \
  --summary '登录页在弱网下白屏' \
  --description '## 复现步骤

1. 打开 **登录页**
2. 限速到 100 Kbps

## 期望 vs 实际

| 项 | 期望 | 实际 |
| --- | --- | --- |
| 首屏 | 3s 内 | 白屏 |
' \
  --assignee zhang.san --priority High --label regression \
  -f 严重程度=Major --attach ./screenshot.png -o yaml
```

- `-f name=value` 可多次传，`name` 用字段显示名或字段 id（`customfield_10001`）都行
- 多选字段的多个取值用逗号分隔：`-f 影响模块=登录,支付`
- 列表型字段的取值必须与 `createmeta` 里的 `allowed` **逐字一致**
- 建单失败（退出码 4）时 stderr 会附上查 `createmeta` 的命令
- `--attach` 可多次传。注意 **`-a` 是 `--assignee` 的短参**，不是附件

`issue update` 的字段语义相同。`--label` 是**覆盖**不是追加；不传的字段则保持不变。

## 7. 附件

**两步：先 `attachments` 看清单，再 `download` 落盘。**

```bash
jira-cli issue attachments ABC-123 -o yaml
```

```yaml
attachments:
- id: '6055369'          # download --id 用这个
  filename: data.csv
  size: 14               # 字节。决定要不要下的关键
  mime: text/csv
```

**这一步的意义是看 `size`。** 生产 issue 上挂几百 MB 的日志包、几 GB 的分卷很常见，不看大小直接全下会拖垮磁盘和时间。

```bash
jira-cli issue download ABC-123 --match '*.log' -o yaml   # 按文件名 glob 过滤，排查日志最常用
jira-cli issue download ABC-123 --id 6055369 -o yaml      # 按 id 精确要某一个
jira-cli issue download ABC-123 --dir /tmp/logs -o yaml   # 指定目录（平铺，不再套 <KEY>/ 那层）
jira-cli issue download ABC-123 --force -o yaml           # 忽略本地缓存强制重下
```

`--match` 与 `--id` 可叠加。要点：

- **用输出里的 `files[].path` 去读文件**（本地绝对路径），不要自己拼路径。不带 `--dir` 时落点是固定的缓存目录，与当前工作目录无关，不会把附件撒进用户的代码仓库
- **下载前自动检查本地缓存**：同路径且大小一致的直接跳过并标 `cached: true`，所以对同一 issue 反复跑很便宜。本地文件被截断时会自动重下
- **没有匹配时不报错**，返回 `downloaded: 0` 且 `files: []`，脚本不会因空结果中断
- **单次实际要下载的量超过 200 MB 会拒绝执行**（退出码 1），stderr 列出清单和三种处理方式。已命中缓存的不计入。**优先用 `--match` / `--id` 缩小范围，而不是无脑加 `-y`**——用户多半只要日志，不要那几个 500 MB 的视频分卷
- **不要试图用 URL 下载**。附件地址必须带 `Authorization` 头，`curl` 或 WebFetch 只会拿到 401 或登录页，白白浪费一轮

拿到文件之后：文本 / 日志直接 Read，**超过几 MB 先 `grep -n` 定位行号再局部读**；图片 Read 可以直接看；压缩包先 `unzip -l` / `7z l` 看清单再解需要的部分。

上传用 `jira-cli issue update ABC-123 --attach ./report.html`，`--attach` 可多次传。

## 8. 正文格式

读出来的 `description` 和评论正文都已转成 **Markdown**；写进去也传 Markdown，工具负责转成 wiki markup。标题、粗体、删除线、代码块、嵌套列表、表格、引用、链接、图片都支持。

转换器在边界情况出问题时，有逃生舱直传 wiki 原文：

```bash
jira-cli issue create ... --description-raw 'h2. 标题
{code:java}int a = 1;{code}'
jira-cli issue comment ABC-1 'bq. 引用' --raw
```

## 参考文档

- `references/workflows.md`：典型多步工作流（分析 issue、建单、改状态、下附件排查）
- `references/output-format.md`：各命令输出字段说明
- `references/jql.md`：JQL 速查，降级到 `--jql` 时用

## 其它

- `meta projects` / `issuetypes` / `statuses` / `priorities` / `fields` 默认读本地缓存（7 天有效期），要最新数据加 `--refresh` 或跑 `jira-cli meta update` 清缓存
- 所有写操作都记在本地留痕日志里，`jira-cli log` 可回查
