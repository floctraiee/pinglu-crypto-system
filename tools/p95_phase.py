# -*- coding: utf-8 -*-
"""按阶段统计网关延迟（P95 重测用）。

用法（仓库根目录）：
    :: 阶段 1（A 跑完 --rounds 3 之后）只看 batch_no <= 3 的批次
    .venv\\Scripts\\python.exe test_data\\perf_100\\p95_phase.py --db test_data\\perf_100\\edge_cache_round5.db --phase1

    :: 阶段 2（A 跑完 --rounds 180 之后）只看 batch_no > 3 的批次
    .venv\\Scripts\\python.exe test_data\\perf_100\\p95_phase.py --db test_data\\perf_100\\edge_cache_round5.db --phase2

    :: 全部样本
    .venv\\Scripts\\python.exe test_data\\perf_100\\p95_phase.py --db test_data\\perf_100\\edge_cache_round5.db --all
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_MS = 1000.0


def percentile(values, ratio):
    if not values:
        return None
    idx = min(len(values) - 1, int((len(values) - 1) * ratio))
    return values[idx]


def describe(latencies):
    values = sorted(latencies)
    if not values:
        return {"count": 0}
    p95 = percentile(values, 0.95)
    return {
        "count": len(values),
        "min_ms": round(values[0], 1),
        "median_ms": round(percentile(values, 0.50), 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(values[-1], 1),
        "target_ms": TARGET_MS,
        "p95_within_target": p95 <= TARGET_MS,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--phase1", action="store_true",
                       help="只看 batch_no <= 3（阶段 1：--rounds 3）")
    group.add_argument("--phase2", action="store_true",
                       help="只看 batch_no > 3（阶段 2：--rounds 180）")
    group.add_argument("--all", action="store_true", help="全部样本")
    parser.add_argument("--where", dest="extra_where", default="",
                        help='额外 SQL 条件，例如 "batch_no > 6"')
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"找不到 {db}")

    uri = "file:" + str(db).replace("\\", "/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT COUNT(*) FROM pending_batches").fetchone()
    except sqlite3.OperationalError:
        # 某些受限环境下只读连接无法创建 -shm，退回 immutable（仅在无写入时使用）
        conn = sqlite3.connect(uri + "&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row

    where = ""
    label = "全部"
    if args.phase1:
        where = " WHERE batch_no <= 3"
        label = "阶段 1（batch_no <= 3，--rounds 3）"
    elif args.phase2:
        where = " WHERE batch_no > 3"
        label = "阶段 2（batch_no > 3，--rounds 180）"
    if args.extra_where:
        where = (" WHERE " + args.extra_where) if not where else (where + " AND (" + args.extra_where + ")")
        label = label + " + " + args.extra_where

    rows = conn.execute(
        "SELECT status, latency_ms FROM pending_batches" + where
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM pending_batches").fetchone()[0]
    conn.close()

    status = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]

    print("=" * 64)
    print("统计范围 :", label)
    print("库内总批 :", total)
    print("本次样本 :", len(rows))
    print("状态分布 :", json.dumps(status, ensure_ascii=False))
    pending = status.get("pending", 0) + status.get("audit_pending", 0)
    print("待传/留证:", pending, "<- 必须 0 才算发完")
    print("=" * 64)
    print("延迟统计 :", json.dumps(describe(latencies), ensure_ascii=False))
    print("=" * 64)

    if not latencies:
        print("结论: 没有延迟样本（可能还没开始转发）")
        return 1
    p95 = percentile(sorted(latencies), 0.95)
    if p95 <= TARGET_MS:
        print("结论: PASS  P95 = %.1f ms <= %.0f ms" % (p95, TARGET_MS))
        return 0
    print("结论: FAIL  P95 = %.1f ms > %.0f ms" % (p95, TARGET_MS))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
