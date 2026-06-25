#!/bin/bash
# task-manager 一鍵安裝：把 Stop hook 註冊到本機 ~/.claude/settings.json。
# 用法：把整個 ~/.claude/hooks/task-manager/ 資料夾複製到目標機後，在該機執行：
#   bash ~/.claude/hooks/task-manager/install.sh
set -e
DIR="$HOME/.claude/hooks/task-manager"
SETTINGS="$HOME/.claude/settings.json"

echo "▶ task-manager 安裝程序"

# 1) 必要檔案
for f in tracker.py hook.sh; do
  [ -f "$DIR/$f" ] || { echo "❌ 缺檔：$DIR/$f（請先把整個資料夾複製過來）"; exit 1; }
done
chmod +x "$DIR/hook.sh" "$DIR/tracker.py"
echo "✅ 核心檔案就緒"

# 2) python3
command -v python3 >/dev/null || { echo "❌ 找不到 python3"; exit 1; }
python3 -c "import urllib.request,json,re,sqlite3" 2>/dev/null && echo "✅ python3 stdlib OK"

# 3) 註冊 Stop hook（冪等、先備份）
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d_%H%M%S)"
python3 - "$SETTINGS" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p))
hooks=d.setdefault("hooks",{}); stop=hooks.setdefault("Stop",[])
cmd="$HOME/.claude/hooks/task-manager/hook.sh"
if not any(cmd in h.get("command","") for s in stop for h in s.get("hooks",[])):
    stop.append({"hooks":[{"type":"command","command":cmd}]})
    json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
    print("✅ Stop hook 已註冊")
else:
    print("✅ Stop hook 已存在（略過）")
PY

# 4) API key 檢查
if [ -n "$DEEPSEEK_API_KEY" ]; then
  echo "✅ DEEPSEEK_API_KEY 在環境中"
elif grep -q 'DEEPSEEK_API_KEY=' "$HOME/.zshrc" 2>/dev/null; then
  echo "✅ DEEPSEEK_API_KEY 在 ~/.zshrc（hook 會自動撈）"
else
  echo "⚠️  找不到 DEEPSEEK_API_KEY！請在 ~/.zshrc 加：export DEEPSEEK_API_KEY=\"sk-...\""
fi

# 5) 煙霧測試
echo '{"transcript_path":"/dev/null","session_id":"install-smoke"}' | python3 "$DIR/tracker.py" 2>/dev/null && echo "✅ 煙霧測試通過（空輸入不崩）"

echo
echo "🎉 安裝完成。重開 claude session 後，每輪對話會自動寫入 ~/task-manager/<session名>-<id>/"
