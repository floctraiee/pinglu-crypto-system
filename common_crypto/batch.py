import json
from copy import deepcopy

from .sm import sign_message, verify_message
from .hash_chain import ZERO_HASH, record_hash, verify_chain
from .merkle import merkle_root


def encoded(obj):
    """统一使用排序后的 JSON 和 UTF-8 编码。"""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def make_header(records):
    first = records[0]
    last = records[-1]

    return {
        "schema_version": 1,
        "device_id": first["device_id"],
        "device_type": first["device_type"],
        "batch_id": first["batch_id"],
        "sequence_start": first["sequence"],
        "sequence_end": last["sequence"],
        "record_count": len(records),
        "timestamp_start": first["timestamp"],
        "timestamp_end": last["timestamp"],
        "previous_hash": first["previous_hash"],
        "merkle_root": merkle_root([
            record_hash(record) for record in records
        ]),
    }


def same_identity(records):
    """一个批次内只能有同一设备、同一批次的记录。"""
    first = records[0]
    fields = ("device_id", "device_type", "batch_id")

    return all(
        all(record[field] == first[field] for field in fields)
        for record in records
    )


def build_batch(records, private_key, public_key):
    if not records:
        raise ValueError("批次不能为空")

    if not same_identity(records):
        raise ValueError("设备或批次信息不一致")

    errors = verify_chain(
        records,
        records[0]["previous_hash"],
        records[0]["sequence"],
    )
    if errors:
        raise ValueError("摘要链检查失败")

    header = make_header(records)

    return {
        "header": header,
        "records": deepcopy(records),
       "signature": sign_message(
           private_key, public_key, encoded(header)
           ),
    }


def verify_batch(
    batch,
    registration,
    previous_hash=ZERO_HASH,
    start_sequence=1,
):
    """registration 必须来自网关或中心已登记的设备信息。"""
    errors = []
    record_errors = []

    try:
        header = batch["header"]
        records = batch["records"]

        if not records:
            raise ValueError("批次没有记录")

        if (
            header["device_id"] != registration["device_id"]
            or header["device_type"] != registration["device_type"]
        ):
            errors.append("设备注册信息不匹配")

        signature_ok = verify_message(
               registration["public_key"],
               encoded(header),
                batch["signature"],
                )
        if not signature_ok:
            errors.append("SM2验签失败")

        if not same_identity(records):
            errors.append("记录的设备或批次信息不一致")

        # 重算记录数量、起止信息和 Merkle 根
        if header != make_header(records):
            errors.append("批次头与实际记录不一致")

        record_errors = verify_chain(
            records, previous_hash, start_sequence
        )
        if record_errors:
            errors.append("记录摘要链检查失败")

    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        errors.append("批次格式错误")

    return {
        "valid": not errors,
        "errors": errors,
        "record_errors": record_errors,
    }