#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task-manager-build-tasks-from-history：把當前 Claude Code session 最近 N 輪對話
回填成 task（寫進該 session 的 ~/task-manager 看板）。

用途：session 在裝 hook 前就開了、或想用完整 pipeline 重抽歷史。
做法：重放 hook 的 per-turn 抽取邏輯（multi-perspective union + 按需 pass2）到最近 N 輪，
每輪餵當前 board 的精簡索引，所以連狀態流轉（in_progress→done）都會一起重建。

用法：
  python3 backfill.py -n 10        回填最近 10 輪
  python3 backfill.py -n 10 --dry  只預覽會抽到什麼，不寫入
  python3 backfill.py --sid <id>   指定 session（預設自動偵測當前）
"""
import sys, os, json, time
sys.path.insert(0, os.path.expanduser("~/.claude/hooks/task-manager"))
import tracker
from pathlib import Path


def current_session_id():
    projdir = Path.home() / ".claude" / "projects"
    js = sorted(projdir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return js[0].stem if js else None


def find_transcript(sid):
    projdir = Path.home() / ".claude" / "projects"
    for p in projdir.glob(f"*/{sid}.jsonl"):
        return str(p)
    js = sorted(projdir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(js[0]) if js else None


def group_turns(entries):
    """把清理過的 entries 依 user 邊界切成 turn（user + 之後的 assistant 直到下一個 user）。"""
    turns, cur = [], []
    for e in entries:
        if e["role"] == "user" and cur:
            turns.append(cur); cur = [e]
        else:
            cur.append(e)
    if cur:
        turns.append(cur)
    return turns


def turn_text(turn):
    return "\n\n".join(f"[{'使用者' if e['role'] == 'user' else 'AI'}] {e['text']}" for e in turn)


def main():
    args = sys.argv[1:]
    n = 10
    sid = None
    dry = "--dry" in args
    for i, a in enumerate(args):
        if a == "-n" and i + 1 < len(args):
            n = int(args[i + 1])
        elif a == "--sid" and i + 1 < len(args):
            sid = args[i + 1]

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("❌ 環境沒有 DEEPSEEK_API_KEY")
        return

    sid = sid or current_session_id()
    transcript = find_transcript(sid)
    if not transcript:
        print("❌ 找不到 transcript")
        return

    entries, title = tracker.load_transcript(transcript)
    turns = group_turns(entries)
    last = turns[-n:]
    print(f"📂 session：{title or sid}（id {sid[:8]}…）")
    print(f"📜 transcript 共 {len(turns)} 輪，回填最近 {len(last)} 輪" + ("（--dry 預覽）" if dry else "") + "\n")

    if dry:
        for idx, turn in enumerate(last, 1):
            ex = turn_text(turn)
            ops, _need, _u = tracker.multi_perspective_ops(ex, "")
            adds = [o for o in ops if o.get("action") == "add"]
            print(f"── 第 {idx}/{len(last)} 輪 → 會抽出 {len(adds)} 項")
            for o in adds:
                print(f"     [{o.get('tag', '')}] {o.get('title', '')}")
        print("\n（--dry：未寫入。拿掉 --dry 即實際建立。）")
        return

    taskdir = tracker.resolve_taskdir(sid, title)
    taskdir.mkdir(parents=True, exist_ok=True)
    (taskdir / "active").mkdir(exist_ok=True)
    (taskdir / "archive").mkdir(exist_ok=True)

    total = 0
    for idx, turn in enumerate(last, 1):
        ex = turn_text(turn)
        t0 = time.time()
        active_index = tracker.build_index(taskdir)
        ops, need, _u = tracker.multi_perspective_ops(ex, active_index)
        if need:
            details = tracker.load_details(taskdir, need)
            if details:
                obj2, _ = tracker.ask_ops(tracker.build_pass2_user(details, ex))
                ops += obj2.get("ops", []) if isinstance(obj2, dict) else []
        applied = tracker.apply_ops(taskdir, ops) if ops else []
        if applied:
            tracker.render_board(taskdir)
        total += sum(1 for a in applied if a.startswith("add"))
        print(f"── 第 {idx}/{len(last)} 輪（{time.time()-t0:.0f}s）→ {len(applied)} 個操作")
        for a in applied:
            print(f"     {a}")

    print(f"\n✅ 回填完成，共新增 {total} 個 task。看板：{taskdir}")
    print(f"   用 /task-manager-check 查看，或看 {taskdir}/BOARD.md")


if __name__ == "__main__":
    main()
