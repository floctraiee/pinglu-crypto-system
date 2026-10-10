import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.hash_chain import ZERO_HASH, record_hash, verify_chain
from common_crypto.merkle import merkle_root
from common_crypto.sm import sm3_hex, verify_message

from device_simulator.register import register_water_device
from device_simulator.batch import build_batch


def main():
    registration, private_key = register_water_device()
    public_key = registration["public_key"]
    device_id = registration["device_id"]

    records = []
    previous = ZERO_HASH

    start_time = datetime(
        2026, 10, 5, 12, 0, 0,
        tzinfo=timezone(timedelta(hours=8)),
    )

    for sequence in range(1, 11):
        record = {
            "version": 1,
            "device_id": device_id,
            "device_type": registration["device_type"],
            "timestamp": (
                start_time + timedelta(seconds=sequence - 1)
            ).isoformat(),
            "longitude": registration["longitude"],
            "latitude": registration["latitude"],
            "sequence": sequence,
            "batch_id": f"{device_id}-000001",
            "payload": {
                "water_level_m": f"{3.20 + sequence * 0.01:.2f}",
            },
            "status": "normal",
            "previous_hash": previous,
        }

        record["record_hash"] = record_hash(record)
        records.append(record)
        previous = record["record_hash"]

    batch = build_batch(records, private_key, public_key)

    # 用公开信息检查样例，不调用旧批次验证函数
    assert verify_message(
        public_key,
        canonical_bytes(signed_header(batch)),
        batch["signature"],
    ), "批次签名检查失败"

    assert verify_chain(
        batch["records"],
        previous_hash=ZERO_HASH,
        start_sequence=1,
    ) == [], "摘要链检查失败"

    assert batch["merkle_root"] == merkle_root([
        record_hash(record) for record in batch["records"]
    ]), "Merkle 根检查失败"

    assert batch["count"] == len(batch["records"]) == 10
    assert batch["start_sequence"] == 1
    assert batch["end_sequence"] == 10

    output = Path(__file__).resolve().parent / "test_data"
    output.mkdir(exist_ok=True)

    # 公开登记表由 register_devices 单独维护。这里绝不能把七设备登记表
    # 覆盖成只有 WL-001 的样例，否则中心和网关会丢失其余设备的可信公钥。
    (output / "normal_batch.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fingerprint = sm3_hex(bytes.fromhex(public_key))

    print("设备：", device_id)
    print("批次：", batch["batch_id"])
    print("记录数量：", batch["count"])
    print("公钥 SM3 指纹：", fingerprint)
    print("标准批次样例检查通过")
    print("样例文件：", output / "normal_batch.json")
    print("公开登记表未改动：", output / "registry_public.json")


if __name__ == "__main__":
    main()
