import random
from datetime import timedelta

from common_crypto.hash_chain import record_hash


SUPPORTED_TYPES = {
    "water_level",
    "weather",
    "navigation_mark",
    "slope",
    "lock",
    "drone",
    "survey_boat",
}


def make_payload(device_type, rng):
    """按任务书字段生成模拟值，所有值使用字符串。"""
    if device_type == "water_level":
        return {
            "water_level_m": f"{rng.uniform(3.10, 3.50):.2f}",
        }

    if device_type == "weather":
        return {
            "wind_speed_m_s": f"{rng.uniform(0.0, 8.0):.1f}",
            "rainfall_mm": f"{rng.uniform(0.0, 3.0):.1f}",
        }

    if device_type == "navigation_mark":
        return {
            "battery_v": f"{rng.uniform(12.0, 12.8):.1f}",
            "light_state": rng.choice(["on", "off"]),
        }

    if device_type == "slope":
        return {
            "displacement_mm": f"{rng.uniform(0.5, 2.5):.1f}",
        }

    if device_type == "lock":
        return {
            "gate_open_pct": f"{rng.uniform(0.0, 100.0):.1f}",
            "water_level_m": f"{rng.uniform(3.10, 3.50):.2f}",
        }

    if device_type == "drone":
        return {
            "altitude_m": f"{rng.uniform(25.0, 40.0):.1f}",
            "image_event": rng.choice(["clear", "blur"]),
        }

    if device_type == "survey_boat":
        return {
            "water_depth_m": f"{rng.uniform(7.0, 10.0):.1f}",
            "speed_kn": f"{rng.uniform(1.0, 3.0):.1f}",
        }

    raise ValueError(f"不支持的设备类型：{device_type}")


def make_records(
    registration,
    batch_id,
    start_sequence,
    previous_hash,
    start_time,
    count=10,
    seed=2026,
):
    """生成指定设备的连续记录，随机值可通过 seed 复现。"""
    device_type = registration["device_type"]

    if device_type not in SUPPORTED_TYPES:
        raise ValueError(f"不支持的设备类型：{device_type}")

    if type(count) is not int or count < 1:
        raise ValueError("count 必须是正整数")

    records = []

    for offset in range(count):
        sequence = start_sequence + offset

        # 按设备和序号确定随机值，不依赖进程内随机状态
        rng = random.Random(
            f"{seed}:{registration['device_id']}:{sequence}"
        )

        longitude = registration["longitude"]
        latitude = registration["latitude"]

        # 无人机和测量船使用简单模拟航线
        if device_type in {"drone", "survey_boat"}:
            longitude = (
                f"{float(longitude) + sequence * 0.000001:.6f}"
            )
            latitude = (
                f"{float(latitude) + sequence * 0.000001:.6f}"
            )

        record = {
            "version": 1,
            "device_id": registration["device_id"],
            "device_type": device_type,
            "timestamp": (
                start_time + timedelta(seconds=offset)
            ).isoformat(),
            "longitude": longitude,
            "latitude": latitude,
            "sequence": sequence,
            "batch_id": batch_id,
            "payload": make_payload(device_type, rng),
            "status": "normal",
            "previous_hash": previous_hash,
        }

        record["record_hash"] = record_hash(record)
        records.append(record)
        previous_hash = record["record_hash"]

    return records