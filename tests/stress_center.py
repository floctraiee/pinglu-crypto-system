"""多设备压力测试：真实生成数、中心验证数、入库数与端到端延迟 P95。

任务书要求「100 台设备每秒每台 1 条、连续 30 分钟」，分别记录
A 端生成数、B 端接收数、中心验证数、入库数，并给出实时 P95。

本脚本负责其中**队长可独立完成的部分**：A 端生成 + 中心接收/验证/入库。
B 端（MQTT 边缘网关）尚未就绪，因此：
- `--url` 模式（默认 direct）直接把批次 POST 到中心，这是**中心侧真实**的
  验证与入库耗时与延迟；
- `--url` 换成网关地址即可走网关链路，结果表会自动多出 B 端列（待网关提供
  `/status` 后由 --gateway-status 读回）；
- 网关未就绪时，报告里 B 端接收数与 gateway_received_at→验证完成 的实时 P95
  如实留空并标注原因，不编造数字。

用法
----
    rem 完整目标规模（100 台 × 30 分钟 × 1 条/秒，每批 10 条 → 18000 批）
    python tests/stress_center.py --devices 100 --minutes 30 --rate 1 --batch-size 10

    rem 快速验证（3 台 × 30 秒）
    python tests/stress_center.py --devices 3 --minutes 0.5 --rate 1 --batch-size 5

    rem 只测生成与签名开销，不提交中心
    python tests/stress_center.py --devices 100 --minutes 1 --no-submit

    rem 分批预生成后再统一提交（生成与提交解耦，便于记录两段耗时）
    python tests/stress_center.py --devices 100 --minutes 30 --generate-only
    python tests/stress_center.py --devices 100 --minutes 30 --submit-only

时间压缩
--------
`--time-scale` 把"设备端 30 分钟"的**逻辑时间戳**压缩到更短的真实运行时间：
设备端记录的时间戳仍按 1 条/秒推进（保证 30 分钟的数据量），但真实等待被压缩。
默认 1.0（不做压缩，真实按节奏跑）。报告里会写明实际用了哪种。

延迟口径
--------
    center_latency_ms = POST 发出 → 中心返回持久化结果
报告给出均值、P50、P95、P99 与最大值，并单独统计 rejected/duplicate 的延迟，
避免异常路径混进正常路径的 P95。
"""

import argparse
import csv
import json
import os
import platform
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
from common_crypto.hash_chain import ZERO_HASH, record_hash  # noqa: E402
from common_crypto.merkle import merkle_root  # noqa: E402
from common_crypto.sm import (  # noqa: E402
    generate_keypair,
    sign_message,
    verify_message,
)
from device_simulator.generator import make_records  # noqa: E402

