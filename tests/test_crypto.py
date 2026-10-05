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



from datetime import datetime, timedelta, timezone

from common_crypto.hash_chain import ZERO_HASH, record_hash, verify_chain 
from common_crypto.sm import generate_keypair, verify_message
from device_simulator.batch import build_batch


def test_build_batch():
    private_key, public_key = generate_keypair()

    records = []
    previous = ZERO_HASH
    start = datetime(
        2026, 10, 5, 12, 0, 0,
        tzinfo=timezone(timedelta(hours=8)),
    )

    for sequence in range(1, 11):
        record = {
            "version": 1,
            "device_id": "WL-001",
            "device_type": "water_level",
            "timestamp": (
                start + timedelta(seconds=sequence - 1)
            ).isoformat(),
            "longitude": "108.500000",
            "latitude": "22.000000",
            "sequence": sequence,
            "batch_id": "WL-001-000001",
            "payload": {
                "water_level_m": "3.25",
            },
            "status": "normal",
            "previous_hash": previous,
        }

        record["record_hash"] = record_hash(record)
        records.append(record)
        previous = record["record_hash"]

    batch = build_batch(records, private_key, public_key)

    assert set(batch) == {
        "version", "device_id", "batch_id",
        "start_sequence", "end_sequence", "count",
        "start_time", "end_time", "merkle_root",
        "records", "signature",
    }
    assert batch["count"] == 10
    assert batch["start_sequence"] == 1
    assert batch["end_sequence"] == 10

    # 正常批次头的签名应通过
    assert verify_message(
        public_key,
        canonical_bytes(signed_header(batch)),
        batch["signature"],
    )

    # 修改签名覆盖的数量字段，原签名应失败
    batch["count"] = 9
    assert not verify_message(
        public_key,
        canonical_bytes(signed_header(batch)),
        batch["signature"],
    )

    # 设备端发现记录被修改，应拒绝组批
    records[3]["payload"]["water_level_m"] = "9.99"

    with pytest.raises(ValueError, match="摘要链检查失败"):
        build_batch(records, private_key, public_key)

import json
import sqlite3

from device_simulator.batch import create_next_batch


def test_continuous_batches_and_saved_state(tmp_path):
    private_key, public_key = generate_keypair()

    registration = {
        "device_id": "WL-001",
        "device_type": "water_level",
        "longitude": "108.500000",
        "latitude": "22.000000",
        "public_key": public_key,
    }

    db_path = tmp_path / "simulator.db"

    first = create_next_batch(
        registration, private_key, db_path=db_path
    )
    second = create_next_batch(
        registration, private_key, db_path=db_path
    )

    assert first["start_sequence"] == 1
    assert first["end_sequence"] == 10
    assert second["start_sequence"] == 11
    assert second["end_sequence"] == 20

    assert (
        second["records"][0]["previous_hash"]
        == first["records"][-1]["record_hash"]
    )

    assert verify_chain(
        first["records"] + second["records"]
    ) == []

    # 每次调用都会关闭数据库；再次调用必须从磁盘恢复
    third = create_next_batch(
        registration, private_key, db_path=db_path
    )

    assert third["batch_id"] == "WL-001-000003"
    assert third["start_sequence"] == 21
    assert third["end_sequence"] == 30
    assert (
        third["records"][0]["previous_hash"]
        == second["records"][-1]["record_hash"]
    )

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM generated_batches"
        ).fetchone()[0]

        saved = conn.execute(
            "SELECT state_json FROM simulator_state WHERE device_id = ?",
            ("WL-001",),
        ).fetchone()[0]

        assert count == 3
        assert json.loads(saved)["last_sequence"] == 30
    finally:
        conn.close()