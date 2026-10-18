"""端到端打通验证：B 端网关缓存 → 转发 → 队长中心。

不依赖 MQTT：直接把一批真实签名批次塞进网关自己的缓存库，再调用网关的
`edge_gateway.forward.run_once()`，看中心是否 accepted、网关是否落成终态。

用法（先起中心，端口用 --port 指定）：
    python 验证脚本_端到端打通.py --center http://127.0.0.1:8160 --device WL-001
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 必须在 import edge_gateway.storage 之前把库指到临时目录
GWDB = ROOT / "_tmp" / "e2e" / "edge_cache.db"
GWDB.parent.mkdir(parents=True, exist_ok=True)
os.environ["DB_PATH"] = str(GWDB)

from common_crypto.sm import sign_message  # noqa: E402
from device_simulator.batch import build_batch  # noqa: E402
from device_simulator.generator import make_records  # noqa: E402


def load_device(device_id):
    registry = json.loads(
        (ROOT / "test_data" / "registry_public.json").read_text(encoding="utf-8")
    )
    if device_id not in registry:
        raise SystemExit(f"{device_id} 不在登记表里：{sorted(registry)}")

    key_path = ROOT / "keys" / f"{device_id}.json"
    if not key_path.exists():
        raise SystemExit(f"缺少私钥：{key_path}")

    return registry[device_id], json.loads(
        key_path.read_text(encoding="utf-8")
    )["private_key"]


def main():
    parser = argparse.ArgumentParser(description="B 端网关 → 中心 端到端打通验证")
    parser.add_argument("--center", default="http://127.0.0.1:8160")
    parser.add_argument("--device", default="WL-001")
    parser.add_argument("--count", type=int, default=5, help="每批记录条数")
    parser.add_argument("--batches", type=int, default=3, help="生成几个批次")
    args = parser.parse_args()

    from edge_gateway import config, forward, storage

    if Path(config.DB_PATH).resolve() != GWDB.resolve():
        raise SystemExit(f"DB_PATH 未生效：{config.DB_PATH}")

    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(GWDB) + suffix)
        if stale.exists():
            stale.unlink()

    storage.init_db()

    registration, private_key = load_device(args.device)
    device_id = registration["device_id"]

    print("=" * 80)
    print("B 端网关 → 中心 端到端打通验证")
    print("=" * 80)
    print(f"网关缓存库：{GWDB}")
    print(f"中心地址  ：{args.center}")
    print(f"设备      ：{device_id}（{registration['device_type']}）")

    # ---- 造真实签名批次并塞进网关缓存（模拟 MQTT 收到） ----
    base = datetime(2026, 10, 14, 19, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    previous = "0" * 64
    sequence = 1
    made = []

    for number in range(1, args.batches + 1):
        batch_id = f"{device_id}-{number:06d}"
        records = make_records(
            registration=registration,
            batch_id=batch_id,
            start_sequence=sequence,
            previous_hash=previous,
            start_time=base + timedelta(seconds=(number - 1) * args.count),
            count=args.count,
            seed=2026,
        )
        batch = build_batch(records, private_key, registration["public_key"])
        storage.save_received(
            batch,
            json.dumps(batch, ensure_ascii=False),
            {"valid": True, "reason_code": "OK", "affected_sequences": []},
            gateway_received_at=storage.now_iso(),
        )
        made.append(batch_id)
        previous = records[-1]["record_hash"]
        sequence = records[-1]["sequence"] + 1

    print(f"\n已塞入网关缓存 {len(made)} 批：{', '.join(made)}")
    print(f"网关队列状态：{storage.count_by_status()}")

    # ---- 跑网关转发 ----
    print("\n调用 edge_gateway.forward.run_once() …")
    summary = forward.run_once(center_url=args.center, timeout=15)
    print(f"转发结果：{json.dumps(summary, ensure_ascii=False)}")

    status = storage.count_by_status()
    print(f"转发后队列状态：{status}")

    latency = storage.latency_stats()
    print(f"网关延迟统计（网关收到→中心终态）：{json.dumps(latency, ensure_ascii=False)}")

    # ---- 判定 ----
    accepted = summary.get("accepted", 0)
    ok = (
        summary.get("sent") == len(made)
        and accepted == len(made)
        and summary.get("rejected", 0) == 0
        and summary.get("conflict", 0) == 0
        and summary.get("retry", 0) == 0
    )

    print("")
    print("=" * 80)
    if ok:
        print(f"打通成功 OK：{accepted}/{len(made)} 批被中心 accepted")
    else:
        print("打通失败 FAIL，请检查上面的转发结果")
    print("=" * 80)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
