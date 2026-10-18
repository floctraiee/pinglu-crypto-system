"""签名批次大小对照：逐条 / 10 条一批 / 20 条一批。

同一批 100 条记录，按三种批次大小分批，统计签名次数、签名与验签耗时、P95、
报文大小。输出 CSV 供中心平台页面第⑥块（性能对比）直接读取。

用法
----
    rem 默认：用 SL-001，100 条记录，重复到每个用例累计约 3 秒
    python tests/benchmark_signing.py

    rem 指定设备、语料条数与输出
    python tests/benchmark_signing.py --device WL-001 --records 100 ^
        --out docs/evidence/benchmark_signing_20261014.csv

    rem 每个用例固定重复次数（更快，但样本更少）
    python tests/benchmark_signing.py --repeat 30

指标口径
--------
- `sign_calls` / `verify_calls`：本次测量实际执行的签名 / 验签调用次数。
- `sign_ms_avg` / `verify_ms_avg`：**单次调用**的平均毫秒数。
- `sign_ms_p95` / `verify_ms_p95`：单次调用的 95 分位毫秒数（用最近秩法）。
- `payload_bytes`：该批次 canonical JSON 的字节数（UTF-8、键排序、无空白）。
- `batch_size`：每批记录条数；`batch_count` = 语料条数 / 每批条数（向下取整）。

计时用 `time.perf_counter`，单位毫秒。签名使用固定私钥，
随机数 k 由签名实现内部生成，因此每次测量都是真实开销，不是缓存结果。
"""

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文输出会报 UnicodeEncodeError。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from common_crypto.canonical import canonical_bytes, signed_header  # noqa: E402
from common_crypto.hash_chain import record_hash  # noqa: E402
from common_crypto.merkle import merkle_root  # noqa: E402
from common_crypto.sm import sign_message, verify_message  # noqa: E402
from device_simulator.generator import make_records  # noqa: E402

REGISTRY_PATH = ROOT / "test_data" / "registry_public.json"
KEYS_DIR = ROOT / "keys"
ZERO_HASH = "0" * 64

CSV_COLUMNS = (
    "batch_size",
    "batch_count",
    "sign_calls",
    "sign_ms_total",
    "sign_ms_avg",
    "sign_ms_p95",
    "sign_ms_min",
    "sign_ms_max",
    "verify_calls",
    "verify_ms_total",
    "verify_ms_avg",
    "verify_ms_p95",
    "verify_ms_min",
    "verify_ms_max",
    "payload_bytes",
    "records",
    "tested_at",
    "device_scale",
    "note",
)


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def percentile(values, fraction):
    """最近秩法（nearest-rank）算分位数，样本量小时也稳定。"""
    if not values:
        return None

    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def load_device(device_id):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    if device_id not in registry:
        raise SystemExit(
            f"{device_id} 不在登记表 {REGISTRY_PATH.name} 中；"
            "先运行 python init_project.py"
        )

    key_path = KEYS_DIR / f"{device_id}.json"

    if not key_path.exists():
        raise SystemExit(f"缺少设备私钥：{key_path}；先运行 python init_project.py")

    saved = json.loads(key_path.read_text(encoding="utf-8"))
    registration = registry[device_id]

    if saved["public_key"] != registration["public_key"]:
        raise SystemExit(f"{device_id} 私钥与登记表公钥不一致，无法继续")

    return registration, saved["private_key"]


def build_corpus(registration, records, seed, start_time):
    """造一段连续记录作为测量语料（摘要链自洽）。"""
    return make_records(
        registration=registration,
        batch_id="benchmark-000001",
        start_sequence=1,
        previous_hash=ZERO_HASH,
        start_time=start_time,
        count=records,
        seed=seed,
    )