REGISTRY_PATH = ROOT / "test_data" / "registry_public.json"
KEYS_DIR = ROOT / "keys"
DEFAULT_CENTER = "http://127.0.0.1:8000"


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def percentile(values, fraction):
    if not values:
        return None

    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def load_registry(limit, repair_keys=False, registry_path=None, keys_dir=None, skip=None):
    """读公开登记表并逐台校验私钥；--repair-keys 时就地重新生成不一致的密钥。

    历史清理脚本可能留下"私钥文件与登记表公钥不一致"的设备（例如 WL-900），
    这种设备在任何提交环节都会被判 UNKNOWN_DEVICE / 签名不通过。
    这里默认直接报错并给出修复命令；加 --skip-devices 排除它，
    或加 --repair-keys 现场重新生成（注意：登记表与中心库要同步重建）。
    """
    registry_path = Path(registry_path) if registry_path else REGISTRY_PATH
    keys_dir = Path(keys_dir) if keys_dir else KEYS_DIR
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    skip = set(skip or ())

    if skip:
        dropped = sorted(device_id for device_id in registry if device_id in skip)
        registry = {
            device_id: item
            for device_id, item in registry.items()
            if device_id not in skip
        }
        if dropped:
            print(f"按 --skip-devices 排除：{', '.join(dropped)}")

    if limit is not None:
        device_ids = sorted(registry)[:limit]
        registry = {device_id: registry[device_id] for device_id in device_ids}

    devices = []
    repaired = []
    broken = []

    for device_id in sorted(registry):
        key_path = keys_dir / f"{device_id}.json"
        item = registry[device_id]

        if not key_path.exists():
            if not repair_keys:
                raise SystemExit(
                    f"{device_id} 缺少设备私钥：{key_path}\n"
                    "运行 python init_project.py 生成，或加 --repair-keys 现场修复"
                )
            saved = None
        else:
            try:
                saved = json.loads(key_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None

        healthy = False

        if saved and saved.get("public_key") == item.get("public_key"):
            probe = f"{device_id} 压力测试自检".encode("utf-8")

            try:
                healthy = verify_message(
                    saved["public_key"], probe,
                    sign_message(saved["private_key"], saved["public_key"], probe),
                )
            except (KeyError, ValueError, TypeError):
                healthy = False

        if not healthy:
            if repair_keys:
                private_key, public_key = generate_keypair()
                probe = f"{device_id} 压力测试自检".encode("utf-8")

                if not verify_message(
                    public_key, probe, sign_message(private_key, public_key, probe)
                ):
                    raise SystemExit(f"{device_id} 重新生成的密钥无法自洽")

                saved = {"private_key": private_key, "public_key": public_key}
                item["public_key"] = public_key
                repaired.append(device_id)

                # keys/ 可能被文件权限挡住；写不进去只警告，不中断测量。
                try:
                    key_path.parent.mkdir(parents=True, exist_ok=True)
                    key_path.write_text(
                        json.dumps(saved, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    print(f"  提示：无法写回 {key_path}（{exc}），仅在本次运行内生效")
            else:
                broken.append(device_id)
                continue

        devices.append((device_id, item, saved["private_key"]))

    if repaired:
        print(f"已现场修复 {len(repaired)} 台设备的密钥：{', '.join(repaired)}")

        try:
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  已同步登记表：{registry_path}")
        except OSError as exc:
            print(f"  提示：无法写回登记表（{exc}）")

    if broken:
        print("")
        print(
            f"警告：{len(broken)} 台设备的私钥与登记表不一致，已跳过："
            f"{', '.join(broken)}"
        )
        print(
            "  这些设备提交时会判 UNKNOWN_DEVICE / 签名不通过，"
            "属于历史清理脚本留下的密钥-登记表错配。"
        )
        print("  修复：python init_project.py（会重新生成并同步登记表）")
        print("")

    if not devices:
        raise SystemExit("没有可用设备，已中止")

    return devices


def post_batch(url, batch, timeout):
    request = urllib.request.Request(
        url.rstrip("/") + "/batches",
        data=json.dumps({"batch": batch}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            elapsed = (time.perf_counter() - started) * 1000.0
            return body, elapsed, None
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        text = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except ValueError:
            body = {"status": "http_error", "reason_code": f"HTTP_{exc.code}"}
        return body, elapsed, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        return {"status": "transport_error", "reason_code": "TRANSPORT"}, elapsed, str(exc)


def generate_device_batches(registration, private_key, batches, batch_size,
                            sequence_start, previous_hash, start_time, seed):
    """为单台设备按批生成并签名；返回批次列表与新的链尾。"""
    produced = []
    sequence = sequence_start
    chain_hash = previous_hash
    timestamp = start_time

    for number in range(1, batches + 1):
        batch_id = f"{registration['device_id']}-{number:06d}"
        records = make_records(
            registration=registration,
            batch_id=batch_id,
            start_sequence=sequence,
            previous_hash=chain_hash,
            start_time=timestamp,
            count=batch_size,
            seed=seed,
        )

        batch = {
            "version": 1,
            "device_id": registration["device_id"],
            "batch_id": batch_id,
            "start_sequence": records[0]["sequence"],
            "end_sequence": records[-1]["sequence"],
            "count": len(records),
            "start_time": records[0]["timestamp"],
            "end_time": records[-1]["timestamp"],
            "merkle_root": merkle_root([r["record_hash"] for r in records]),
            "records": records,
        }
        batch["signature"] = sign_message(
            private_key, registration["public_key"],
            canonical_bytes(signed_header(batch)),
        )
        produced.append(batch)

        sequence = records[-1]["sequence"] + 1
        chain_hash = records[-1]["record_hash"]
        timestamp = datetime.fromisoformat(records[-1]["timestamp"]) + timedelta(seconds=1)

    return produced, sequence, chain_hash, timestamp


def summarize_latencies(values):
    if not values:
        return {
            "count": 0, "mean": None, "p50": None, "p95": None,
            "p99": None, "max": None,
        }

    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 2),
        "p50": round(percentile(values, 0.50), 2),
        "p95": round(percentile(values, 0.95), 2),
        "p99": round(percentile(values, 0.99), 2),
        "max": round(max(values), 2),
    }


def main():
    parser = argparse.ArgumentParser(
        description="多设备压力测试：生成数、中心验证数、入库数与延迟 P95",
    )
    parser.add_argument("--devices", type=int, default=100, help="设备台数（默认 100）")
    parser.add_argument("--minutes", type=float, default=30.0,
                        help="每台设备的逻辑时长（分钟，默认 30）")
    parser.add_argument("--rate", type=float, default=1.0,
                        help="每台设备每秒生成记录数（默认 1）")
    parser.add_argument("--batch-size", type=int, default=10, help="每批记录数（默认 10）")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--url", default=DEFAULT_CENTER, help="中心地址")
    parser.add_argument("--timeout", type=float, default=60.0, help="单次请求超时秒数")
    parser.add_argument("--time-scale", type=float, default=1.0,
                        help="真实等待压缩系数；1.0 表示按真实节奏")
    parser.add_argument("--no-submit", action="store_true", help="只生成与签名，不提交")
    parser.add_argument("--generate-only", action="store_true",
                        help="只生成并把批次写入 --store，不提交")
    parser.add_argument("--submit-only", action="store_true",
                        help="只从 --store 读回批次并提交")
    parser.add_argument("--store", type=Path, default=None,
                        help="批次暂存目录（generate-only / submit-only 共用）")
    parser.add_argument("--out-csv", type=Path, default=None, help="汇总 CSV 输出")
    parser.add_argument("--out-json", type=Path, default=None, help="汇总 JSON 输出")
    parser.add_argument("--label", default="", help="本次运行的标签，写入报告")
    parser.add_argument("--repair-keys", action="store_true",
                        help="现场重新生成与登记表不一致的设备密钥后再继续")
    parser.add_argument("--skip-devices", default="",
                        help="逗号分隔，跳过这些设备（例如密钥与登记表不一致的历史设备）")
    parser.add_argument("--registry", type=Path, default=None,
                        help="公开登记表路径（默认 test_data/registry_public.json）")
    parser.add_argument("--keys-dir", type=Path, default=None,
                        help="设备私钥目录（默认 keys/）")
    args = parser.parse_args()

    if args.devices < 1 or args.batch_size < 1 or args.rate <= 0 or args.minutes <= 0:
        raise SystemExit("--devices、--batch-size、--minutes 必须为正，--rate 必须大于 0")

    if args.time_scale <= 0:
        raise SystemExit("--time-scale 必须大于 0")

    total_seconds = args.minutes * 60.0
    records_per_device = int(total_seconds * args.rate)
    batches_per_device = max(1, records_per_device // args.batch_size)
    total_batches = batches_per_device * args.devices
    total_records = batches_per_device * args.batch_size * args.devices

    skip = [item.strip() for item in args.skip_devices.split(",") if item.strip()]
    devices = load_registry(
        args.devices,
        repair_keys=args.repair_keys,
        registry_path=args.registry,
        keys_dir=args.keys_dir,
        skip=skip,
    )
    started_at = now_iso()
    wall_start = time.perf_counter()

    print("=" * 82)
    print("多设备压力测试")
    print(f"设备 {len(devices)} 台 × 逻辑 {args.minutes:g} 分钟 × {args.rate:g} 条/秒 "
          f"→ 每台 {records_per_device} 条 / {batches_per_device} 批")
    print(f"合计约 {total_records} 条记录 / {total_batches} 个批次，每批 {args.batch_size} 条")
    print(f"开始 {started_at}")
    if args.time_scale != 1.0:
        print(f"时间压缩系数 {args.time_scale:g}（记录时间戳仍按 1 条/秒推进）")
    print("=" * 82)

    base_time = datetime(2026, 10, 14, 16, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    generated = 0
    sign_seconds = 0.0
    generate_wall = 0.0
    pending = []

    # ---------------- 生成阶段 ----------------
    if not args.submit_only:
        print("阶段 1/2：设备端生成与签名…")
        generate_start = time.perf_counter()

        for index, (device_id, registration, private_key) in enumerate(devices, start=1):
            mark = time.perf_counter()
            batches, _, _, _ = generate_device_batches(
                registration, private_key, batches_per_device, args.batch_size,
                sequence_start=1, previous_hash=ZERO_HASH,
                start_time=base_time + timedelta(seconds=index),
                seed=args.seed,
            )
            sign_seconds += time.perf_counter() - mark
            generated += sum(batch["count"] for batch in batches)

            # 默认在内存里排队等提交；--generate-only 时改写成文件。
            pending.extend(batches)

            if index % max(1, len(devices) // 10) == 0 or index == len(devices):
                elapsed = time.perf_counter() - generate_start
                print(
                    f"  已生成 {index}/{len(devices)} 台，"
                    f"累计 {generated} 条，用时 {elapsed:.1f}s"
                )

        generate_wall = time.perf_counter() - generate_start

        if args.generate_only:
            store = args.store or (ROOT / "_tmp" / "stress_batches")
            store.mkdir(parents=True, exist_ok=True)
            written = 0

            for batch in pending:
                path = store / f"{batch['device_id']}_{batch['batch_id']}.json"
                path.write_text(
                    json.dumps(batch, ensure_ascii=False), encoding="utf-8"
                )
                written += 1

            print(f"  已写出 {written} 个批次 → {store}")
            print(f"生成阶段用时 {generate_wall:.1f}s，签名净耗时 {sign_seconds:.1f}s")
            print("接下来用 --submit-only 提交。")
            return 0

        print(
            f"  生成完成：{generated} 条，用时 {generate_wall:.1f}s "
            f"（签名净耗时 {sign_seconds:.1f}s，"
            f"平均 {sign_seconds / total_batches * 1000:.2f} ms/批）"
        )
    else:
        store = args.store or (ROOT / "_tmp" / "stress_batches")

        if not store.exists():
            raise SystemExit(f"--submit-only 需要 --store 指定的批次目录：{store}")

        for path in sorted(store.glob("*.json")):
            pending.append(json.loads(path.read_text(encoding="utf-8")))

        generated = sum(batch["count"] for batch in pending)
        print(f"阶段 1/2：从 {store} 读回 {len(pending)} 个批次，共 {generated} 条")

    # ---------------- 提交阶段 ----------------
    accepted = rejected = duplicate = transport_errors = 0
    center_records = 0
    normal_latencies = []
    abnormal_latencies = []
    reason_counter = {}
    submit_start = time.perf_counter()

    if not args.no_submit:
        if args.no_submit:
            print("阶段 2/2：已跳过提交（--no-submit）")
        else:
            print(f"阶段 2/2：提交中心 {args.url} …")

        interval = (1.0 / args.rate) if args.time_scale != 1.0 else 0.0

        if not args.no_submit:
            for index, batch in enumerate(pending, start=1):
                body, elapsed, error = post_batch(args.url, batch, args.timeout)
                status = body.get("status")
                reason = body.get("reason_code")

                reason_counter[reason] = reason_counter.get(reason, 0) + 1

                if status == "accepted":
                    accepted += 1
                    center_records += batch["count"]
                    normal_latencies.append(elapsed)
                elif status == "duplicate":
                    duplicate += 1
                    abnormal_latencies.append(elapsed)
                elif status == "rejected":
                    rejected += 1
                    abnormal_latencies.append(elapsed)
                else:
                    transport_errors += 1
                    abnormal_latencies.append(elapsed)

                if error and transport_errors <= 3:
                    print(f"  传输异常：{error}")

                if index % max(1, len(pending) // 10) == 0 or index == len(pending):
                    done = time.perf_counter() - submit_start
                    print(
                        f"  已提交 {index}/{len(pending)} 批，接受 {accepted}，"
                        f"入库 {center_records} 条，用时 {done:.1f}s"
                    )

                if args.time_scale != 1.0 and interval:
                    time.sleep(interval * args.time_scale)

        submit_wall = time.perf_counter() - submit_start
    else:
        submit_wall = 0.0
        print("阶段 2/2：已跳过提交（--no-submit）")

    wall_seconds = time.perf_counter() - wall_start
    normal = summarize_latencies(normal_latencies)
    abnormal = summarize_latencies(abnormal_latencies)

    # 中心侧最终统计（权威入库数）
    center_stats = None

    try:
        with urllib.request.urlopen(
            args.url.rstrip("/") + "/stats", timeout=args.timeout
        ) as response:
            center_stats = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"  读取 /stats 失败：{exc}")

    summary = {
        "label": args.label,
        "started_at": started_at,
        "finished_at": now_iso(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "parameters": {
            "devices": len(devices),
            "minutes": args.minutes,
            "rate": args.rate,
            "batch_size": args.batch_size,
            "time_scale": args.time_scale,
            "url": args.url,
            "submitted": not args.no_submit,
        },
        "target_scale": {
            "records_per_device": records_per_device,
            "batches_per_device": batches_per_device,
            "device_target": 100,
            "minutes_target": 30,
            "records_target": 100 * 30 * 60,
        },
        # A 端
        "generated_records": generated,
        "generate_wall_seconds": round(generate_wall, 2),
        "sign_net_seconds": round(sign_seconds, 2),
        # B 端（网关未就绪，如实留空）
        "gateway_received_batches": None,
        "gateway_received_note": (
            "B 端边缘网关未在本脚本链路内；走 --url 网关地址并由网关写出只读状态后，"
            "才能填 B 端接收数与 gateway_received_at→中心验证完成的实时 P95。"
        ),
        # 中心
        "center_accepted_batches": accepted,
        "center_duplicate_batches": duplicate,
        "center_rejected_batches": rejected,
        "transport_errors": transport_errors,
        "center_stored_records": center_records,
        "center_db_stats": center_stats,
        "center_latency_ms": normal,
        "center_latency_abnormal_ms": abnormal,
        "submit_wall_seconds": round(submit_wall, 2),
        "wall_seconds": round(wall_seconds, 2),
        "throughput_batches_per_second": (
            round(len(pending) / submit_wall, 2) if submit_wall > 0 else None
        ),
        "reason_codes": reason_counter,
    }

    print("")
    print("=" * 82)
    print("结果")
    print("=" * 82)
    print(f"  A 端生成记录数           {generated}")
    print(f"  A 端生成用时             {summary['generate_wall_seconds']}s"
          f"（签名净耗时 {summary['sign_net_seconds']}s）")
    print(f"  B 端接收批次             {'—（网关未就绪，见 gateway_received_note）' if not summary['gateway_received_batches'] else summary['gateway_received_batches']}")
    print(f"  中心接受批次             {accepted}")
    print(f"  中心重复批次             {duplicate}")
    print(f"  中心拒绝批次             {rejected}")
    print(f"  传输失败                 {transport_errors}")
    print(f"  中心入库记录数           {center_records}")
    print(f"  提交用时                 {summary['submit_wall_seconds']}s"
          f"（{summary['throughput_batches_per_second'] or '—'} 批/秒）")
    print("")
    print("  中心侧端到端延迟（POST 发出 → 返回持久化结果）")
    print(f"    有效样本 {normal['count']}  均值 {normal['mean']} ms  "
          f"P50 {normal['p50']} ms  P95 {normal['p95']} ms  "
          f"P99 {normal['p99']} ms  最大 {normal['max']} ms")
    if abnormal["count"]:
        print(f"    异常路径 {abnormal['count']}  均值 {abnormal['mean']} ms  "
              f"P95 {abnormal['p95']} ms")

    if center_stats:
        print("")
        print("  中心库最终统计：", json.dumps(center_stats, ensure_ascii=False))

    print("")
    print(f"  原因码分布：{json.dumps(reason_counter, ensure_ascii=False)}")
    print(f"  总墙钟时间 {summary['wall_seconds']}s")

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  汇总 JSON 已写出：{args.out_json}")

    if args.out_csv:
        args.out_csv.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "label", "started_at", "finished_at", "devices", "minutes", "rate",
            "batch_size", "time_scale", "generated_records",
            "generate_wall_seconds", "sign_net_seconds",
            "gateway_received_batches", "center_accepted_batches",
            "center_duplicate_batches", "center_rejected_batches",
            "transport_errors", "center_stored_records",
            "submit_wall_seconds", "wall_seconds",
            "throughput_batches_per_second",
            "center_latency_count", "center_latency_mean_ms",
            "center_latency_p50_ms", "center_latency_p95_ms",
            "center_latency_p99_ms", "center_latency_max_ms",
            "center_latency_note",
        ]
        row = {
            "label": args.label,
            "started_at": started_at,
            "finished_at": summary["finished_at"],
            "devices": len(devices),
            "minutes": args.minutes,
            "rate": args.rate,
            "batch_size": args.batch_size,
            "time_scale": args.time_scale,
            "generated_records": generated,
            "generate_wall_seconds": summary["generate_wall_seconds"],
            "sign_net_seconds": summary["sign_net_seconds"],
            "gateway_received_batches": "",
            "center_accepted_batches": accepted,
            "center_duplicate_batches": duplicate,
            "center_rejected_batches": rejected,
            "transport_errors": transport_errors,
            "center_stored_records": center_records,
            "submit_wall_seconds": summary["submit_wall_seconds"],
            "wall_seconds": summary["wall_seconds"],
            "throughput_batches_per_second": summary["throughput_batches_per_second"],
            "center_latency_count": normal["count"],
            "center_latency_mean_ms": normal["mean"],
            "center_latency_p50_ms": normal["p50"],
            "center_latency_p95_ms": normal["p95"],
            "center_latency_p99_ms": normal["p99"],
            "center_latency_max_ms": normal["max"],
            "center_latency_note": "POST → 中心返回持久化结果；不含 MQTT 网关段",
        }

        with args.out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerow(row)

        print(f"  汇总 CSV 已写出：{args.out_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
