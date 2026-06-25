#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task-manager：Claude Code Stop hook 的背景工作者。

每輪「使用者說完 + AI 回完」後被觸發，讀取本 session transcript 的「最新一輪純對話」
（只取 user / assistant 的 text，跳過 tool_use / tool_result），交給 DeepSeek V4 Flash(high)
判斷要對任務／問題清單做哪些新增 / 更新 / 刪除，最後由 Python deterministically 套用到
per-item JSON 檔（時間戳、id、檔案搬移都由 Python 蓋，LLM 只負責語意判斷）。

設計原則（對應使用者 CLAUDE.md）：
- deterministic-first：找檔、定位、蓋時間、搬 done/backlog 一律 Python，零 LLM 幻覺。
- 防 context rot：只把「active 項目的精簡索引（id/tag/title）」餵 LLM，detail 永不進 context；
  done / backlog 歸檔後完全不餵。active 累積再多，進 LLM 的也只是每條約 15 token 的索引。
- context caching：固定的分類規則 / schema / few-shot 全放 system（穩定前綴 → DeepSeek 自動快取）。
- 時間：一律 Asia/Taipei (+08)。
"""

import sys
import os
import json
import re
import time
import urllib.request
import urllib.error
import concurrent.futures as cf
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ8 = timezone(timedelta(hours=8))
DS_ENDPOINT = "https://api.deepseek.com/anthropic/v1/messages"
DS_MODEL = "deepseek-v4-flash"
MAX_SWEEP_ROUNDS = 3  # 補抓迴圈上限：第一趟後最多再掃 3 輪，跑到某輪無新增才停
HOOK_DIR = Path(__file__).resolve().parent

ACTIVE_TAGS = ["todo", "in_progress", "need_verify", "question", "workaround", "issue"]
ARCHIVE_TAGS = ["done", "backlog"]
ALL_TAGS = ACTIVE_TAGS + ARCHIVE_TAGS

# ──────────────────────────────────────────────────────────────────────────
# 固定 system prompt：穩定前綴，DeepSeek 會對它做 context cache（每輪命中、便宜）。
# 變動內容（active 索引 + 最新對話）一律放 user message，不放這裡。
# ──────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一個「開發任務／問題追蹤器」。每次會收到「目前 active 項目的精簡索引」與「最新一輪使用者↔AI 對話」，你只需判斷這輪對話對清單造成哪些變化，輸出一段 JSON 操作指令。你不負責寫時間、不負責產生 id、不負責搬檔——那些由外部程式處理。

# 8 個標籤的定義
任務類（狀態機，會流轉）：
- todo：使用者交辦或 AI 想到但尚未開始的工作、待辦、待研究、還沒寫完、做一半暫停的工作（注意：若是因 bug/error/出錯而中斷，記 issue 不是 todo）。
- in_progress：這輪正在進行中的工作。
- need_verify：程式改完、待驗證/待測試/待 review 的工作。
- done：已驗證完成的工作。
問題類（記錄「為什麼卡住」，不是狀態流轉）：
- backlog：需長期未來才能做、現在還不能或還不打算做的。必須在 detail 寫清楚「為何現在無法做／為何不打算做」。
- question：AI 有疑問、沒釐清、要問使用者、等使用者決定的事項。
- workaround：暫時略過、退版到舊版、應急先跳過、改了新程式但仍走舊路跑舊版的妥協。
- issue：程式或任務「出錯／壞掉／受阻」的記錄。包含 bug、error、exception、crash、編譯錯誤、測試失敗、stacktrace、token limit、邏輯錯誤、語意錯誤（程式沒報錯但行為違背設計初衷／專案目標）、權限問題，或任何導致失敗／受阻的原因。**只要是「壞掉／出錯／卡住」就記 issue，即使打算稍後修**（不要因為「之後會修」就改記 todo）。

# 你能輸出的操作（action）
- add：新增一個項目。欄位 tag, title, detail。
- update：更新既有項目（用 id 指定）。可改 tag（例如 in_progress→need_verify、todo→in_progress）、title、detail。只給要改的欄位即可；要把任務推進到下一狀態時，用 update 改 tag。
- delete：刪除一個項目（用 id）。僅用於明顯重複或誤建。

# 嚴格規則
1. **追求高 recall：寧可多建、絕不漏記。** 漏掉一個該追的任務代價極高；多建一個只要幾秒刪掉。所以判斷門檻往「建」的方向拉：
   - 只要對話裡出現「可能是任務 / 問題 / 待辦 / 卡關 / 決策 / 妥協」的東西，就建；**模稜兩可、不確定算不算的，一律建**。
   - 一段話有多個不同任務/問題，**全部**列出，不可只記第一個。
   - 底線（grounding，防幻覺）：每個項目都必須**對得上對話裡實際出現的內容**，detail 要能寫出「依據對話的哪一句」；**只有**純問候/與工作完全無關的閒聊才不建。寧可多建真實的，不可虛構不存在的。
2. 一個工作在這輪內被推進（例如「我改完了，要來驗證」），對既有 id 用 update 改 tag，不要 add 新項目。
3. 索引裡已存在語意相同的項目時，用 update，絕不重複 add。
4. title 用繁體中文、精簡一句（≤30 字）。detail 用繁體中文，寫清楚脈絡：要做什麼 / 卡在哪 / 為何如此。
5. backlog 與 issue 的 detail 必須含「原因」。
6. 若這輪對話對清單沒有任何該記的變化，輸出 {"ops": [], "need_detail": []}。
7. 只輸出一個 JSON 物件，不要任何前後說明、不要 markdown code fence。
8. 你預設只看得到既有 task 的「id/tag/title」精簡索引，看不到它們的完整 detail。
   - 新增任務、改 tag/狀態、刪除、補一句簡短 detail：直接放進 ops 即可。
   - 但若這輪要「修改某既有 task 的具體內容/需求」（增補、修正、重寫其 detail），你**必須先看到它現有的完整 detail** 才能正確改寫，不可憑空覆蓋。此時把該 task id 放進 need_detail（通常 0–2 個），**先不要**對它輸出 ops；系統會把完整內容給你，你再於第二趟改寫。

# 輸出格式（第一趟）
{"ops": [
  {"action": "add", "tag": "todo", "title": "...", "detail": "..."},
  {"action": "update", "id": "T-003", "tag": "need_verify"},
  {"action": "delete", "id": "T-009"}
], "need_detail": ["T-007"]}

# 第二趟（只有當你回報了 need_detail 才會發生）
你會收到那些 task 的現有完整內容（id/tag/title/detail）。請在「現有 detail 的基礎上」做使用者要求的修改，
輸出 {"ops": [{"action": "update", "id": "...", "title": "(可選)", "detail": "改寫後的完整內容"}]}，保留仍有效的部分、只動該動的。

# 範例（第一趟）
索引：
T-003 [in_progress] 實作登入重試邏輯
T-007 [question] 是否要支援 SSO，等使用者決定
最新對話：
[使用者] 登入重試 review ok 了。另外把 T-007 的需求改清楚：改成強制走 SSO、且要支援多租戶
[AI] 好，登入重試標記完成。T-007 我來更新需求。
正確輸出（status 直接做；T-007 要改內容 → 進 need_detail）：
{"ops": [
  {"action": "update", "id": "T-003", "tag": "done"}
], "need_detail": ["T-007"]}
"""


