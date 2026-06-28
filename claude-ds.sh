# ── claude-ds：DeepSeek V4 Flash 後端，相容 Claude Code ──
# 用法：echo '\n# claude-ds' >> ~/.bashrc && cat claude-ds.sh >> ~/.bashrc
# 然後 source ~/.bashrc 或重開終端，打 claude-ds 或 claude-ds-pro
# 前提：~/.zshrc 或 ~/.bashrc 有 DEEPSEEK_API_KEY

claude-ds() {
  kill $(lsof -ti :8799) 2>/dev/null
  nohup python3 "$HOME/.claude/hooks/task-manager/ds-vision-proxy.py" >/tmp/ds-vision-proxy.log 2>&1 &
  sleep 1
  ANTHROPIC_BASE_URL="http://127.0.0.1:8799" \
  ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY" \
  ANTHROPIC_MODEL="deepseek-v4-flash[1m]" \
  ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-flash[1m]" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-flash[1m]" \
  ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-v4-flash[1m]" \
  CLAUDE_CODE_SUBAGENT_MODEL="deepseek-v4-flash[1m]" \
  CLAUDE_CODE_EFFORT_LEVEL="max" \
  claude "$@"
}

claude-ds-pro() {
  kill $(lsof -ti :8799) 2>/dev/null
  nohup python3 "$HOME/.claude/hooks/task-manager/ds-vision-proxy.py" >/tmp/ds-vision-proxy.log 2>&1 &
  sleep 1
  ANTHROPIC_BASE_URL="http://127.0.0.1:8799" \
  ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY" \
  ANTHROPIC_MODEL="deepseek-v4-pro[1m]" \
  ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-pro[1m]" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-pro[1m]" \
  ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-v4-pro[1m]" \
  CLAUDE_CODE_SUBAGENT_MODEL="deepseek-v4-pro[1m]" \
  CLAUDE_CODE_EFFORT_LEVEL="max" \
  claude "$@"
}
