---
name: task-manager-build-tasks-from-history
description: 把當前 Claude Code session 最近 N 輪對話「回填」成 task，寫進該 session 的 task-manager 看板。用途：session 在裝 hook 前就開了（前面的對話沒被自動記）、或想用完整 multi-perspective pipeline 重抽歷史補齊。Trigger on：'/task-manager-build-tasks-from-history'、'回填任務'、'從歷史建任務'、'把前面對話的任務補進來'、'backfill tasks'、'從對話記錄抽任務'.
---

# task-manager-build-tasks-from-history

把當前 session 最近 N 輪對話回填成 task。**deterministic 重放**：直接呼叫 hook 的同一套抽取邏輯（multi-perspective union + 按需 pass2），逐輪餵當前 board 索引，所以連狀態流轉都會重建。

## 怎麼做
1. 先用 `--dry` 預覽會抽到什麼（不寫入，讓使用者確認）：
   ```bash
   python3 ~/.claude/skills/task-manager-build-tasks-from-history/backfill.py -n 10 --dry
   ```
2. 使用者確認後再實際寫入：
   ```bash
   python3 ~/.claude/skills/task-manager-build-tasks-from-history/backfill.py -n 10
   ```
3. 把輸出原樣呈現給使用者，並提示可用 `/task-manager-check` 查看結果。

## 參數
| 參數 | 意義 |
|---|---|
| `-n N` | 回填最近 N 輪（預設 10） |
| `--dry` | 只預覽、不寫入 |
| `--sid <id>` | 指定 session（預設自動偵測當前 = 最近寫入的 transcript） |

## 注意
- **預設先 --dry 預覽再寫**：回填會打多次 LLM（每輪 multi-perspective），先讓使用者看抽到什麼、確認 N 對不對，避免白跑或抽錯範圍。
- 它寫進的是「當前 session 的看板」`~/task-manager/<session名>-<id>/`，與 hook 自動記的同一個資料夾；已存在的 task 不會重複建（抽取時看得到 board 索引）。
- N 很大（如 50）會跑比較久且花較多 token，背景跑或提醒使用者。
- 唯讀 transcript，只寫看板；不改對話。
