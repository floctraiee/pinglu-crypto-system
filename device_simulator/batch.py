from copy import deepcopy
from datetime import datetime, timedelta

from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.hash_chain import verify_chain
from common_crypto.merkle import merkle_root
from common_crypto.sm import sign_message


RECORD_FIELDS = {
    "version",
    "device_id",
    "device_type",
    "timestamp",
    "longitude",
    "latitude",
    "sequence",
    "batch_id",
    "payload",
    "status",
    "previous_hash",
    "record_hash",
}


def build_batch(records, private_key, public_key):
    """检查记录，构建任务书规定的批次，并签名。"""
    if not isinstance(records, list) or not records:
        raise ValueError("批次必须是非空记录列表")

    records = deepcopy(records)
    first = records[0]
    previous_time = None

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("每条记录必须是对象")

        if set(record) != RECORD_FIELDS:
            raise ValueError("记录字段不符合任务书")

        if type(record["version"]) is not int or record["version"] != 1:
            raise ValueError("记录 version 必须是整数 1")

        if (
            type(record["sequence"]) is not int
            or record["sequence"] < 1
        ):
            raise ValueError("记录序号必须是正整数")

        for field in ("device_id", "device_type", "batch_id"):
            if not isinstance(record[field], str) or not record[field]:
                raise ValueError(f"{field} 必须是非空字符串")

            if record[field] != first[field]:
                raise ValueError("同一批次的设备和批次信息必须一致")

        for field in (
            "timestamp", "longitude", "latitude",
            "status", "previous_hash", "record_hash",
        ):
            if not isinstance(record[field], str):
                raise ValueError(f"{field} 必须是字符串")

        payload = record["payload"]
        if not isinstance(payload, dict) or not payload:
            raise ValueError("payload 必须是非空对象")

        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise ValueError("payload 的键和值必须是字符串")

        if not record["timestamp"].endswith("+08:00"):
            raise ValueError("时间必须带 +08:00 时区")

        current_time = datetime.fromisoformat(record["timestamp"])

        if current_time.utcoffset() != timedelta(hours=8):
            raise ValueError("时间时区不正确")

        if previous_time is not None and current_time <= previous_time:
            raise ValueError("记录时间必须递增")

        previous_time = current_time

    # batch_id 采用：设备ID-六位批次号
    prefix = first["device_id"] + "-"
    suffix = first["batch_id"][len(prefix):]

    if (
        not first["batch_id"].startswith(prefix)
        or len(suffix) != 6
        or any(char not in "0123456789" for char in suffix)
        or int(suffix) < 1
    ):
        raise ValueError("批次号应为设备ID-六位正整数")

    errors = verify_chain(
        records,
        previous_hash=first["previous_hash"],
        start_sequence=first["sequence"],
    )
    if errors:
        raise ValueError(f"记录摘要链检查失败：{errors}")

    last = records[-1]

    batch = {
        "version": 1,
        "device_id": first["device_id"],
        "batch_id": first["batch_id"],
        "start_sequence": first["sequence"],
        "end_sequence": last["sequence"],
        "count": len(records),
        "start_time": first["timestamp"],
        "end_time": last["timestamp"],
        "merkle_root": merkle_root([
            record["record_hash"] for record in records
        ]),
        "records": records,
    }

    batch["signature"] = sign_message(
        private_key,
        public_key,
        canonical_bytes(signed_header(batch)),
    )

    return batch

