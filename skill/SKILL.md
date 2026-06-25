---
name: task-manager-check
description: 查看「目前這個 Claude Code session」的 task-manager 任務看板。預設列出所有非 done 的任務（in_progress / need_verify / todo / question / issue / workaround / backlog）標題，依標籤分組。也可看特定標籤、單一項目完整內容、含 done、或附 detail。Trigger on：'/task-manager-check'、'看 task'、'目前任務'、'還有什麼沒做'、'task 看板'、'task-manager 現在有啥'、'這個 session 的待辦'、'列任務'.
---

# task-manager-check

唯讀查看本 session（[[project-task-manager-hook]] 寫出）的任務看板。**列任務是 deterministic 的事**，全交給 `show.py`，不要自己讀 per-item JSON 進 context。

## 怎麼做
直接執行 helper（它會自動偵測當前 session 的資料夾 `~/task-manager/<session名>-<session_id>/`）：

```bash
python3 ~/.claude/skills/task-manager-check/show.py
```

把它的輸出原樣呈現給使用者即可（已分組、已排序、繁體中文）。

## 依使用者意圖選參數
| 使用者要的 | 指令 |
|---|---|
| 預設：列所有非 done 的標題 | `show.py` |
| 連 detail 一起看 | `show.py --detail` |
| 連 done 也看 | `show.py --all` |
| 只看完成的 | `show.py --done` |
| 只看某標籤（如卡住的問題） | `show.py --tag question`（或 issue / in_progress…） |
| 看某一項的完整內容 | `show.py T-003` |

## 注意
- 「當前 session」靠「最近被寫入的 transcript」判定（本 session 此刻正在寫，必為最新）；若使用者明確指定別的 session，用 `--show.py --sid <id>`。
- 找不到資料夾 → 代表這個 session 還沒被 hook 記過任何 task（例如 session 在裝 hook 後尚未 resume）。如實告知，別假裝有資料。
- 唯讀：本 skill 不改、不刪任何 task；要改動請回到對話讓 hook 自然處理。
