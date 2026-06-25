# task-manager

Claude Code **Stop hook**：每輪「使用者說完＋AI 回完」後，背景讀取本 session 最新一輪純對話
（只取 user/assistant 的 text，跳過 tool 輸出），用 **DeepSeek V4 Flash (effort high)** 判斷要對
任務／問題清單做哪些變化，由 Python deterministically 套用到 per-item JSON。

## 8 個標籤
- 任務（狀態機）：`todo` → `in_progress` → `need_verify` → `done`
- 問題（記原因）：`backlog`（為何不做）、`question`（待你決定）、`workaround`（妥協/應急）、`issue`（失敗議題）

## 檔案佈局（每個 Claude session 一個資料夾）
```
~/task-manager/
  <session名>-<session_id>/      # 例：公司網頁加強-fcacfcb9-1124-...
    active/   T-XXX.json   # todo/in_progress/need_verify/question/workaround/issue
    archive/  T-XXX.json   # done/backlog（不再餵 LLM）
    BOARD.md               # 人看的彙整看板（Python 自動生成）
    ops.jsonl              # 每輪 LLM 操作稽核 log（含 latency / cache usage）
    state.json             # 處理進度（last_uuid，避免重複處理）
    worker.log             # 背景執行 log
```
- session 名取自 transcript 的 `custom-title`；session_id 取自 payload 或 transcript 檔名。
- 用 session_id 後綴比對重用資料夾：session 改名也認得同一個，不會另開、不丟狀態。
- 每個 session 各自獨立（board 不跨 session 累積）。跨 session 記憶請另用 claude-mem 之類。

## 防 context rot 設計
LLM 每輪只看到「active 項目精簡索引（id/tag/title）」+ 最新對話；detail 永不進 context，
done/backlog 歸檔後完全不餵。active 累積再多，進 LLM 的也只是每條約 15 token 的索引。
「找檔／定位／改 tag／搬檔／蓋時間（+08）／產 id」全由 Python 做，零 LLM 幻覺。

## 省錢設計（context caching）
固定的分類規則／schema／few-shot 全放 `system` 並標 `cache_control: ephemeral`，
DeepSeek 對相同前綴快取（~896 token），每輪只付 ~150 個變動 token 全價。

## 開關
- 關閉：編輯 `~/.claude/settings.json`，從 `hooks.Stop` 移除 `task-manager/hook.sh` 那筆。
- 模型：改 `tracker.py` 的 `DS_MODEL`（目前 `deepseek-v4-flash`）。
- 時區：`tracker.py` 的 `TZ8`（目前 +08）。

## 注意
- 每個 Stop 觸發一次（背景、不阻塞；首呼 ~10s、之後 ~2–4s）。
- 純閒聊/解釋會被規則濾掉、不建項目；判斷由 DeepSeek 做。
- key 兜底：env 沒 `DEEPSEEK_API_KEY` 時 hook.sh 會從 `~/.zshrc` 撈。
