# -*- coding: utf-8 -*-
"""一次输出队长要的全部指标：到达速率 / 转发吞吐 / 端到端 P50·P95 / 待传 / 状态分布。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe test_data\\perf_100\\final_report.py --db test_data\\perf_100\\edge_cache_round5.db --where "batch_no > 18"
"""
import argparse
import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

TARGET_P95 = 1000.0
TARGET_RATE = 10.0


def parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int((len(v) - 1) * q))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--where", default="", help='SQL 条件，例如 "batch_no > 18"')
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"找不到 {db}")

    uri = "file:" + str(db).replace("\\", "/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT COUNT(*) FROM pending_batches").fetchone()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(uri + "&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row

    where = (" WHERE " + args.where) if args.where else ""
    rows = conn.execute(
        "SELECT batch_id, status, latency_ms, gateway_received_at, acked_at "
        "FROM pending_batches" + where + " ORDER BY id"
    ).fetchall()
    total_all = conn.execute("SELECT COUNT(*) FROM pending_batches").fetchone()[0]
    conn.close()

    if not rows:
        raise SystemExit("没有匹配的记录")

    status = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1

    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    recv = [t for t in (parse(r["gateway_received_at"]) for r in rows) if t]
    ack = [t for t in (parse(r["acked_at"]) for r in rows) if t]

    # ① 到达速率：按秒分桶取中位数（跨度平均会被首尾轮带偏，只作参考）
    per_sec = {}
    for t in recv:
        key = t.replace(microsecond=0)
        per_sec[key] = per_sec.get(key, 0) + 1
    active = [v for v in per_sec.values() if v > 0]

    # ② 转发吞吐：相邻 acked_at 间隔的中位数（并行下只作参考）
    gaps = []
    acked_sorted = sorted(ack)
    for prev, cur in zip(acked_sorted, acked_sorted[1:]):
        ms = (cur - prev).total_seconds() * 1000
        if 0 <= ms < 60000:
            gaps.append(ms)

    print("=" * 70)
    print("性能统计" + ("  [" + args.where + "]" if args.where else ""))
    print("=" * 70)
    print("  库内总批 :", total_all)
    print("  本次样本 :", len(rows), "批 /", len(rows) * args.batch_size, "条")
    print("  范围     :", rows[0]["batch_id"], "->", rows[-1]["batch_id"])

    print()
    print("  ① 到达速率")
    if active:
        print("     稳态（按秒中位） : %.1f 批/秒" % statistics.median(active))
        print("     峰值（按秒最大） : %d 批/秒" % max(active))
        print("     有数据秒数/总秒数: %d / %d" % (len(active), len(per_sec)))
    if len(recv) >= 2:
        span = (max(recv) - min(recv)).total_seconds()
        print("     首末跨度         : %.2f 秒（跨度平均 %.2f 批/秒）"
              % (span, len(recv) / span if span else 0))

    print()
    print("  ② 转发吞吐")
    if gaps:
        med = statistics.median(gaps)
        print("     相邻确认间隔中位 : %.1f ms" % med)
        print("     折算（用中位）   : %.2f 批/秒 = %.0f 条/秒"
              % (1000.0 / med, 1000.0 / med * args.batch_size))
    if len(ack) >= 2 and len(recv) >= 1:
        total_span = (max(ack) - min(recv)).total_seconds()
        print("     端到端总耗时     : %.2f 秒" % total_span)
        print("     端到端整体速率   : %.2f 批/秒 = %.0f 条/秒"
              % (len(rows) / total_span if total_span else 0,
                 len(rows) / total_span * args.batch_size if total_span else 0))

    print()
    print("  ③ 端到端延迟")
    if lat:
        p50 = pct(lat, 0.50)
        p95 = pct(lat, 0.95)
        print("     P50 : %.1f ms" % p50)
        print("     P95 : %.1f ms    （目标 <= %.0f）%s"
              % (p95, TARGET_P95, "PASS" if p95 <= TARGET_P95 else "FAIL"))
        print("     min / max : %.1f / %.1f ms" % (min(lat), max(lat)))

    print()
    print("  ④ 状态分布（待传 = pending + audit_pending）")
    print("     " + json.dumps(status, ensure_ascii=False))
    pending = status.get("pending", 0)
    audit_pending = status.get("audit_pending", 0)
    print("     pending       : %d" % pending)
    print("     audit_pending : %d" % audit_pending)
    print("     待传合计      : %d    %s"
          % (pending + audit_pending, "已清空" if pending + audit_pending == 0 else "仍在排队"))

    print()
    print("=" * 70)
    ok = (lat and pct(lat, 0.95) <= TARGET_P95
          and pending + audit_pending == 0)
    print("门禁:", "PASS  P95 达标且队列已清空" if ok else "FAIL  见上面各项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
