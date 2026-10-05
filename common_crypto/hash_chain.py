import json
from .sm import sm3_hex

# 每台设备的第一条记录使用这个起始摘要
ZERO_HASH = "0" * 64


def record_hash(record):
    """统一编码后计算摘要，计算时排除摘要字段本身。"""
    body = {
        key: value
        for key, value in record.items()
        if key != "record_hash"
    }

    data = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return sm3_hex(data)


def verify_chain(records, previous_hash=ZERO_HASH, start_sequence=1):
    """检查同一台设备的连续记录，返回异常列表。"""
    errors = []
    device_id = records[0]["device_id"] if records else None

    for index, record in enumerate(records):
        reasons = []

        if record["device_id"] != device_id:
            reasons.append("设备ID不一致")

        if record["sequence"] != start_sequence + index:
            reasons.append("序号不连续")

        if record["previous_hash"] != previous_hash:
            reasons.append("摘要链断开")

        if record["record_hash"] != record_hash(record):
            reasons.append("记录摘要不匹配")

        if reasons:
            errors.append({
                "index": index,
                "sequence": record["sequence"],
                "reasons": reasons,
            })

        previous_hash = record["record_hash"]

    return errors