def make_batches(corpus, batch_size, registration, private_key):
    """把语料切成等长批次，每个批次都真实签名一次。"""
    batches = []
    total = len(corpus) // batch_size

    for index in range(total):
        segment = corpus[index * batch_size:(index + 1) * batch_size]
        # 复制一份，改写 batch_id 与序号连续性（摘要链要自洽）。
        records = []
        previous = ZERO_HASH if index == 0 else batches[-1]["records"][-1]["record_hash"]

        for offset, source in enumerate(segment):
            record = dict(source)
            record["sequence"] = index * batch_size + offset + 1
            record["previous_hash"] = previous
            record["batch_id"] = "benchmark-000001"
            record["record_hash"] = record_hash(record)
            previous = record["record_hash"]
            records.append(record)

        batch = {
            "version": 1,
            "device_id": registration["device_id"],
            "batch_id": "benchmark-000001",
            "start_sequence": records[0]["sequence"],
            "end_sequence": records[-1]["sequence"],
            "count": len(records),
            "start_time": records[0]["timestamp"],
            "end_time": records[-1]["timestamp"],
            "merkle_root": merkle_root([r["record_hash"] for r in records]),
            "records": records,
        }
        batches.append(batch)

    # 统一签名一次，得到真实报文大小。
    for batch in batches:
        batch["signature"] = sign_message(
            private_key, registration["public_key"],
            canonical_bytes(signed_header(batch)),
        )

    return batches


def measure(batch_size, batches, registration, private_key, min_seconds, max_repeat):
    """对一个批次大小做签名/验签计时，返回统计行。"""
    public_key = registration["public_key"]
    sign_times = []
    verify_times = []
    payload_bytes = None

    started = time.perf_counter()
    repeat = 0
    round_index = 0

    while repeat < max_repeat:
        # 一轮 = 对每个批次各签名一次；这样样本覆盖不同批次内容。
        for batch in batches:
            header_bytes = canonical_bytes(signed_header(batch))

            if payload_bytes is None:
                payload_bytes = len(canonical_bytes(batch))

            mark = time.perf_counter()
            signature = sign_message(private_key, public_key, header_bytes)
            sign_times.append((time.perf_counter() - mark) * 1000.0)

            mark = time.perf_counter()
            verify_message(public_key, header_bytes, signature)
            verify_times.append((time.perf_counter() - mark) * 1000.0)

        repeat += len(batches)
        round_index += 1

        if time.perf_counter() - started >= min_seconds and repeat >= len(batches):
            break

    return {
        "batch_size": batch_size,
        "batch_count": len(batches),
        "sign_calls": len(sign_times),
        "sign_ms_total": round(sum(sign_times), 4),
        "sign_ms_avg": round(statistics.fmean(sign_times), 5),
        "sign_ms_p95": round(percentile(sign_times, 0.95), 5),
        "sign_ms_min": round(min(sign_times), 5),
        "sign_ms_max": round(max(sign_times), 5),
        "verify_calls": len(verify_times),
        "verify_ms_total": round(sum(verify_times), 4),
        "verify_ms_avg": round(statistics.fmean(verify_times), 5),
        "verify_ms_p95": round(percentile(verify_times, 0.95), 5),
        "verify_ms_min": round(min(verify_times), 5),
        "verify_ms_max": round(max(verify_times), 5),
        "payload_bytes": payload_bytes,
        "records": len(batches) * batch_size,
        "rounds": round_index,
    }


