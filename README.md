# task-manager

> Claude Code **Stop hook**：每輪「你說完＋AI 回完」後，背景用 **DeepSeek V4 Flash** 從**真實可見對話**自動抽取待辦／問題，寫進 per-item JSON 任務看板。解決長對話中 AI「忘了自己做到哪、版本錯亂」的痛點。

純本地、純 Python stdlib、零外部依賴。背景執行不阻塞對話。成本極低（實測自動追蹤 ~$0.05/天）。

---

## 安裝（其他機器一鍵）
```bash
git clone https://github.com/dswf65411-new/task-manager.git ~/.claude/hooks/task-manager
bash ~/.claude/hooks/task-manager/install.sh
```
前提：啟動 `claude` 的環境有 `DEEPSEEK_API_KEY`、已裝 `python3`。
`install.sh` 會：註冊 Stop hook 到 `~/.claude/settings.json`、安裝兩個隨附 skill、跑煙霧測試。
裝完**重開**（或 `claude --resume`）即生效，每輪對話自動寫入 `~/task-manager/<session名>-<id>/`。

---

## 8 個標籤
- 任務（狀態機）：`todo` → `in_progress` → `need_verify` → `done`
- 問題（記原因）：`backlog`（為何現在不做）、`question`（待你決定）、`workaround`（妥協/應急）、`issue`（bug/error/crash/失敗/出錯，即使打算稍後修）

每項 per-item JSON：`id / tag / title / detail / created / updated`，時間一律 **+08**。`done`/`backlog` 歸檔到 `archive/`、其餘在 `active/`。

---

## 抽取 pipeline（為什麼準）

每輪對話依序跑（全部 deterministic 或 non-think，背景、快）：

1. **只取真實可見對話**：過濾 tool 輸出、`isMeta`/`isSidechain`、`<system-reminder>`/slash 指令展開/`<bash-stdout>` 等 harness 注入。
2. **deterministic 訊號掃描**（純 regex，零 LLM）：329 個訊號詞（182 中＋147 英，涵蓋 8 tag）逐句掃，含訊號的句子當提示注入，逼模型逐句確認、不漏。英文用 lookaround 邊界＋IGNORECASE，中英混寫（`用 Redis`）也對。
3. **multi-perspective 雙 lens 平行 union**：通用 lens ＋ 專盯「改善/重寫/技術債」的清理 lens 各跑一次取聯集——去相關，補單一視角會系統性漏的隱性項。
4. **loop-until-dry 補抓**：把已記的餵回問「還有漏的嗎」，跑到某輪無新增才停。
5. **保守模糊去重**：合併「同事換句話說」的重複；短字串不做包含比對，**寧可留重複也不誤殺真 task**（recall-first）。
6. **按需讀 detail（兩趟 lazy fetch）**：要改既有 task 內容時才把該條完整 detail 餵回改寫，平常只餵精簡索引（防 context rot）。
7. **規則過濾**：AI 的服務性提議（「要不要我做X」）本身不建 task，等你同意才算。

**設計哲學：recall-first（寧可多建）**。漏一個 task 沒追代價極高；多建一個花幾秒刪掉。所有判斷門檻往「建」拉，但守 grounding 底線（不虛構原文沒有的）。

---

## 檔案佈局（每個 session 一個資料夾）
```
~/task-manager/<session名>-<session_id>/
  active/   T-XXX.json   # 未結束 6 種標籤
  archive/  T-XXX.json   # done / backlog
  BOARD.md               # 人看的彙整看板（自動生成）
  ops.jsonl              # 每輪操作稽核 log（含 latency / token）
  state.json / .seq      # 處理進度 / id 高水位（單調遞增不重用）
```
session 名取自 transcript 的 `custom-title`；用 session_id 後綴比對重用資料夾（改名也認得）。每個 session 各自獨立。

---

## 隨附兩個 skill

**`/task-manager-check`** — 查看本 session 任務。預設列所有非 done 標題（依標籤分組）。
`show.py --detail`（附內容）/ `--all`（含 done）/ `--tag question` / `T-003`（單項）。

**`/task-manager-build-tasks-from-history -n N`** — 回填：把最近 N 輪對話補抽成 task（用途：session 在裝 hook 前就開了）。重放同一套抽取邏輯、逐輪餵 board 索引→連狀態流轉都重建。先 `--dry` 預覽再寫。

---

## 設定 / 開關
- 關閉追蹤：`~/.claude/settings.json` 的 `hooks.Stop` 移除 `task-manager/hook.sh`。
- 換模型：`tracker.py` 的 `DS_MODEL`（預設 `deepseek-v4-flash`）。
- 改時區：`tracker.py` 的 `TZ8`（預設 +08）。
- **一律 non-think**（無逃生口）：抽取/分類屬短輸出判斷，thinking 邊際效益僅 +1~3%(arXiv 2603.19558) 卻 token 暴增 10~100×、大輸入會 timeout 失敗。實測 non-think 比 think 快 5×、recall 不掉、更可靠。

---

## 已知限制
- **不抓背景 subagent / workflow 的工作**（刻意過濾 `isSidechain`）。背景 agent 做的事看不到——請在主線對話講清楚做了什麼，hook 才抓得到。
- **無 grounding 閘門**：因 recall-first 且實測無幻覺（捏造率 ~0），未加。若觀察到幻覺，可加 Tier0 詞彙預篩 + DeepSeek Flash skeptical judge（~$1.5–3.5/月）。

---

## 設計決策（都有 benchmark 數據背書，見 `eval/`）
- **chunking 否決**：PDF 的 chunking 主場是「萬字級→上百實體」；一個對話 turn 達不到（35 項埋藏 prose non-think 單趟仍抓全），切塊只徒增重複。保留給超大文件。
- **thinking 否決**：見上「一律 non-think」。
- **multi-perspective 採用**：把 correlated miss（同 prompt 多抽都漏同一個）decorrelate，mega 案 98.7%→穩定 100%。

`eval/` 內含 recall benchmark（`cases.json` 13 案含 15 項大尺度案、`score.py`、`RESULTS.md`）。跑法：`DEEPSEEK_API_KEY=... python3 eval/score.py single sweep`。

---

MIT License.
