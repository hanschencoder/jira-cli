# 正文转换（改 `markup/` 前读）

对外约定：**AI 读到的和写入的都是 Markdown**，wiki markup 只存在于 `markup/` 内部。

## 两个方向手段不同

| 方向 | 实现 | 为什么 |
|---|---|---|
| wiki → Markdown（读） | `jira2markdown` 库 | 难的一半：要解析任意 wiki 语法。`ankitpokhrel/jira-cli` 为此手写了 597 行 parser |
| Markdown → wiki（写） | `markdown-it-py` 出 AST + 自写 `JiraWikiRenderer` | 简单的一半：AI 生成的 Markdown 是可预期子集 |

**md→wiki 绝不用正则替换。** 正则会在嵌套列表、表格内联代码、元字符转义上翻车。参考项目源码注释：`'*' can be either be bold or an unordered list`。

## 输入归一化（`normalize_jira`）

喂给 `jira2markdown` 前必须做，两处都是实测踩到的：

1. **CRLF**——该实例返回 `\r\n`，jira2markdown 的表格解析器不认 `\r`，残留的 `\r` 会变成幻影列，导致表格永不终止、把后续所有段落吞进最后一个单元格（数据损坏级）。
2. **`{*}bold{*}`**——Jira 富文本编辑器产出的转义写法，jira2markdown 0.5.1 不认，会原样吐出 `{**}bold{**}`。

## 转义与嵌套

- **正文中的 wiki 元字符（`{} [] | * _ - + ^ ~`）必须转义**，否则普通文本会被误解析成标记。`escape_text` 对 `{}[]|` 无条件转义，对 `*_-+^~` 只在可能开启标记的位置转义（避免把 `snake_case`、`jira-cli` 转得满是反斜杠）。
- **嵌套列表前缀要反映祖先链类型**：有序里嵌无序是 `#*`，不是 `**`。见 `_marker_prefix`。

## 逃生舱

转换器出边界情况时不阻塞流程：`--description-raw`（建单/更新）与 `--raw`（评论）直接提交 wiki 原文。
