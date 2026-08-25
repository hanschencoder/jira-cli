#!/usr/bin/env bash
#
# jira-cli 一键安装脚本。
#
#   curl -fsSL https://raw.githubusercontent.com/hanschencoder/jira-cli/main/install.sh | bash
#
# 完成两件事：
#   1. 用 uv 安装 jira-cli 命令
#   2. 安装配套 skill 到 ~/.agents/skills/jira-cli，并为已安装的各 AI 工具
#      （Claude Code / Cursor / Codex / Gemini / Copilot）的 skill 目录建立软链接
#
set -euo pipefail

REPO_URL="${JIRA_CLI_REPO:-https://github.com/hanschencoder/jira-cli.git}"
SKILL_ROOT="${JIRA_CLI_SKILL_DIR:-$HOME/.agents/skills}"

# 需要软链到此正本的各工具 skill 目录（仅当其父目录已存在，即该工具已安装时才建链）。
# 要支持新工具加一行即可。
LINK_DIRS=(
  "$HOME/.claude/skills"    # Claude Code
  "$HOME/.cursor/skills"    # Cursor
  "$HOME/.codex/skills"     # Codex
  "$HOME/.gemini/skills"    # Gemini CLI
  "$HOME/.copilot/skills"   # GitHub Copilot CLI
)

info() { printf '\033[32m==>\033[0m %s\n' "$*"; }
err()  { printf '\033[31m错误:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 依赖检查 ----
command -v git >/dev/null 2>&1 || err "需要 git"
command -v uv  >/dev/null 2>&1 || \
  err "需要 uv，请先安装：curl -LsSf https://astral.sh/uv/install.sh | sh"

# ---- 克隆仓库到临时目录 ----
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
info "克隆仓库 $REPO_URL ..."
git clone --depth 1 "$REPO_URL" "$TMP/jira-cli"

# ---- 安装 CLI ----
info "用 uv 安装 jira-cli ..."
uv tool install --force "$TMP/jira-cli"

# ---- 安装 skill（正本）----
info "安装 skill 到 $SKILL_ROOT/jira-cli ..."
mkdir -p "$SKILL_ROOT"
rm -rf "$SKILL_ROOT/jira-cli"
cp -r "$TMP/jira-cli/skills/jira-cli" "$SKILL_ROOT/jira-cli"

# ---- 为各工具 skill 目录建软链（失败则回退为复制）----
# 仅当工具的 skill 父目录已存在（即该工具已安装）时才处理，避免产生无效目录。
for dir in "${LINK_DIRS[@]}"; do
  parent="$(dirname "$dir")"
  [ -d "$parent" ] || continue          # 工具未安装，跳过
  mkdir -p "$dir"
  link="$dir/jira-cli"
  rm -rf "$link"                         # 移除同名旧条目（软链/目录/文件）
  if ln -s "$SKILL_ROOT/jira-cli" "$link" 2>/dev/null; then
    info "已链接 $link -> $SKILL_ROOT/jira-cli"
  else
    # 软链失败（如 Windows 无权限），回退为复制
    cp -r "$SKILL_ROOT/jira-cli" "$link"
    info "已复制 skill 到 $link（软链不可用，已回退为复制）"
  fi
done

# ---- 收尾提示 ----
info "安装完成 ✅"
echo
echo "下一步："
echo "  1. 配置连接（交互式，会自动探测部署形态与正文渲染器）："
echo "       jira-cli config init"
echo "  2. 验证："
echo "       jira-cli meta whoami"
echo
echo "提示："
echo "  1. uv 安装的 jira-cli 路径在 ~/.local/bin/jira-cli，请确保它在你的 PATH 中。可通过以下命令配置: uv tool update-shell"
echo "  2. PAT 在 Jira 页面右上角头像 →「个人设置」→「Personal Access Tokens」处创建"
echo "  3. skill 正本在 ~/.agents/skills/jira-cli，已为已安装的 AI 工具（Claude Code / Cursor / Codex / Gemini / Copilot）软链到各自 skill 目录（重启工具后生效）"
echo "  4. Enjoy! 🎉"
