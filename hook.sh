#!/bin/bash
# task-manager Stop hook 入口。
# 讀 hook 的 JSON（含 transcript_path / cwd）→ fork 背景工作者 → 立刻 return，絕不阻塞對話。
input=$(cat)
DIR="$HOME/.claude/hooks/task-manager"

# DEEPSEEK_API_KEY 由 env 提供（hook 為 Claude Code 子程序，自動繼承其環境變數）。
# 不解析任何 shell rc 檔；若 env 沒有，tracker.py 會在 worker.log 記明確錯誤。

# 背景執行；detach（nohup + & + disown），輸出導到 log。把 hook JSON 由 stdin 餵給 python。
nohup python3 "$DIR/tracker.py" >>"$DIR/worker.log" 2>&1 <<HOOK_EOF &
$input
HOOK_EOF
disown 2>/dev/null

exit 0
