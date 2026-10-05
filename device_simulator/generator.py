from datetime import timedelta

from common_crypto.hash_chain import record_hash


def make_water_records(
    registration,
    batch_id,
    start_sequence,
    previous_hash,
    start_time,
    count=10,
):
    """生成连续的水位记录；序号和起始摘要由状态管理提供。"""
    if registration["device_type"] != "water_level":
        raise ValueError("当前生成器只支持水位设备")

    records = []

    for offset in range(count):
        sequence = start_sequence + offset

        record = {
            "version": 1,
            "device_id": registration["device_id"],
            "device_type": registration["device_type"],
            "timestamp": (
                start_time + timedelta(seconds=offset)
            ).isoformat(),
            "longitude": registration["longitude"],
            "latitude": registration["latitude"],
            "sequence": sequence,
            "batch_id": batch_id,
            "payload": {
                "water_level_m": f"{3.20 + (sequence % 20) * 0.01:.2f}",
            },
            "status": "normal",
            "previous_hash": previous_hash,
        }

        record["record_hash"] = record_hash(record)
        records.append(record)
        previous_hash = record["record_hash"]

    return records