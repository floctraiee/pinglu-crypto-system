"""搭建页面实测环境：独立工作库 + 七类设备合法批次 + 几个异常批次。

用法：
    python 验证脚本_搭建页面数据.py <目标数据库路径>

不会改动 center_platform/data/center.db，也不会改动 simulator.db。
"""
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TARGET = Path(sys.argv[1]).resolve()
TARGET.parent.mkdir(parents=True, exist_ok=True)

if TARGET.exists():
    TARGET.unlink()

from center_platform import storage  # noqa: E402

storage.DB_PATH = TARGET
storage.init_db()

from common_crypto.batch import PAYLOAD_FIELDS  # noqa: E402
from common_crypto.canonical import canonical_bytes, signed_header  # noqa: E402
from common_crypto.hash_chain import ZERO_HASH, record_hash  # noqa: E402
from common_crypto.merkle import merkle_root  # noqa: E402
from common_crypto.sm import generate_keypair, sign_message  # noqa: E402

DEVICE_TYPES = [
    ("WL-001", "water_level"),
    ("WE-001", "weather"),
    ("NM-001", "navigation_mark"),
    ("SL-001", "slope"),
    ("LK-001", "lock"),
    ("DR-001", "drone"),
    ("SB-001", "survey_boat"),
]

BASE_TIME = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
REGISTRY = {}


def payload_for(device_type, index):
    return {field: f"{10 + index}.{index:02d}" for field in PAYLOAD_FIELDS[device_type]}


def make_records(device_id, device_type, start, count, previous, batch_id, start_time):
    records = []
    previous_hash = previous

    for offset in range(count):
        sequence = start + offset
        record = {
            "version": 1,
            "device_id": device_id,
            "device_type": device_type,
            "timestamp": (start_time + timedelta(seconds=offset)).isoformat(),
            "longitude": "108.500000",
            "latitude": "22.000000",
            "sequence": sequence,
            "batch_id": batch_id,
            "payload": payload_for(device_type, sequence),
            "status": "normal",
            "previous_hash": previous_hash,
        }
        record["record_hash"] = record_hash(record)
        previous_hash = record["record_hash"]
        records.append(record)

    return records, previous_hash


def make_batch(device_id, device_type, private_key, public_key, records):
    batch = {
        "version": 1,
        "device_id": device_id,
        "batch_id": records[0]["batch_id"],
        "start_sequence": records[0]["sequence"],
        "end_sequence": records[-1]["sequence"],
        "count": len(records),
        "start_time": records[0]["timestamp"],
        "end_time": records[-1]["timestamp"],
        "merkle_root": merkle_root([r["record_hash"] for r in records]),
        "records": records,
    }
    batch["signature"] = sign_message(
        private_key, public_key, canonical_bytes(signed_header(batch))
    )
    return batch


def post(batch):
    return storage.process_batch(batch, __import__(
        "center_platform.validator", fromlist=["validate_batch"]
    ).validate_batch)


accepted = []
rejected = []
duplicates = []

