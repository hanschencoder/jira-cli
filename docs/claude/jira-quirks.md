# Jira 与目标实例的反直觉行为

都是实测结论，不是从文档推的。碰到「调用成功但结果不对」时先翻这里。

## 目标实例

实例地址与 PAT 只在本地配置（`~/.config/jira-cli/config.toml`），不入库。

- **Jira Server 8.20.11**，`deploymentType: "Server"`
- **REST API v2**——Server 没有 v3，别写 `/rest/api/3/`
- 认证走 **PAT / Bearer**
- `description` 字段是 **Wiki Style Renderer**（原始 `*粗*` 渲染成 `<b>`、`|a|b|` 渲染成 `<table>`）
- `comment` 字段的渲染器未能实测确认（采样到的评论都是纯文本），按 wiki 处理，由 `config init` 运行时探测兜底
- 规模参考：14 个可访问项目、11.7 万条有描述的 issue
- **多节点部署**（响应头带 `X-ANODEID`），这是下面「id 不与时间同序」的根因

## 静默失败（最危险的一类）

调用返回 2xx，但事情没发生：

1. **transition 的 `update.comment` 会被丢弃**——该 transition 的界面若没配「评论」字段，Jira 返回成功但评论根本没写入。所以流转带评论必须**流转成功后单独发一次 `add_comment`**。
2. **`createmeta` 拿到项目名时返回空 `issuetypes` 列表**而非报错，看起来像「该项目没有 issue 类型」。所以项目标识必须先归一化成 KEY。
3. **错误响应可以没有 body**（反代吐的 403/502 尤其常见）。`_handle` 里「空 body 就返回 None」这条判断必须排在状态码判断**之后**，否则错误被当成「成功但没数据」吞掉。

## 标识与唯一性

- **项目名不保证唯一**，且允许一个项目的名称等于另一个项目的 KEY（JRASERVER-69362，2025-03 以 Low Engagement 关闭，不会修）。`fields.resolve_one` 因此必须**按 id → key → name 逐字段整轮扫描**，不能逐条候选依次比对各字段——后者的命中结果取决于列表顺序。同一轮撞到多个必须报错。
- **JQL 的 `project` 接受项目名，但 `/issue`（建单）和 `/issue/createmeta` 只认 KEY。** `Ctx.resolve_project()` 统一归一化，所有吃 `--project` 的命令都要走它。
- **用户身份模型**：Server 用 `user.name`（登录名），Cloud 用 `user.accountId`。这是写 `Backend` 抽象时的真正语义差异，不是换个端点。

## changelog

- **不含创建事件**，只记录变更。`fields.changelog_rows` 用 `fields.created` + `creator`/`reporter` 合成一条 `field: created` 补上时间线第一格。
- **id 不保证与时间同序**（多节点按块预分配）。同一秒内的两次流转可能被倒序返回，读出来是「To Do → Done → In progress」这种不可能的时间线。`_chain_within_group` 在「同时间戳 + 同字段」组内按 from/to 首尾相接还原顺序；链条有歧义（分叉/成环/缺端点）就保持原样不猜。

## 字段与时间

- **一条 issue 挂 60+ 个自定义字段**，绝大多数是建单预填的模板占位符（`【前提条件】：`、`Please fill in the template below.`），还混着插件塞的 Java 对象 toString。全量输出 4600 字符 vs 精简后 365。所以默认不输出，`issue show --custom` 才给。
- **`parent` 要的是 `{"key": "ABC-1"}`**，不是 `{"name": ...}`。它的 schema type 是 `issuelink`，落不进 `coerce_value` 里任何一个按 type 分派的分支，兜底会把它当普通字符串或按标准字段名包成 `name`——两种都得到一个没有指向性的 400。`_KEY_WRAPPED` 单独拦它。
- **多行文本带 CRLF，且常有行尾空格**。两者都会让 PyYAML 放弃块字面量、退化成 `"步骤一\r\n步骤二 "` 的双引号转义形式，既难读又更费 token。展示路径（`custom_fields`）用 `normalize_newlines` 收敛；description 与评论走 codec 时已经处理过。
- **时间戳格式**：Jira 返回 `2026-08-25T11:31:57.000+0800`——带 T、偏移不带冒号，**Python 3.10 的 `fromisoformat` 不认**。`timefmt.py` 自己解析，对外统一成 `2026-08-25 11:31:57.000` 并换算到配置时区。毫秒按源数据实际有什么显示什么，源里没有就不补 `.000`。
