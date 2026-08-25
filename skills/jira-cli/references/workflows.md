# 典型工作流

每条都是「先查清楚 → 确认 → 再动手」。写操作不可批量、不可撤销，多查一次永远比改错一条便宜。

## 1. 分析一个 issue 的根因（含附件排查）

```bash
# 1. 拿全貌：详情 + 评论 + 变更历史
jira-cli issue show ABC-123 --comments --history -o yaml

# 2. 看有没有附件，重点看 size
jira-cli issue attachments ABC-123 -o yaml

# 3. 挑需要的下载
jira-cli issue download ABC-123 --match '*.log' --dir ./logs -o yaml

# 4. 用输出里的 path（绝对路径）读文件
grep -n "FATAL\|Exception" ./logs/*.log | head -50
```

拿到 `description`（已是 Markdown）后再结合代码仓库定位。**历史（`--history`）常常比描述更有信息量**——能看出它被打回过几次、在谁手上停留最久。

### 附件排查的三条纪律

1. **先 `attachments` 看 `size` 再 `download`。** 生产 issue 上几 GB 附件很常见（实测见过单条 issue 合计 2.54 GB、另一条挂 132 个附件）。超过 200 MB 时 `download` 会拒绝执行并列出清单——**这时应该用 `--match` / `--id` 缩小范围，而不是直接加 `-y`**。用户通常只要日志，不要那些 500 MB 的视频分卷。
2. **大文件先 `grep -n` 定位再局部读**，不要整个文件塞进上下文。
3. **绝不要拿 URL 去 curl / WebFetch。** Jira 附件必须带认证头，裸链接只会得到 401 或登录页。只能用 `issue download`。

### 按类型处理下载到的文件

| 类型 | 做法 |
|---|---|
| 日志 / 文本 | 小的直接 Read；大的先 `grep -n` 定位行号再按行区间读 |
| 图片 / 截图 | Read 可直接查看 |
| 压缩包 | 先 `unzip -l` / `7z l` 看清单，只解需要的部分 |
| trace / 性能数据 | 先确认体积，必要时用专门工具而不是直接读 |

**下载落点默认是固定的缓存目录** `~/.cache/jira-cli/attachments/<KEY>/`，与当前工作目录无关，不会撒进用户的代码仓库。同一 issue 反复下载会命中本地缓存（同路径且大小一致则跳过，标 `cached: true`），所以重复跑很便宜；要重下加 `--force`。

### 分析完回写附件

```bash
jira-cli issue update ABC-123 --attach ./analysis.md --attach ./flamegraph.svg
```

`--attach` 可多次传。**`-a` 是 `--assignee` 的短参，不是附件。**

## 2. 查一批 issue 并归类

```bash
# 1. 先小范围试，确认 JQL 和数据符合预期
jira-cli issue list --project ABC --status open -n 5

# 2. 输出里的 jql 字段是实际执行的语句，不对就调参数
# 3. 确认无误后拉全量，用 yaml 省 token
jira-cli issue list --project ABC --status open -n 200 -o yaml
```

要按字段分组统计时用 `-o json` 配 `jq`：

```bash
jira-cli issue list --project ABC --status open -n 200 -o json \
  | jq -r '.issues[] | .priority' | sort | uniq -c | sort -rn
```

本工具**不提供 stats 子命令**，统计一律走 `jq` 或你自己汇总。

## 3. 建一个 issue

```bash
# 1. 确认项目 key
jira-cli meta projects -o yaml

# 2. 确认类型名（不同项目可用类型不同）
jira-cli meta createmeta --project ABC -o yaml | head -40

# 3. 查该项目 + 该类型的必填字段及可选值。这一步不能省
jira-cli meta createmeta --project ABC --type Bug -o yaml

# 4. 要指派的话，查登录名（不是显示名）
jira-cli meta users 张三 -o yaml

# 5. 把完整命令给用户 review，确认后再执行
jira-cli issue create --project ABC --type Bug \
  --summary '真实标题' \
  --description '真实的 Markdown 描述' \
  --assignee zhang.san \
  -f 严重程度=Major \
  -o yaml

# 6. 执行后把返回的 url 贴给用户
```

**`allowed` 里的取值必须逐字照抄**，多一个空格、全半角不一致都会导致 400。

失败时错误信息会带上 `meta createmeta` 的命令，照着跑一次就能看到缺什么。

## 4. 改状态

```bash
# 1. 看当前状态
jira-cli issue show ABC-123 --fields status,resolution -o yaml

# 2. 直接按名称流转。流转名和目标状态名都能匹配
jira-cli issue transition ABC-123 '完成'

# 3. 失败时错误信息会列出全部可用流转及必填字段，照着补
jira-cli issue transition ABC-123 '完成' -f resolution=Done

# 4. 核对状态已变
jira-cli issue show ABC-123 --fields status,resolution -o yaml

# 5. 把 url 贴给用户
```

不必先跑 `meta transitions`——直接试，失败时的错误信息就是那份清单。真想先看也可以：

```bash
jira-cli meta transitions ABC-123 -o yaml
```

**目标状态一步到不了时不要自己找多跳路径**。Jira 的工作流是有意设计的，跨级流转会产生用户没预期的中间状态变更记录。告诉用户当前只能到哪几个状态，让用户决定。

## 5. 修完 bug 回写

```bash
# 1. 加说明性评论（Markdown）
jira-cli issue comment ABC-123 '已修复。

根因：`SyncManager#run` 里的线程竞争，并发写同一个 buffer。
方案：改用 `ConcurrentLinkedQueue`。

提交：abc1234'

# 2. 有产物就传上去
jira-cli issue update ABC-123 --attach ./test-report.html

# 3. 流转状态，顺带一条结论
jira-cli issue transition ABC-123 '完成' --comment '自测通过，已合入 release/2.0'
```

`--comment` 会在流转成功后单独发一条评论，保证一定写得进去。

## 6. 批量场景怎么办

本工具**故意不支持批量写**。要改多条时：

```bash
# 1. 先用 list 筛出目标，把条数和 key 列给用户确认
jira-cli issue list --project ABC --jql 'labels = stale AND updated <= -90d' -o yaml

# 2. 用户确认后，逐条执行
for k in ABC-1 ABC-2 ABC-3; do
  jira-cli issue transition "$k" '关闭' -f resolution='无法复现'
done

# 3. 回查留痕确认结果
jira-cli log -n 20
```

循环前**必须**把 key 清单给用户确认过。JQL 范围判断失误会一次性改错一大片，且不可撤销。

## 7. 出问题了怎么查

```bash
jira-cli config get          # 连的哪个实例、token 是否还在
jira-cli meta whoami         # token 对应谁、是否还有效
jira-cli log -n 20           # 我刚才到底改了什么
jira-cli issue show KEY --raw -o json    # 裁剪逻辑可疑时看原始数据
jira-cli meta update         # 元数据像是过期了就清缓存
```

正文转换器出边界情况时（读回来的 Markdown 明显不对），用 `--raw` 看 Jira 里实际存的 wiki 原文；写入时用 `--description-raw` / `--raw` 绕过转换器直接传 wiki。