for position, (device_id, device_type) in enumerate(DEVICE_TYPES):
    private_key, public_key = generate_keypair()
    storage.register_device(
        device_id, device_type, public_key,
        longitude="108.500000", latitude="22.000000",
    )
    REGISTRY[device_id] = {
        "device_id": device_id,
        "device_type": device_type,
        "longitude": "108.500000",
        "latitude": "22.000000",
        "public_key": public_key,
    }

    start_time = BASE_TIME + timedelta(minutes=position)

    # 第 1 批：合法入库
    records, last_hash = make_records(
        device_id, device_type, 1, 5, ZERO_HASH,
        f"{device_id}-000001", start_time,
    )
    batch = make_batch(device_id, device_type, private_key, public_key, records)
    result = post(batch)
    accepted.append((device_id, batch["batch_id"], result["status"]))

    # 第 2 批：合法续链
    records2, last_hash = make_records(
        device_id, device_type, 6, 3, last_hash,
        f"{device_id}-000002", start_time + timedelta(minutes=5),
    )
    batch2 = make_batch(device_id, device_type, private_key, public_key, records2)
    result2 = post(batch2)
    accepted.append((device_id, batch2["batch_id"], result2["status"]))

    # 重复提交第 1 批
    duplicates.append(post(batch)["status"])

    # 异常批次必须从第 2 批的链尾正确续上，否则会先被判序号/链错误。
    next_start_time = start_time + timedelta(minutes=10)

    def continue_records(count, number, previous=last_hash):
        return make_records(
            device_id, device_type, 9, count, previous,
            f"{device_id}-{number:06d}", next_start_time,
        )

    def resign(built):
        built["signature"] = sign_message(
            private_key, public_key, canonical_bytes(signed_header(built))
        )
        return built

    # 篡改测量值：摘要不匹配，应精确定位序号（轮换设备类型覆盖多种 payload）
    records3, _ = continue_records(3, 3)
    tampered = make_batch(device_id, device_type, private_key, public_key, records3)
    first_field = list(tampered["records"][1]["payload"])[0]
    tampered["records"][1]["payload"][first_field] = "999.00"
    rejected.append((device_id, tampered["batch_id"], post(tampered)["reason_code"]))

    # 逐台设备再补一种不同异常，保证审计页覆盖多种 reason_code
    if position == 0:
        forged = resign(make_batch(
            device_id, device_type, private_key, public_key,
            continue_records(2, 4)[0],
        ))
        forged["signature"] = "0" * 128
        rejected.append((device_id, forged["batch_id"], post(forged)["reason_code"]))

    elif position == 1:
        unknown_records, _ = make_records(
            "XX-999", device_type, 9, 2, last_hash, "XX-999-000001", next_start_time
        )
        unknown = make_batch("XX-999", device_type, private_key, public_key, unknown_records)
        rejected.append(("XX-999", unknown["batch_id"], post(unknown)["reason_code"]))

    elif position == 2:
        gap_records, _ = continue_records(2, 5)
        for record in gap_records:
            record["sequence"] += 5
            record["previous_hash"] = "1" * 64
            record["record_hash"] = record_hash(record)
        gap = resign({
            "version": 1,
            "device_id": device_id,
            "batch_id": f"{device_id}-000005",
            "start_sequence": gap_records[0]["sequence"],
            "end_sequence": gap_records[-1]["sequence"],
            "count": len(gap_records),
            "start_time": gap_records[0]["timestamp"],
            "end_time": gap_records[-1]["timestamp"],
            "merkle_root": merkle_root([r["record_hash"] for r in gap_records]),
            "records": gap_records,
        })
        rejected.append((device_id, gap["batch_id"], post(gap)["reason_code"]))

    elif position == 3:
        missing_records, _ = continue_records(3, 6)
        missing = resign({
            "version": 1,
            "device_id": device_id,
            "batch_id": f"{device_id}-000006",
            "start_sequence": missing_records[0]["sequence"],
            "end_sequence": missing_records[-1]["sequence"],
            "count": 2,
            "start_time": missing_records[0]["timestamp"],
            "end_time": missing_records[-1]["timestamp"],
            "merkle_root": merkle_root([r["record_hash"] for r in missing_records]),
            "records": missing_records[:1] + missing_records[2:],
        })
        rejected.append((device_id, missing["batch_id"], post(missing)["reason_code"]))

    elif position == 4:
        conflict_records, _ = continue_records(2, 7)
        conflict = make_batch(device_id, device_type, private_key, public_key, conflict_records)
        # 与已入库的第 1 批同批次号但内容不同 → 冲突
        conflict["batch_id"] = f"{device_id}-000001"
        for record in conflict["records"]:
            record["batch_id"] = conflict["batch_id"]
        resign(conflict)
        rejected.append((device_id, conflict["batch_id"], post(conflict)["reason_code"]))

    elif position == 5:
        root_records, _ = continue_records(3, 8)
        root_bad = make_batch(device_id, device_type, private_key, public_key, root_records)
        field = list(root_bad["records"][2]["payload"])[0]
        root_bad["records"][2]["payload"][field] = "999.00"
        root_bad["records"][2]["record_hash"] = record_hash(root_bad["records"][2])
        resign(root_bad)
        rejected.append((device_id, root_bad["batch_id"], post(root_bad)["reason_code"]))

    elif position == 6:
        old = {
            "header": signed_header(batch),
            "records": batch["records"],
            "signature": batch["signature"],
        }
        rejected.append((device_id, "（旧结构）", post(old)["reason_code"]))


# 造一个"未登记类型"的痕迹？不需要：七类都登记了。
stats = storage.get_stats()

# 顺手写一份公开登记表副本，供页面与指纹核对（不含私钥）。
registry_copy = TARGET.parent / "registry_public_page_test.json"
registry_copy.write_text(
    json.dumps(REGISTRY, ensure_ascii=False, indent=2), encoding="utf-8"
)

print("库：", TARGET)
print("中心统计：", json.dumps(stats, ensure_ascii=False))
print("合法批次：", len(accepted), "全部 accepted：",
      all(item[2] == "accepted" for item in accepted))
print("重复提交：", duplicates)
print("预期异常：")
for device_id, batch_id, reason in rejected:
    print(f"   {device_id:<8} {batch_id:<20} {reason}")
print("登记表副本：", registry_copy)
