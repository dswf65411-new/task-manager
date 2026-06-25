#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task-manager-check：唯讀查看「目前這個 Claude Code session」的 task 看板。

deterministic：找資料夾 / 讀 JSON / 過濾 / 排序 / 列印全是純 code，零 LLM。

用法：
  python3 show.py                 預設：列出所有「非 done」任務的標題（依標籤分組）
  python3 show.py --detail        同上，但每項附 detail
  python3 show.py --all           包含 done
  python3 show.py --done          只看 done（archive）
  python3 show.py --tag question  只看某個標籤
  python3 show.py T-003           看單一項目的完整內容
  python3 show.py --sid <id>      指定 session_id（預設自動偵測當前 session）
"""
import sys
import os
import json
from pathlib import Path

# 顯示順序：最該關注的在前；done 預設不顯示
TAG_ORDER = ["in_progress", "need_verify", "todo", "question", "issue", "workaround", "backlog", "done"]
LABEL = {
    "in_progress": "🔨 in_progress", "need_verify": "🔍 need_verify", "todo": "📋 todo",
    "question": "❓ question", "issue": "🛑 issue", "workaround": "🩹 workaround",
    "backlog": "🗄️ backlog", "done": "✅ done",
}
BASE = Path.home() / "task-manager"


def current_session_id():
    """當前 session = 最近被寫入的 transcript（本 session 此刻正在寫，必為最新）。"""
    projdir = Path.home() / ".claude" / "projects"
    js = sorted(projdir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return js[0].stem if js else None


def find_folder(sid):
    if sid:
        for d in BASE.glob(f"*-{sid}"):
            if d.is_dir():
                return d
    # 退路：最近修改的 task-manager 資料夾
    dirs = [d for d in BASE.glob("*") if d.is_dir()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def load_items(folder):
    items = []
    for sub in ("active", "archive"):
        d = folder / sub
        if d.is_dir():
            for fp in d.glob("T-*.json"):
                try:
                    items.append(json.loads(fp.read_text(encoding="utf-8")))
                except Exception:
                    pass
    return items


def main():
    args = sys.argv[1:]
    show_detail = "--detail" in args or "detail" in args or "詳細" in args
    include_done = "--all" in args or "all" in args or "全部" in args
    only_done = "--done" in args or "done" in args
    only_tag = None
    single_id = None
    sid = None
    for i, a in enumerate(args):
        if a == "--tag" and i + 1 < len(args):
            only_tag = args[i + 1]
        elif a in TAG_ORDER:
            only_tag = a
        elif a == "--sid" and i + 1 < len(args):
            sid = args[i + 1]
        elif a.upper().startswith("T-"):
            single_id = a.upper()

    if not BASE.is_dir():
        print("（尚無 ~/task-manager/，這個 session 還沒記過任何 task）")
        return

    sid = sid or current_session_id()
    folder = find_folder(sid)
    if not folder:
        print("（找不到對應的 task-manager 資料夾）")
        return

    # 解析資料夾名 = <session名>-<id>
    name = folder.name
    items = load_items(folder)
    by_id = {it.get("id"): it for it in items}

    # 單一項目完整內容
    if single_id:
        it = by_id.get(single_id)
        if not it:
            print(f"找不到 {single_id}")
            return
        print(f"【{it['id']}】[{it['tag']}] {it.get('title','')}")
        print(f"  建立：{it.get('created','')}　更新：{it.get('updated','')}")
        print(f"  內容：{it.get('detail','')}")
        return

    # 過濾
    def keep(it):
        t = it.get("tag", "")
        if only_tag:
            return t == only_tag
        if only_done:
            return t == "done"
        if include_done:
            return True
        return t != "done"

    sel = [it for it in items if keep(it)]
    sel.sort(key=lambda it: (TAG_ORDER.index(it["tag"]) if it.get("tag") in TAG_ORDER else 99,
                             it.get("id", "")))

    header = f"📂 task-manager：{name}"
    scope = ("全部" if include_done else ("只看 done" if only_done else (f"只看 {only_tag}" if only_tag else "除 done 外")))
    print(f"{header}\n（{scope}，共 {len(sel)} 項）\n")
    if not sel:
        print("　🎉 沒有項目")
        return

    cur = None
    for it in sel:
        t = it.get("tag", "")
        if t != cur:
            cur = t
            cnt = sum(1 for x in sel if x.get("tag") == t)
            print(f"{LABEL.get(t, t)} ({cnt})")
        print(f"　{it.get('id','')}  {it.get('title','')}")
        if show_detail and it.get("detail"):
            print(f"　　└ {it['detail']}")


if __name__ == "__main__":
    main()
