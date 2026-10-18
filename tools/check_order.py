# -*- coding: utf-8 -*-
"""检查网关库里的序号连续性：并行转发后必须仍然"每台设备 1 条链、序号无断层"。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe test_data\\perf_100\\check_order.py --db test_data\\perf_100\\edge_cache_round5.db
    .venv\\Scripts\\python.exe test_data\\perf_100\\check_order.py --db ... --where "batch_no > 9"
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--where", default="", help='SQL 条件，例如 "batch_no > 9"')
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

    sql = ("SELECT device_id, batch_id, batch_no, first_seq, last_seq, status "
           "FROM pending_batches")
    if args.where:
        sql += " WHERE (" + args.where + ")"
    sql += " ORDER BY device_id, batch_no"
    rows = conn.execute(sql).fetchall()
    conn.close()

    if not rows:
        raise SystemExit("没有匹配的记录")

    by_dev = defaultdict(list)
    for r in rows:
        by_dev[r["device_id"]].append(r)

    print("=" * 66)
    print("序号连续性检查")
    print("=" * 66)
    print("  设备数   :", len(by_dev))
    print("  记录数   :", len(rows))
    print("  状态分布 :", json.dumps(
        dict(Counter(r["status"] for r in rows)), ensure_ascii=False))

    problems = []
    for dev in sorted(by_dev):
        chain = by_dev[dev]
        for prev, cur in zip(chain, chain[1:]):
            if cur["first_seq"] != prev["last_seq"] + 1:
                problems.append((dev, prev["batch_id"], prev["last_seq"],
                                 cur["batch_id"], cur["first_seq"]))
        # 同设备内部 batch_no 必须严格递增
        nos = [r["batch_no"] for r in chain]
        if nos != sorted(nos) or len(nos) != len(set(nos)):
            problems.append((dev, "batch_no 乱序或重复", str(nos)))

    print()
    if problems:
        print("  ❌ 发现 %d 处问题：" % len(problems))
        for item in problems[:10]:
            print("    ", item)
        print()
        print("结论: FAIL  并行破坏了顺序，立即 set FORWARD_SHARDS=8 回退")
        return 1

    print("  序号断层 : 0")
    print("  batch_no : 每台设备严格递增、无重复")
    print()
    print("结论: PASS  每台设备一条连续链，并行未破坏顺序")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
