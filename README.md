# task-manager

Claude Code **Stop hook**：每輪「使用者說完＋AI 回完」後，背景讀取本 session 最新一輪的**真實可見對話**，用 **DeepSeek V4 Flash (effort high)** 判斷要對任務／問題清單做哪些變化，再由 Python deterministically 套用到 per-item JSON。解決長對話中 AI「忘了自己做到哪、版本錯亂」的痛點。

## 安裝（其他機器一鍵）
```bash
git clone https://github.com/dswf65411-new/task-manager.git ~/.claude/hooks/task-manager
bash ~/.claude/hooks/task-manager/install.sh
```
前提：啟動 `claude` 的環境有 `DEEPSEEK_API_KEY`、已裝 `python3`（只用 stdlib）。
裝完重開（或 `claude --resume`）即生效，每輪對話自動寫入 `~/task-manager/<session名>-<id>/`。
`install.sh` 同時會裝查看 skill（見下）。

## 8 個標籤
- 任務（狀態機）：`todo` → `in_progress` → `need_verify` → `done`
- 問題（記原因）：`backlog`（為何現在不做）、`question`（待你決定）、`workaround`（妥協/應急）、`issue`（bug/error/crash/測試失敗/出錯受阻，即使打算稍後修）

每項 per-item JSON：`id / tag / title / detail / created / updated`，時間一律 **+08**。

## 檔案佈局（每個 Claude session 一個資料夾）
```
~/task-manager/
  <session名>-<session_id>/      # 例：公司網頁加強-fcacfcb9-1124-...
    active/   T-XXX.json   # todo/in_progress/need_verify/question/workaround/issue
    archive/  T-XXX.json   # done/backlog（不再餵 LLM）
    BOARD.md               # 人看的彙整看板（Python 自動生成）
    ops.jsonl              # 每輪 LLM 操作稽核 log（含 latency / cache usage）
    state.json             # 處理進度（last_uuid，避免重複處理）
    .seq                   # id 高水位，保證單調遞增、刪除後不重用
    worker.log             # 背景執行 log
```
- session 名取自 transcript 的 `custom-title`；session_id 取自 hook payload 或 transcript 檔名。
- 用 session_id 後綴比對重用資料夾：session 改名也認得同一個，不另開、不丟狀態。
- 每個 session 各自獨立（board 不跨 session 累積）。跨 session 記憶請另用 claude-mem 之類。

## 只記真實可見對話
只取 user/assistant 的 text，並過濾掉：tool_use / tool_result、`isMeta` / `isSidechain`、以及 harness 注入的 `<system-reminder>` / slash 指令展開（`<command-name>` 等）/ `<bash-stdout>`。純閒聊、純解釋、一次性問答會被規則判為「不需追蹤」，不建項目。

## 防 context rot（含「按需讀 detail」兩趟 lazy fetch）
- **第一趟（每輪）**：LLM 只看「active 精簡索引（id/tag/title）」+ 最新對話。新增任務、改 tag/狀態、刪除都在這趟完成；detail 不進 context、done/backlog 歸檔後完全不餵。active 再多，進 LLM 的也只是每條約 15 token 的索引。
- **第二趟（按需）**：當這輪要「改某既有 task 的具體內容/需求」時，第一趟回報 `need_detail:[id]`，Python 才把那 1–2 條完整 detail 餵回去，讓 LLM 在現有內容基礎上改寫（保留舊脈絡、不盲目覆蓋）。純狀態流轉不觸發第二趟。

「找檔／定位／改 tag／搬檔／蓋時間（+08）／產 id」全由 Python 做，零 LLM 幻覺。

## 省錢（context caching）
固定的分類規則／schema／few-shot 全放 `system` 並標 `cache_control: ephemeral`（Anthropic 端點的快取是顯式的，配 header `anthropic-beta: prompt-caching-2024-07-31`）。命中後每輪固定段 ~896 token 走快取價，只付 ~150 個變動 token 全價。

## 查看任務：task-manager-check skill
隨附唯讀查看 skill（`install.sh` 一併裝到 `~/.claude/skills/task-manager-check/`）。
Claude Code 內打 `/task-manager-check` 或「看目前任務」，列出**本 session** 所有非 done 任務標題（依標籤分組）。
底層 deterministic 的 `show.py`：`--detail`（附內容）、`--all`（含 done）、`--tag question`（單一標籤）、`T-003`（單項詳情）。

## 設定 / 開關
- 關閉追蹤：編輯 `~/.claude/settings.json`，從 `hooks.Stop` 移除 `task-manager/hook.sh` 那筆。
- 換模型：改 `tracker.py` 的 `DS_MODEL`（目前 `deepseek-v4-flash`）。
- 改時區：`tracker.py` 的 `TZ8`（目前 +08）。

## 行為注意
- **不抓背景 subagent / workflow 的工作**：hook 刻意過濾 `isSidechain`，所以背景 agent（Task/workflow）內部做的事看不到、不會自動記。若 subagent 做了實質工作（如跑實驗、改檔），**請在主線對話明講做了什麼**，hook 才抓得到；或日後啟用 SubagentStop 捕捉（見下方候選）。
- 每個 Stop 觸發一次（背景、不阻塞；首呼 ~10s、之後 ~2–4s）。
- API key **只看 env**（hook 是 Claude Code 子程序、自動繼承其環境變數）；env 沒有則 `worker.log` 記明確錯誤，不會無聲失敗。
- 已在跑的 session 想掛上：`Ctrl+D` 退出再 `claude --resume`（開新 process 重讀設定、接回 context）。
