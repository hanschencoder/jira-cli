#!/usr/bin/env bash
#
# jira-cli 卸载脚本。
#
#   curl -fsSL https://raw.githubusercontent.com/hanschencoder/jira-cli/main/uninstall.sh | bash
#
# 移除：① jira-cli 命令；② skill 正本及各工具软链；③ 配置（含 PAT）。
#
set -euo pipefail

SKILL_ROOT="${JIRA_CLI_SKILL_DIR:-$HOME/.agents/skills}"

# 各工具 skill 目录（与 install.sh 的 LINK_DIRS 保持一致）
LINK_DIRS=(
  "$HOME/.claude/skills"    # Claude Code
  "$HOME/.cursor/skills"    # Cursor
  "$HOME/.codex/skills"     # Codex
  "$HOME/.gemini/skills"    # Gemini CLI
  "$HOME/.copilot/skills"   # GitHub Copilot CLI
)

info() { printf '\033[32m==>\033[0m %s\n' "$*"; }

# ---- 卸载 CLI ----
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^jira-cli'; then
  info "卸载 jira-cli 命令 ..."
  uv tool uninstall jira-cli || true
else
  info "未检测到 uv 安装的 jira-cli，跳过（如用 pip 装请手动 pip uninstall jira-cli）"
fi

# ---- 删除各工具软链 ----
for dir in "${LINK_DIRS[@]}"; do
  link="$dir/jira-cli"
  if [ -e "$link" ] || [ -L "$link" ]; then
    rm -rf "$link"
    info "已移除 $link"
  fi
done

# ---- 删除 skill 正本 ----
if [ -e "$SKILL_ROOT/jira-cli" ]; then
  rm -rf "$SKILL_ROOT/jira-cli"
  info "已移除 skill 正本 $SKILL_ROOT/jira-cli"
fi

# ---- 删除配置（含 PAT）与缓存（含已下载的附件）----
# 卸载即彻底清理，默认删除所有平台可能的配置目录。
CONFIG_DIRS=(
  "${JIRA_CLI_CONFIG_DIR:-}"
  "$HOME/.config/jira-cli"                          # Linux
  "$HOME/Library/Application Support/jira-cli"      # macOS
  "${JIRA_CLI_CACHE_DIR:-}"
  "$HOME/.cache/jira-cli"                           # Linux（元数据缓存与下载的附件）
  "$HOME/Library/Caches/jira-cli"                   # macOS
)
removed=0
for d in "${CONFIG_DIRS[@]}"; do
  if [ -n "$d" ] && [ -d "$d" ]; then
    rm -rf "$d"
    info "已删除配置（含 PAT）$d"
    removed=1
  fi
done
[ "$removed" -eq 0 ] && info "未找到配置目录，无需清理"

info "卸载完成 ✅"
