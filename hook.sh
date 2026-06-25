#!/bin/bash
# task-manager Stop hook 入口。
# 讀 hook 的 JSON（含 transcript_path / cwd）→ fork 背景工作者 → 立刻 return，絕不阻塞對話。
input=$(cat)
DIR="$HOME/.claude/hooks/task-manager"

# DEEPSEEK_API_KEY 兜底：hook 執行環境不保證帶到，沒有就從 ~/.zshrc 撈出 export 那行。
if [ -z "$DEEPSEEK_API_KEY" ]; then
  export DEEPSEEK_API_KEY=$(grep -oE 'DEEPSEEK_API_KEY="[^"]+"' "$HOME/.zshrc" 2>/dev/null | head -1 | sed -E 's/.*="([^"]+)"/\1/')
fi

# 背景執行；detach（nohup + & + disown），輸出導到 log。把 hook JSON 由 stdin 餵給 python。
nohup python3 "$DIR/tracker.py" >>"$DIR/worker.log" 2>&1 <<HOOK_EOF &
$input
HOOK_EOF
disown 2>/dev/null

exit 0
