#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task 抽取 recall benchmark 評分器。可插拔配置，平行跑、每案重複取平均。"""
import sys, os, re, json, statistics
import concurrent.futures as cf
sys.path.insert(0, os.path.expanduser("~/.claude/hooks/task-manager"))
import tracker

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = json.load(open(os.path.join(HERE, "cases.json"), encoding="utf-8"))
REPEATS = int(os.environ.get("REPEATS", "3"))


def norm(s):
    return re.sub(r"[\s\W_]+", "", (s or "")).lower()


def op_text(op):
    return norm(op.get("title", "") + op.get("detail", ""))


def hit(expected_item, extracted):
    keys = [norm(k) for k in expected_item["keys"]]
    return any(any(k in op_text(op) for k in keys) for op in extracted)


def score_case(expected, extracted):
    if not expected:  # 純閒聊：測幻覺，extracted 越少越好
        return {"recall": None, "fp": len(extracted)}
    hits = sum(1 for e in expected if hit(e, extracted))
    matched = sum(1 for op in extracted
                  if any(any(norm(k) in op_text(op) for k in e["keys"]) for e in expected))
    return {"recall": hits / len(expected), "hits": hits, "total": len(expected),
            "n_ex": len(extracted), "fp": len(extracted) - matched}


# ── 配置（每個回傳 add-ops 清單）─────────────────────────────
def cfg_single(exchange):
    obj, _ = tracker.ask_ops(tracker.build_pass1_user("", exchange))
    return [o for o in obj.get("ops", []) if o.get("action") == "add"]


def cfg_sweep(exchange):
    obj, _ = tracker.ask_ops(tracker.build_pass1_user("", exchange))
    ops = [o for o in obj.get("ops", []) if o.get("action") == "add"]
    fn = {tracker.norm_title(o.get("title", "")) for o in ops}
    fd = [f"[{o.get('tag', '')}] {o.get('title', '')}" for o in ops if o.get("title")]
    for _ in range(tracker.MAX_SWEEP_ROUNDS):
        ox, _ = tracker.ask_ops(tracker.build_more_user(exchange, fd))
        extra = []
        for op in ox.get("ops", []):
            if op.get("action") == "add" and op.get("title") and not tracker.is_dup_title(op["title"], fn):
                extra.append(op); fn.add(tracker.norm_title(op["title"])); fd.append(f"[{op.get('tag', '')}] {op['title']}")
        if not extra:
            break
        ops += extra
    return ops


CONFIGS = {"single": cfg_single, "sweep": cfg_sweep}
# chunking 配置由 score_chunk.py 注入（實作 Task#1 後）
try:
    from chunk_cfg import cfg_chunk
    CONFIGS["chunk"] = cfg_chunk
except Exception:
    pass


def run_config(name, fn):
    rows = []
    def one(case):
        recs, fps = [], []
        for _ in range(REPEATS):
            ex = fn(case["exchange"])
            s = score_case(case["expected"], ex)
            if s["recall"] is not None:
                recs.append(s["recall"])
            fps.append(s["fp"])
        return case, recs, fps
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for case, recs, fps in pool.map(one, CASES):
            rows.append((case, recs, fps))
    # 維持 cases.json 順序
    order = {c["id"]: i for i, c in enumerate(CASES)}
    rows.sort(key=lambda r: order[r[0]["id"]])
    print(f"\n===== 配置：{name}（每案 {REPEATS} 次取平均）=====")
    all_rec = []
    for case, recs, fps in rows:
        if recs:
            mr = statistics.mean(recs); all_rec.append(mr)
            print(f"  {case['id']:18s} recall={mr*100:5.1f}%  (min {min(recs)*100:.0f}%)  fp均={statistics.mean(fps):.1f}  | {case['desc']}")
        else:
            print(f"  {case['id']:18s} [純閒聊] 幻覺數均={statistics.mean(fps):.1f}（應 0）")
    agg = statistics.mean(all_rec) * 100 if all_rec else 0
    print(f"  >>> 平均 recall = {agg:.1f}%")
    return agg


if __name__ == "__main__":
    which = sys.argv[1:] or list(CONFIGS.keys())
    results = {}
    for name in which:
        if name in CONFIGS:
            results[name] = run_config(name, CONFIGS[name])
    print("\n========== 總表 ==========")
    for name, agg in results.items():
        print(f"  {name:10s} 平均 recall {agg:.1f}%")
