import pytest

from common_crypto.canonical import (
    canonical_bytes,
    record_body,
    signed_header,
)


def test_canonical_bytes():
    first = {"b": "水位", "a": "3.25"}
    second = {"a": "3.25", "b": "水位"}

    expected = '{"a":"3.25","b":"水位"}'.encode("utf-8")

    assert canonical_bytes(first) == expected
    assert canonical_bytes(second) == expected


def test_record_body():
    record = {
        "sequence": 1,
        "previous_hash": "0" * 64,
        "record_hash": "a" * 64,
        "payload": {"water_level_m": "3.25"},
    }

    body = record_body(record)

    assert "record_hash" not in body
    assert body["previous_hash"] == "0" * 64
    assert body["payload"] == {"water_level_m": "3.25"}

    # 提取摘要内容不应修改原记录
    assert record["record_hash"] == "a" * 64


def test_signed_header():
    batch = {
        "version": 1,
        "device_id": "WL-001",
        "batch_id": "WL-001-000001",
        "start_sequence": 1,
        "end_sequence": 10,
        "count": 10,
        "start_time": "2026-10-05T12:00:00+08:00",
        "end_time": "2026-10-05T12:00:09+08:00",
        "merkle_root": "a" * 64,
        "records": [],
        "signature": "test_signature",
    }

    header = signed_header(batch)
    original_bytes = canonical_bytes(header)

    assert len(header) == 9
    assert "records" not in header
    assert "signature" not in header

    # 签名字段本身不参与签名
    batch["signature"] = "another_signature"
    assert canonical_bytes(signed_header(batch)) == original_bytes

    # 修改记录数量，待签名内容必须改变
    batch["count"] = 9
    assert canonical_bytes(signed_header(batch)) != original_bytes


def test_reject_nan():
    with pytest.raises(ValueError):
        canonical_bytes({"value": float("nan")})