# ──────────────────────────────────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────────────────────────────────
def now_str():
    return datetime.now(TZ8).strftime("%Y-%m-%d %H:%M:%S +08")


def log(taskdir, msg):
    try:
        with open(taskdir / "worker.log", "a", encoding="utf-8") as f:
            f.write(f"[{now_str()}] {msg}\n")
    except Exception:
        pass


def read_text_blocks(content):
    """從 message.content 取出純文字（list 或 str），跳過 tool_use / tool_result / thinking / image。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "text":
            t = blk.get("text", "")
            if t:
                parts.append(t)
    return "\n".join(parts).strip()


# harness 注入的非對話內容：slash 指令展開、本地指令輸出、system-reminder、bash 輸出等。
# 這些不是「真實可見對話」，整段剝除；剝完若無實質文字則跳過該筆。
_NOISE_PATTERNS = [
    r"<system-reminder>.*?</system-reminder>",
    r"<local-command-caveat>.*?</local-command-caveat>",
    r"<local-command-stdout>.*?</local-command-stdout>",
    r"<command-name>.*?</command-name>",
    r"<command-message>.*?</command-message>",
    r"<command-args>.*?</command-args>",
    r"<bash-input>.*?</bash-input>",
    r"<bash-stdout>.*?</bash-stdout>",
    r"<bash-stderr>.*?</bash-stderr>",
]
_NOISE_RE = [re.compile(p, re.DOTALL) for p in _NOISE_PATTERNS]


def clean_user_text(t):
    """剝除 harness 注入的 wrapper，回傳使用者真正打的字（可能為空）。"""
    if not t:
        return ""
    for rx in _NOISE_RE:
        t = rx.sub("", t)
    return t.strip()


def load_transcript(path):
    """單次掃描 transcript，回傳 (entries, title)。
    entries：只含主線可見對話（過濾 isMeta/isSidechain/tool 輸出/slash 指令噪音）。
    title：最後一個 custom-title（session 名）。"""
    entries = []
    title = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                t = e.get("type")
                if t == "custom-title" and e.get("customTitle"):
                    title = e["customTitle"]
                    continue
                if t not in ("user", "assistant"):
                    continue
                if e.get("isMeta") or e.get("isSidechain"):
                    continue  # 跳過 harness meta 與 subagent 旁支，只留主線可見對話
                msg = e.get("message") or {}
                text = read_text_blocks(msg.get("content"))
                if t == "user":
                    text = clean_user_text(text)  # 剝除 slash 指令展開 / system-reminder / bash 輸出
                if not text:
                    continue  # 跳過純 tool_result / 純噪音 / 空筆
                entries.append({"uuid": e.get("uuid", ""), "role": t, "text": text})
    except FileNotFoundError:
        pass
    return entries, title


def slice_exchange(entries, last_uuid):
    """從 entries 取 last_uuid 之後的對話；找不到 last_uuid（新 session/compaction）只取最後一輪。
    回傳 (exchange_text, newest_uuid)。"""
    if not entries:
        return "", last_uuid

    newest_uuid = entries[-1]["uuid"]
    if newest_uuid == last_uuid:
        return "", newest_uuid  # 沒有新內容

    # 決定起點
    start = 0
    if last_uuid:
        for i, e in enumerate(entries):
            if e["uuid"] == last_uuid:
                start = i + 1
                break
        else:
            # 找不到 → 只取最後一個 user 之後（避免 compaction 後一次灌太多）
            last_user = max((i for i, e in enumerate(entries) if e["role"] == "user"), default=0)
            start = last_user
    else:
        last_user = max((i for i, e in enumerate(entries) if e["role"] == "user"), default=0)
        start = last_user

    chunk = entries[start:]
    if not chunk:
        return "", newest_uuid

    lines = []
    for e in chunk:
        who = "使用者" if e["role"] == "user" else "AI"
        lines.append(f"[{who}] {e['text']}")
    return "\n\n".join(lines), newest_uuid


def load_items(taskdir):
    items = {}
    for sub in ("active", "archive"):
        d = taskdir / sub
        if not d.is_dir():
            continue
        for fp in d.glob("T-*.json"):
            try:
                items[fp.stem] = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return items


def next_id(taskdir):
    mx = 0
    for sub in ("active", "archive"):
        d = taskdir / sub
        if not d.is_dir():
            continue
        for fp in d.glob("T-*.json"):
            m = re.match(r"T-(\d+)", fp.stem)
            if m:
                mx = max(mx, int(m.group(1)))
    # .seq 高水位記號：保證 id 單調遞增、刪除後不重用（避免 ops.jsonl 同號指向不同項目）
    seqf = taskdir / ".seq"
    try:
        mx = max(mx, int(seqf.read_text().strip()))
    except Exception:
        pass
    nid = mx + 1
    try:
        seqf.write_text(str(nid))
    except Exception:
        pass
    return f"T-{nid:03d}"


def build_index(taskdir):
    """active 精簡索引（id/tag/title），只給 LLM 看的小東西。"""
    d = taskdir / "active"
    rows = []
    if d.is_dir():
        for fp in sorted(d.glob("T-*.json")):
            try:
                it = json.loads(fp.read_text(encoding="utf-8"))
                rows.append(f"{it['id']} [{it['tag']}] {it.get('title','')}")
            except Exception:
                pass
    return "\n".join(rows) if rows else "（目前無 active 項目）"


def item_path(taskdir, tag, iid):
    sub = "archive" if tag in ARCHIVE_TAGS else "active"
    return taskdir / sub / f"{iid}.json"


def find_existing_path(taskdir, iid):
    for sub in ("active", "archive"):
        p = taskdir / sub / f"{iid}.json"
        if p.exists():
            return p
    return None


def write_item(taskdir, item):
    """寫到正確的 active/archive，並清掉另一邊的舊檔（tag 變動造成的搬移）。"""
    p = item_path(taskdir, item["tag"], item["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    # 移除另一目錄的殘檔
    other = "active" if p.parent.name == "archive" else "archive"
    op = taskdir / other / f"{item['id']}.json"
    if op.exists():
        op.unlink()


def apply_ops(taskdir, ops):
    items = load_items(taskdir)
    applied = []
    for op in ops:
        action = op.get("action")
        try:
            if action == "add":
                tag = op.get("tag")
                if tag not in ALL_TAGS:
                    continue
                iid = next_id(taskdir)
                item = {
                    "id": iid,
                    "tag": tag,
                    "title": (op.get("title") or "").strip(),
                    "detail": (op.get("detail") or "").strip(),
                    "created": now_str(),
                    "updated": now_str(),
                }
                write_item(taskdir, item)
                items[iid] = item
                applied.append(f"add {iid} [{tag}] {item['title']}")

            elif action == "update":
                iid = op.get("id")
                if not iid or iid not in items:
                    continue
                item = items[iid]
                if op.get("tag") in ALL_TAGS:
                    item["tag"] = op["tag"]
                if op.get("title"):
                    item["title"] = op["title"].strip()
                if op.get("detail"):
                    item["detail"] = op["detail"].strip()
                item["updated"] = now_str()
                write_item(taskdir, item)
                applied.append(f"update {iid} -> [{item['tag']}] {item['title']}")

            elif action == "delete":
                iid = op.get("id")
                p = find_existing_path(taskdir, iid) if iid else None
                if p:
                    p.unlink()
                    items.pop(iid, None)
                    applied.append(f"delete {iid}")
        except Exception as e:
            applied.append(f"ERR {action}: {e}")
    return applied


def render_board(taskdir):
    """產生人看的 BOARD.md（彙整全部項目，分標籤）。純 Python，不進 LLM。"""
    items = load_items(taskdir)
    by_tag = {t: [] for t in ALL_TAGS}
    for it in items.values():
        by_tag.get(it.get("tag", "todo"), by_tag["todo"]).append(it)
    lines = [f"# Task Board", f"_更新於 {now_str()}_", ""]
    labels = {
        "todo": "📋 Todo", "in_progress": "🔨 In Progress", "need_verify": "🔍 Need Verify",
        "done": "✅ Done", "backlog": "🗄️ Backlog", "question": "❓ Question",
        "workaround": "🩹 Workaround", "issue": "🛑 Issue",
    }
    for t in ALL_TAGS:
        rows = sorted(by_tag[t], key=lambda x: x.get("id", ""))
        if not rows:
            continue
        lines.append(f"## {labels[t]} ({len(rows)})")
        for it in rows:
            lines.append(f"- **{it['id']}** {it.get('title','')}  ·_{it.get('updated','')}_")
            if it.get("detail"):
                lines.append(f"  - {it['detail']}")
        lines.append("")
    (taskdir / "BOARD.md").write_text("\n".join(lines), encoding="utf-8")


def build_pass1_user(active_index, exchange):
    return (
        "## 目前 active 項目索引（只有 id/tag/title）\n"
        f"{active_index}\n\n"
        "## 最新一輪對話\n"
        f"{exchange}\n\n"
        "請依規則輸出第一趟 JSON：{\"ops\":[...], \"need_detail\":[...]}。"
    )


def build_pass2_user(details, exchange):
    blocks = "\n".join(
        f"- {d['id']} [{d['tag']}] {d.get('title','')}\n  現有 detail：{d.get('detail','')}"
        for d in details
    )
    return (
        "## 你要求查看的既有 task 完整內容\n"
        f"{blocks}\n\n"
        "## 最新一輪對話\n"
        f"{exchange}\n\n"
        "請在現有 detail 基礎上做使用者要求的修改，輸出 {\"ops\":[{\"action\":\"update\",\"id\":...,\"detail\":...}]}。"
    )


def norm_title(t):
    """正規化標題供模糊去重：去標點/空白、剝除常見動詞前綴。"""
    t = re.sub(r"[\s，。、,.:：;；!！?？（）()\[\]【】「」]", "", t or "")
    t = re.sub(r"^(修復|修正|處理|實作|完成|新增|加上|解決|修|加|做)", "", t)
    return t.lower()


def is_dup_title(title, found_norms):
    """換句話說/問題 vs 修它的工作 都算同一件 → 正規化包含或字元 Jaccard≥0.8 即視為重複。"""
    n = norm_title(title)
    if not n:
        return True
    for f in found_norms:
        if not f:
            continue
        if n == f or n in f or f in n:
            return True
        a, b = set(n), set(f)
        if a and b and len(a & b) / len(a | b) >= 0.8:
            return True
    return False


def build_cleanup_lens_user(exchange):
    """多視角第二鏡：專盯通用 lens 系統性會漏的『改善/重寫/清理/技術債』類（benchmark 證實能 decorrelate 漏抓）。"""
    return (
        "【特定視角抽取】從以下對話專門找出所有「需要改善 / 重寫 / 重構 / 清理 / 優化 / "
        "有技術債 / 寫得不好 / 很醜 / 將就 / 先頂著」的項目，**即使聽起來只是小毛病也要列**；"
        "沒有就回 {\"ops\":[]}，不要虛構。\n"
        "輸出 {\"ops\":[{\"action\":\"add\",\"tag\":...,\"title\":...,\"detail\":...}]}（tag 依 8 標籤定義）。\n\n"
        f"對話：\n{exchange}"
    )


def build_more_user(exchange, found_display):
    found = "\n".join(f"- {s}" for s in found_display) if found_display else "（無）"
    return (
        "## 這輪「已記錄／已存在」的項目（含標籤）\n"
        f"{found}\n\n"
        "## 同一輪對話（再看一次，找有沒有漏的）\n"
        f"{exchange}\n\n"
        "請只找出『上面清單還沒有、且確實是另一件不同事情』的任務或問題，"
        "輸出 {\"ops\":[{\"action\":\"add\",\"tag\":...,\"title\":...,\"detail\":...}]}。\n"
        "重要：同一件事換句話說、或同一個 bug 既算『問題(issue)』又算『要修的工作』，都是**同一項**，已列就**不要**再加。\n"
        "沒有任何遺漏就輸出 {\"ops\":[]}；**絕不可為了湊數而虛構**。"
    )


def load_details(taskdir, ids):
    """載入指定 id 的完整內容（給第二趟改寫用）。"""
    out = []
    for iid in ids:
        p = find_existing_path(taskdir, iid)
        if p:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    return out


def call_deepseek(user_msg):
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未設定")
    body = {
        "model": DS_MODEL,
        "max_tokens": 2000,
        # 固定前綴標 cache_control（Anthropic 語意的快取是「顯式」的，非自動）→ 每輪命中、省錢。
        "system": [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_msg}],
    }
    req = urllib.request.Request(
        DS_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = ""
    for blk in data.get("content", []):
        if blk.get("type") == "text":
            text += blk.get("text", "")
    usage = data.get("usage", {})
    return text.strip(), usage


def extract_json(text):
    """從可能含雜訊的回應抽出第一個 JSON 物件。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError(f"無法解析 JSON：{text[:200]}")


