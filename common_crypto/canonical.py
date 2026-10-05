import json


def canonical_bytes(obj):
    """将对象按统一 JSON 规则转成 UTF-8 字节。"""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def record_body(record):
    """记录摘要包含所有记录字段，但排除 record_hash 本身。"""
    return {
        key: value
        for key, value in record.items()
        if key != "record_hash"
    }


def signed_header(batch):
    """只提取任务书规定的九个批次签名字段。"""
    fields = (
        "version",
        "device_id",
        "batch_id",
        "start_sequence",
        "end_sequence",
        "count",
        "start_time",
        "end_time",
        "merkle_root",
    )

    return {field: batch[field] for field in fields}