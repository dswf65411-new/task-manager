# task 抽取 recall benchmark — 結論

13 個手標難回合（含一個 15 項大尺度 prose dump `mega_sprint`），每案重複多次取平均。

## 各配置 recall

| 配置 | mega_sprint(15項) | 小案(≤7項) | 純閒聊幻覺 | 判決 |
|---|---|---|---|---|
| single（recall-first prompt） | 98.7%（最差93%） | 100% | 0 | 好基線 |
| sweep（single + loop-until-dry） | ~99% | 100% | 0 | 邊際增益 |
| chunking + union | 98.7% + 重複雜訊(抽21–25項) | — | — | ❌ 此尺度無增益、傷精度 |
| multi-sample union（同 prompt ×3） | 98.3% | — | — | ❌ correlated miss 無解 |
| **multi-perspective（通用 lens + 清理 lens）** | **100%（5/5）** | **100%** | **0** | ✅ **採用** |

## 關鍵發現
1. **recall-first prompt 本身就把 recall 拉到 ~99%**——哲學翻轉（寧可多建）是最大功臣。
2. **chunking 在 per-turn 尺度（≤15項、短句）無效**：PDF 的 chunking 主場是「1000篇→100+實體」的巨尺度；一個對話 turn 達不到，反而 overlap 製造重複。→ chunking 保留給**回填 skill 的大文件場景**。
3. **殘留的漏抓是 correlated miss**：模型「一致地」把某類（如「template 很醜要重寫」）判為不值得追 → 多抽同一 prompt 幾次都漏同一個。
4. **解法是換視角（multi-perspective）不是多抽樣**：加一個專盯「改善/重寫/清理/技術債」的 lens 去 decorrelate，把 mega 從 98.7% 拉到穩定 100%，且簡單案 0 幻覺。

## 生產採用
`tracker.py` 第一趟改為「通用 lens + 清理 lens」平行 union（+ 既有 loop-until-dry 當保險）。
跑法：`DEEPSEEK_API_KEY=... REPEATS=3 python3 score.py single sweep`
