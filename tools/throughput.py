# -*- coding: utf-8 -*-
"""网关吞吐测量：从 acked_at / gateway_received_at 算真实转发速率。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe test_data\\perf_100\\throughput.py --db test_data\\perf_100\\edge_cache_round5.db
    .venv\\Scripts\\python.exe test_data\\perf_100\\throughput.py --db ... --where "batch_no > 3"
"""
import argparse
import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
    parser.add_argument("--where", default="", help="SQL 条件，例如 \"batch_no > 3\"")
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

    sql = ("SELECT device_id, batch_id, batch_no, status, gateway_received_at, acked_at "
           "FROM pending_batches WHERE acked_at IS NOT NULL")
    if args.where:
        sql += " AND (" + args.where + ")"
    sql += " ORDER BY acked_at"
    rows = conn.execute(sql).fetchall()
    conn.close()

    if not rows:
        raise SystemExit("没有已确认的批次")

    acked = [parse(r["acked_at"]) for r in rows]
    recv = [parse(r["gateway_received_at"]) for r in rows]
    recv = [t for t in recv if t]

    print("=" * 66)
    print("样本")
    print("=" * 66)
    print("  已确认批次 :", len(rows))
    print("  范围       :", rows[0]["batch_id"], "->", rows[-1]["batch_id"])
    print("  状态       :", json.dumps(
        {s: sum(1 for r in rows if r["status"] == s) for s in {r["status"] for r in rows}},
        ensure_ascii=False))

    # 服务时间：相邻 acked_at 的间隔（近似单批转发耗时）
    gaps = []
    for i in range(1, len(acked)):
        if acked[i] and acked[i - 1]:
            ms = (acked[i] - acked[i - 1]).total_seconds() * 1000
            if 0 <= ms < 60000:
                gaps.append(ms)

    print()
    print("=" * 66)
    print("① 转发服务时间（相邻 acked_at 间隔，近似单批耗时）")
    print("=" * 66)
    if gaps:
        med = statistics.median(gaps)
        print("  样本数     :", len(gaps))
        print("  平均       : %.1f ms" % (sum(gaps) / len(gaps)))
        print("  中位       : %.1f ms" % med)
        print("  P90        : %.1f ms" % pct(gaps, 0.90))
        print("  最小/最大  : %.1f / %.1f ms" % (min(gaps), max(gaps)))
        print()
        print("  ==> 转发吞吐（用中位数）: %.2f 批/秒" % (1000.0 / med if med else 0))
        print("  ==> 转发吞吐（用平均值）: %.2f 批/秒" % (len(gaps) / (sum(gaps) / 1000.0)))
        print("  ==> 折合记录 : %.0f 条/秒（批大小 %d）" % (
            len(gaps) / (sum(gaps) / 1000.0) * args.batch_size, args.batch_size))
        print("  ==> 单批耗时 %d ms 时，100 条/秒（10 批/秒）需要 <=100 ms" % med)

    print()
    print("=" * 66)
    print("② 到达速率")
    print("=" * 66)
    if len(recv) >= 2:
        span = (max(recv) - min(recv)).total_seconds()
        print("  首末跨度   : %.2f 秒" % span)
        print("  跨度平均   : %.2f 批/秒  = %.0f 条/秒" % (
            len(recv) / span if span else 0,
            len(recv) / span * args.batch_size if span else 0))
        print("  ⚠️ 跨度平均会把首尾几轮算进去，不能当稳态速率看")

        # 按秒分桶：真正该看的是"每秒收到多少批"
        buckets = {}
        for t in recv:
            buckets[t.replace(microsecond=0)] = buckets.get(t.replace(microsecond=0), 0) + 1
        per_sec = sorted(buckets.values())
        active = [v for v in per_sec if v > 0]
        print()
        print("  按秒统计（%d 个有数据的秒）:" % len(active))
        print("    每秒收到批数 中位 : %.1f 批/秒" % statistics.median(active))
        print("    每秒收到批数 最大 : %d 批/秒" % max(active))
        print("    有数据的秒数      : %d   空秒数: %d" % (
            len(active), len(per_sec) - len(active)))
        print("  ==> 稳态到达速率 = 每秒收到批数的中位数（上面那个）")

    print()
    print("=" * 66)
    print("③ 端到端耗时（首批收到 -> 末批确认）")
    print("=" * 66)
    if recv:
        first = min(recv)
        last = max(t for t in acked if t)
        total = (last - first).total_seconds()
        print("  开始       :", first.isoformat())
        print("  结束       :", last.isoformat())
        print("  总耗时     : %.2f 秒" % total)
        print("  整体速率   : %.2f 批/秒  = %.0f 条/秒" % (
            len(rows) / total if total else 0,
            len(rows) / total * args.batch_size if total else 0))
        print()
        print("  说明：这个速率包含队列等待，会低于①的真实转发能力")
        print("       判断能否撑住 100 条/秒，看①的『单批服务时间』<=100ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
