"""公共接收端验证：只接受最终任务书规定的扁平批次。"""
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from .canonical import canonical_bytes, signed_header
from .hash_chain import ZERO_HASH, record_hash
from .merkle import merkle_root
from .sm import verify_message

BATCH_FIELDS = {
    "version", "device_id", "batch_id", "start_sequence", "end_sequence",
    "count", "start_time", "end_time", "merkle_root", "records", "signature",
}
RECORD_FIELDS = {
    "version", "device_id", "device_type", "timestamp", "longitude", "latitude",
    "sequence", "batch_id", "payload", "status", "previous_hash", "record_hash",
}
PAYLOAD_FIELDS = {
    "water_level": {"water_level_m"},
    "weather": {"wind_speed_m_s", "rainfall_mm"},
    "navigation_mark": {"battery_v", "light_state"},
    "slope": {"displacement_mm"},
    "lock": {"gate_open_pct", "water_level_m"},
    "drone": {"altitude_m", "image_event"},
    "survey_boat": {"water_depth_m", "speed_kn"},
}


def _hex(value, size=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{%d}" % size, value) is not None


def _time(value):
    if not isinstance(value, str) or not value.endswith("+08:00"):
        raise ValueError("timestamp 必须显式使用 +08:00")
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(hours=8):
        raise ValueError("timestamp 时区错误")
    return result


def _coordinate(value, limit):
    if not isinstance(value, str):
        return False
    try:
        number = Decimal(value)
        return number.is_finite() and -limit <= number <= limit
    except InvalidOperation:
        return False


def verify_batch(batch, registration, previous_hash=ZERO_HASH, start_sequence=1):
    """公钥及链尾必须来自接收端可信状态；此函数不更新状态或判定重复。"""
    errors, record_errors, codes = [], [], []

    def fail(code, message):
        codes.append(code)
        errors.append(message)

    def result():
        return {
            "valid": not errors,
            "reason_code": codes[0] if codes else "OK",
            "errors": errors,
            "record_errors": record_errors,
            "affected_sequences": sorted({e["sequence"] for e in record_errors}),
        }

    if not registration:
        fail("UNKNOWN_DEVICE", "设备未登记")
        return result()
    try:
        if not isinstance(batch, dict) or set(batch) != BATCH_FIELDS:
            raise ValueError("批次必须是合同规定的扁平结构，字段不得缺失或增加")
        if type(batch["version"]) is not int or batch["version"] != 1:
            raise ValueError("批次 version 必须为整数 1")
        for field in ("start_sequence", "end_sequence", "count"):
            if type(batch[field]) is not int or batch[field] <= 0:
                raise ValueError(field + " 必须是正整数")
        if not isinstance(batch["device_id"], str) or not batch["device_id"]:
            raise ValueError("device_id 格式错误")
        if not isinstance(batch["batch_id"], str) or not re.fullmatch(
            re.escape(batch["device_id"]) + r"-[0-9]{6}", batch["batch_id"]
        ) or int(batch["batch_id"][-6:]) < 1:
            raise ValueError("batch_id 必须是设备ID加六位正批次号")
        if not _hex(batch["merkle_root"]) or not _hex(batch["signature"], 128):
            raise ValueError("Merkle 根或 SM2 签名格式错误")
        start_time, end_time = _time(batch["start_time"]), _time(batch["end_time"])
        records = batch["records"]
        if not isinstance(records, list) or not records:
            raise ValueError("records 必须是非空列表")
        for record in records:
            if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
                raise ValueError("记录字段与合同不一致")
            if type(record["version"]) is not int or record["version"] != 1:
                raise ValueError("记录 version 必须为整数 1")
            if type(record["sequence"]) is not int or record["sequence"] <= 0:
                raise ValueError("记录 sequence 必须是正整数")
            if any(not isinstance(record[f], str) or not record[f] for f in (
                "device_id", "device_type", "batch_id", "status"
            )):
                raise ValueError("记录标识、类型或状态格式错误")
            payload = record["payload"]
            if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS.get(record["device_type"]):
                raise ValueError("payload 字段与设备类型不一致")
            if any(not isinstance(k, str) or not isinstance(v, str) for k, v in payload.items()):
                raise ValueError("payload 的键和值必须为字符串")
            if not _coordinate(record["longitude"], 180) or not _coordinate(record["latitude"], 90):
                raise ValueError("经纬度必须是有效范围内的十进制字符串")
            _time(record["timestamp"])
            if not _hex(record["previous_hash"]) or not _hex(record["record_hash"]):
                raise ValueError("记录摘要必须为 64 位十六进制字符串")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        fail("INVALID_BATCH_STRUCTURE", str(exc))
        return result()

    if batch["device_id"] != registration.get("device_id"):
        fail("UNKNOWN_DEVICE", "设备登记信息不匹配")
        return result()
    try:
        signature_ok = verify_message(
            registration["public_key"], canonical_bytes(signed_header(batch)), batch["signature"]
        )
    except (KeyError, TypeError, ValueError, IndexError):
        signature_ok = False
    if not signature_ok:
        fail("INVALID_SIGNATURE", "SM2 验签失败")

    if batch["count"] != len(records) or batch["end_sequence"] - batch["start_sequence"] + 1 != len(records):
        fail("INVALID_BATCH_RANGE", "批次数量或序号范围与记录不一致")
    if batch["start_sequence"] != records[0]["sequence"] or batch["end_sequence"] != records[-1]["sequence"]:
        fail("INVALID_BATCH_RANGE", "批次起止序号与首尾记录不一致")
    if batch["start_time"] != records[0]["timestamp"] or batch["end_time"] != records[-1]["timestamp"] or start_time > end_time:
        fail("INVALID_TIMESTAMP", "批次起止时间与首尾记录不一致")

    last_time = None
    for index, record in enumerate(records):
        reasons, record_codes = [], []
        def record_fail(code, message):
            reasons.append(message)
            record_codes.append(code)
        if (record["device_id"] != batch["device_id"] or record["batch_id"] != batch["batch_id"] or
                record["device_type"] != registration.get("device_type")):
            record_fail("INVALID_DEVICE_IDENTITY", "记录的设备、类型或批次不一致")
        if record["sequence"] != start_sequence + index:
            record_fail("INVALID_SEQUENCE", "序号与接收端可信下一序号不一致")
        if record["record_hash"] != record_hash(record):
            record_fail("INVALID_RECORD_HASH", "记录摘要不匹配")
        if record["previous_hash"] != previous_hash:
            record_fail("INVALID_HASH_CHAIN", "摘要链断开")
        stamp = _time(record["timestamp"])
        if last_time is not None and stamp <= last_time:
            record_fail("INVALID_TIMESTAMP", "记录时间没有递增")
        last_time = stamp
        previous_hash = record["record_hash"]
        if reasons:
            record_errors.append({"index": index, "sequence": record["sequence"], "reasons": reasons})
            for code, message in zip(record_codes, reasons):
                fail(code, message)
    if merkle_root([r["record_hash"] for r in records]) != batch["merkle_root"]:
        fail("INVALID_MERKLE_ROOT", "Merkle 根与记录摘要不一致")
    return result()