def ask_ops(user_msg, retries=2):
    """呼叫 + 解析，對「空回應 / 解析失敗」自動重試（保護 recall：失敗的 extraction = 漏 task）。
    重試用盡仍失敗才回 {"ops": []}，並把最後一次 usage 一併回傳供記帳。"""
    usage = {}
    for attempt in range(retries + 1):
        try:
            text, usage = call_deepseek(user_msg)
            return extract_json(text), usage
        except Exception:
            continue
    return {"ops": []}, usage


def sanitize(name):
    """把 session 名清成可當資料夾名（保留中英數，其餘換 _，限長）。"""
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name)
    name = name.strip("_")
    return name[:40] or "session"


def resolve_taskdir(sid, title):
    """~/task-manager/<session名>-<session_id>/。
    用 session_id 後綴比對重用既有資料夾——session 改名也認得同一個 session、不丟狀態。"""
    base = Path.home() / "task-manager"
    base.mkdir(parents=True, exist_ok=True)
    if sid:
        for d in base.glob(f"*-{sid}"):
            if d.is_dir():
                return d
    folder = f"{sanitize(title)}-{sid}" if sid else sanitize(title)
    return base / folder


# ──────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────
def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return
    transcript = payload.get("transcript_path", "")
    if not transcript or not os.path.exists(transcript):
        return

    # session id：payload 優先，否則用 transcript 檔名（檔名即 session_id）
    sid = payload.get("session_id") or os.path.basename(transcript).rsplit(".jsonl", 1)[0]
    # 單次掃描拿到主線對話 + session 名
    entries, title = load_transcript(transcript)

    taskdir = resolve_taskdir(sid, title)
    taskdir.mkdir(parents=True, exist_ok=True)
    (taskdir / "active").mkdir(exist_ok=True)
    (taskdir / "archive").mkdir(exist_ok=True)

    # mkdir 鎖（macOS 無 flock）：拿不到就放棄這輪（下一輪會再處理）
    lock = taskdir / ".lock"
    try:
        lock.mkdir()
    except FileExistsError:
        # 過期鎖（>120s）清掉
        try:
            if time.time() - lock.stat().st_mtime > 120:
                lock.rmdir()
                lock.mkdir()
            else:
                return
        except Exception:
            return

    try:
        state_fp = taskdir / "state.json"
        last_uuid = ""
        if state_fp.exists():
            try:
                last_uuid = json.loads(state_fp.read_text(encoding="utf-8")).get("last_uuid", "")
            except Exception:
                pass

        exchange, newest_uuid = slice_exchange(entries, last_uuid)
        if not exchange.strip():
            state_fp.write_text(json.dumps({"last_uuid": newest_uuid}, ensure_ascii=False), encoding="utf-8")
            return

        active_index = build_index(taskdir)
        t0 = time.time()
        # 第一趟：multi-perspective——通用 lens + 清理 lens 平行 union（benchmark 證實 98.7%→100%、無額外幻覺）。
        # 通用 lens 出 ops + need_detail；清理 lens 補通用會系統性漏的「改善/重寫/技術債」類。
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            f_gen = pool.submit(ask_ops, build_pass1_user(active_index, exchange))
            f_cln = pool.submit(ask_ops, build_cleanup_lens_user(exchange))
            obj, usage = f_gen.result()
            obj_cln, _ucln = f_cln.result()
        ops = obj.get("ops", []) if isinstance(obj, dict) else []
        need = obj.get("need_detail", []) if isinstance(obj, dict) else []
        need = [i for i in need if isinstance(i, str)]
        # union 清理 lens 的 adds（模糊去重，避免和通用 lens 重複）
        _n = {norm_title(o.get("title", "")) for o in ops if o.get("action") == "add"}
        for op in (obj_cln.get("ops", []) if isinstance(obj_cln, dict) else []):
            if op.get("action") == "add" and op.get("title") and not is_dup_title(op["title"], _n):
                ops.append(op); _n.add(norm_title(op["title"]))

        # 第二趟（按需）：只把要改內容的那 1–2 條完整 detail 餵回去 → 拿改寫 ops
        usage2 = None
        if need:
            details = load_details(taskdir, need)
            if details:
                obj2, usage2 = ask_ops(build_pass2_user(details, exchange))
                ops += obj2.get("ops", []) if isinstance(obj2, dict) else []

        # 補抓迴圈（loop-until-dry）：把已記的餵回去問「還有漏的嗎」，跑到某輪無新增才停。
        # 解決單次抽取在多任務回合的 recall 衰減；嚴禁虛構，並用模糊去重擋「換句話說的重複」。
        seed = list(load_items(taskdir).values()) + [op for op in ops if op.get("action") == "add"]
        found_norms = {norm_title(it.get("title", "")) for it in seed}
        found_display = [f"[{it.get('tag', '')}] {it.get('title', '')}" for it in seed if it.get("title")]
        sweeps = 0
        for _ in range(MAX_SWEEP_ROUNDS):
            ox, _u = ask_ops(build_more_user(exchange, found_display))
            extra = []
            for op in (ox.get("ops", []) if isinstance(ox, dict) else []):
                if op.get("action") == "add" and op.get("title") and not is_dup_title(op["title"], found_norms):
                    extra.append(op)
                    found_norms.add(norm_title(op["title"]))
                    found_display.append(f"[{op.get('tag', '')}] {op['title']}")
            sweeps += 1
            if not extra:
                break
            ops += extra
        dt = time.time() - t0

        applied = apply_ops(taskdir, ops) if ops else []
        if applied:
            render_board(taskdir)

        # 稽核 log（每輪一筆）
        with open(taskdir / "ops.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": now_str(),
                "latency_s": round(dt, 2),
                "usage": usage,
                "usage_pass2": usage2,
                "need_detail": need,
                "sweeps": sweeps,
                "ops": ops,
                "applied": applied,
            }, ensure_ascii=False) + "\n")

        cached = usage.get("cache_read_input_tokens", 0)
        log(taskdir, f"done {dt:.1f}s ops={len(ops)} applied={len(applied)} "
                     f"need_detail={len(need)} pass2={'Y' if usage2 else 'N'} sweeps={sweeps} "
                     f"cache_read={cached} in={usage.get('input_tokens',0)}")

        # 成功才推進 state（失敗則下輪重試同一段）
        state_fp.write_text(json.dumps({"last_uuid": newest_uuid}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(taskdir, f"ERROR {type(e).__name__}: {e}")
    finally:
        try:
            lock.rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    main()