def main():
    parser = argparse.ArgumentParser(
        description="签名批次大小对照：逐条 / 10 条一批 / 20 条一批",
    )
    parser.add_argument("--device", default="SL-001", help="用于测量的设备 ID")
    parser.add_argument("--records", type=int, default=100, help="语料记录条数")
    parser.add_argument("--sizes", default="1,10,20",
                        help="逗号分隔的批次大小，默认 1,10,20")
    parser.add_argument("--repeat", type=int, default=None,
                        help="每个用例的签名调用次数上限（默认按时间自动决定）")
    parser.add_argument("--min-seconds", type=float, default=3.0,
                        help="每个用例至少测量的秒数（默认 3 秒）")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out", type=Path, default=None,
                        help="输出 CSV；默认 docs/evidence/benchmark_signing_<日期>.csv")
    parser.add_argument("--device-scale", default=None,
                        help="设备规模说明，写入 CSV 的 device_scale 列")
    parser.add_argument("--note", default="", help="附加说明，写入 CSV 的 note 列")
    args = parser.parse_args()

    sizes = []
    for item in args.sizes.split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item)
        if value < 1:
            raise SystemExit("批次大小必须是正整数")
        if value > args.records:
            raise SystemExit(f"批次大小 {value} 超过语料条数 {args.records}")
        sizes.append(value)

    if not sizes:
        raise SystemExit("至少需要一个批次大小")

    registration, private_key = load_device(args.device)

    start_time = datetime(2026, 10, 14, 15, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    corpus = build_corpus(registration, args.records, args.seed, start_time)

    print("=" * 78)
    print("签名批次大小对照")
    print(f"设备 {args.device}（{registration['device_type']}）  "
          f"语料 {args.records} 条  "
          f"批次大小 {sizes}")
    if args.repeat:
        print(f"每个用例签名调用上限 {args.repeat} 次")
    else:
        print(f"每个用例至少测量 {args.min_seconds} 秒")
    print("=" * 78)

    rows = []

    for batch_size in sizes:
        batches = make_batches(corpus, batch_size, registration, private_key)

        max_repeat = args.repeat if args.repeat else 200000
        row = measure(
            batch_size, batches, registration, private_key,
            args.min_seconds, max_repeat,
        )
        rows.append(row)

        print(
            f"每批 {batch_size:>2} 条 | 批次数 {row['batch_count']:>3} | "
            f"签名 {row['sign_calls']:>4} 次 均值 {row['sign_ms_avg']:>8.3f} ms "
            f"P95 {row['sign_ms_p95']:>8.3f} ms | "
            f"验签 均值 {row['verify_ms_avg']:>8.3f} ms "
            f"P95 {row['verify_ms_p95']:>8.3f} ms | "
            f"报文 {row['payload_bytes']:>7} B"
        )

    tested_at = now_iso().split("T")[0]
    device_scale = args.device_scale or f"1 台设备（{args.device}）"
    note_base = args.note or "同一批语料按不同批次大小分批的签名对照"

    out = args.out
    if out is None:
        out = ROOT / "docs" / "evidence" / f"benchmark_signing_{tested_at.replace('-', '')}.csv"

    out = ensure_writable_output(out)
    write_csv(rows, out, tested_at, device_scale, note_base, args.records)

    print("")
    print(f"CSV 已写出：{out}")
    print(f"测试日期 {tested_at} · 设备规模 {device_scale}")
    print("页面第⑥块会自动读取该 CSV（放过 docs/evidence/ 或仓库根目录即可）。")

    return 0


def ensure_writable_output(path):
    """返回实际可写的输出路径。

    docs/evidence 在受限环境里可能建不出来，此时退到仓库根目录，
    两者都在页面第⑥块的查找范围里。
    """
    candidates = [path]

    if path.parent.name == "evidence" and path.parent.parent.name == "docs":
        candidates.append(ROOT / path.name)

    last_error = None

    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            probe = candidate.parent / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError as exc:
            last_error = exc
            continue

    raise SystemExit(
        f"没有可写的输出目录（最后一次错误：{last_error}）。"
        "请用 --out 指定一个可写路径。"
    )


def write_csv(rows, out, tested_at, device_scale, note_base, records_total):
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for row in rows:
            record = dict(row)
            record["tested_at"] = tested_at
            record["device_scale"] = device_scale
            record["note"] = (
                f"{note_base}；语料 {records_total} 条；"
                f"共 {row['rounds']} 轮往返"
            )
            writer.writerow({key: record.get(key) for key in CSV_COLUMNS})

    return out


if __name__ == "__main__":
    raise SystemExit(main())
