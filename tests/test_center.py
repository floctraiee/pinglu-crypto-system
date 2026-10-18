"""中心平台的端到端测试：合法入库、未知设备、改值、删记录、伪造签名、重复提交、证明路径。

在仓库根目录执行：
    python -m pytest tests/test_center.py -v

测试使用临时 SQLite，不会改动 center_platform/data/center.db。
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from center_platform import storage
from center_platform.main import app
from common_crypto.batch import PAYLOAD_FIELDS
from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.hash_chain import ZERO_HASH, record_hash
from common_crypto.merkle import merkle_root
from common_crypto.sm import generate_keypair, sign_message


DEVICE_TYPES = {
    "water_level": "WL",
    "weather": "WE",
    "navigation_mark": "NM",
    "slope": "SL",
    "lock": "LK",
    "drone": "DR",
    "survey_boat": "SB",
}


def payload_for(device_type, index):
    """按设备类型造一条合法的字符串 payload。"""
    return {
        field: f"{10 + index}.{index:02d}"
        for field in PAYLOAD_FIELDS[device_type]
    }


def make_records(device_id, device_type, start=1, count=3, previous=ZERO_HASH,
                 batch_id=None, start_time="2026-10-09T10:00:00+08:00"):
    """按任务书字段造一段连续记录，返回（记录列表, 末条摘要）。"""
    from datetime import datetime, timedelta

    if batch_id is None:
        batch_id = f"{device_id}-000001"

    base = datetime.fromisoformat(start_time)
    records = []
    previous_hash = previous

    for offset in range(count):
        sequence = start + offset
        record = {
            "version": 1,
            "device_id": device_id,
            "device_type": device_type,
            "timestamp": (base + timedelta(seconds=offset)).isoformat(),
            "longitude": "108.500000",
            "latitude": "22.000000",
            "sequence": sequence,
            "batch_id": batch_id,
            "payload": payload_for(device_type, sequence),
            "status": "normal",
            "previous_hash": previous_hash,
        }
        record["record_hash"] = record_hash(record)
        previous_hash = record["record_hash"]
        records.append(record)

    return records, previous_hash


def make_batch(device_id, device_type, private_key, public_key, records=None,
               batch_id="WL-001-000001", **kwargs):
    """把记录组装成带 SM2 签名的批次，签名对象只含约定的头部字段。"""
    if records is None:
        records, _ = make_records(device_id, device_type, batch_id=batch_id, **kwargs)

    batch = {
        "version": 1,
        "device_id": device_id,
        "batch_id": records[0]["batch_id"],
        "start_sequence": records[0]["sequence"],
        "end_sequence": records[-1]["sequence"],
        "count": len(records),
        "start_time": records[0]["timestamp"],
        "end_time": records[-1]["timestamp"],
        "merkle_root": merkle_root([r["record_hash"] for r in records]),
        "records": records,
    }
    batch["signature"] = sign_message(
        private_key, public_key, canonical_bytes(signed_header(batch))
    )
    return batch


def resign(batch, private_key, public_key):
    """内容改动后重算签名，用于伪造“签名有效但内容非法”的批次。"""
    batch["signature"] = sign_message(
        private_key, public_key, canonical_bytes(signed_header(batch))
    )
    return batch


def post(client, batch):
    response = client.post("/batches", json={"batch": batch})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def center(tmp_path, monkeypatch):
    """每个用例一个临时数据库和一个已登记 WL-001 的中心。"""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "center.db")
    storage.init_db()

    private_key, public_key = generate_keypair()
    storage.register_device("WL-001", "water_level", public_key, longitude="108.500000", latitude="22.000000")

    with TestClient(app) as client:
        yield {
            "client": client,
            "private_key": private_key,
            "public_key": public_key,
            "db_path": storage.DB_PATH,
        }


def test_valid_batch_is_stored(center):
    """合法批次：入库、返回 accepted、统计与证明路径都对得上。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])

    result = post(client, batch)

    assert result["status"] == "accepted"
    assert result["reason_code"] == "OK"
    assert result["device_id"] == "WL-001"
    assert result["batch_id"] == "WL-001-000001"

    stats = client.get("/stats").json()
    assert stats["devices"] == 1
    assert stats["batches"] == 1
    assert stats["accepted_batches"] == 1
    assert stats["records"] == 3

    records = client.get("/records", params={"device_id": "WL-001"}).json()
    assert [item["sequence"] for item in records] == [1, 2, 3]
    assert records[0]["record"]["device_id"] == "WL-001"

    batches = client.get("/batches").json()
    assert batches[0]["batch_id"] == "WL-001-000001"
    assert batches[0]["status"] == "accepted"


def test_unknown_device_is_rejected_and_audited(center):
    """未知设备：拒绝、写审计、不入库。"""
    client = center["client"]
    batch = make_batch("XX-999", "water_level", center["private_key"], center["public_key"])

    result = post(client, batch)

    assert result["status"] == "rejected"
    assert result["reason_code"] == "UNKNOWN_DEVICE"
    assert client.get("/stats").json()["records"] == 0

    audits = client.get("/audits").json()
    assert len(audits) == 1
    assert audits[0]["status"] == "rejected"
    assert audits[0]["reason_code"] == "UNKNOWN_DEVICE"
    assert audits[0]["raw"]["batch_id"] == batch["batch_id"]


def test_tampered_value_is_located_by_sequence(center):
    """改测量值：定位到具体序号，保留原始批次作为证据。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    batch["records"][1]["payload"]["water_level_m"] = "999.00"

    result = post(client, batch)

    assert result["status"] == "rejected"
    assert result["reason_code"] == "INVALID_RECORD_HASH"
    assert result["affected_sequences"] == [2]
    assert client.get("/stats").json()["records"] == 0
    assert client.get("/audits").json()[0]["raw"]["records"][1]["payload"]["water_level_m"] == "999.00"


def test_missing_record_breaks_count_and_chain(center):
    """删掉中间一条记录：数量与链都对不上，被拒绝且不入库。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    del batch["records"][1]

    result = post(client, batch)

    assert result["status"] == "rejected"
    assert result["reason_code"] in {"INVALID_BATCH_RANGE", "INVALID_SEQUENCE", "INVALID_HASH_CHAIN"}
    assert client.get("/stats").json()["records"] == 0

    # 删掉末条记录时数量仍然对不上
    client2 = center["client"]
    second = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    del second["records"][-1]
    assert post(client2, second)["status"] == "rejected"


def test_forged_signature_is_rejected(center):
    """伪造签名：拒绝，理由是签名不通过。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    batch["signature"] = "0" * 128

    result = post(client, batch)

    assert result["status"] == "rejected"
    assert result["reason_code"] == "INVALID_SIGNATURE"
    assert client.get("/stats").json()["batches"] == 0


def test_forged_signature_with_recomputed_hash_still_fails_root(center):
    """改值后连摘要一起重算、再重签：签名能过，但和签名的 Merkle 根不一致。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    batch["records"][2]["payload"]["water_level_m"] = "999.00"
    batch["records"][2]["record_hash"] = record_hash(batch["records"][2])

    result = post(client, batch)

    assert result["status"] == "rejected"
    assert result["reason_code"] == "INVALID_MERKLE_ROOT"


def test_duplicate_batch_is_not_counted_twice(center):
    """重复提交：状态 duplicate、记录数不变、重复事件留审计。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])

    assert post(client, batch)["status"] == "accepted"
    again = post(client, batch)

    assert again["status"] == "duplicate"
    assert again["reason_code"] == "DUPLICATE_BATCH"
    assert client.get("/stats").json()["records"] == 3

    audits = client.get("/audits").json()
    assert audits[0]["reason_code"] == "DUPLICATE_BATCH"

    # 同一 batch_id 换了内容属于冲突，不能覆盖已入库的数据
    changed = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    changed["records"][0]["payload"]["water_level_m"] = "0.01"
    conflict = post(client, changed)
    assert conflict["reason_code"] == "BATCH_CONTENT_CONFLICT"
    assert client.get("/stats").json()["records"] == 3
    assert client.get("/records").json()[0]["record"]["payload"]["water_level_m"] != "0.01"


def test_merkle_proof_path_for_every_sequence(center):
    """证明路径：逐条返回叶摘要、兄弟方向、签名根，并且能重新验证通过。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    assert post(client, batch)["status"] == "accepted"

    for sequence in (1, 2, 3):
        proof = client.get(f"/batches/{batch['batch_id']}/proof/{sequence}").json()
        assert proof["verified"] is True
        assert proof["root"] == batch["merkle_root"]
        assert proof["sequence"] == sequence
        assert proof["record_hash"] == batch["records"][sequence - 1]["record_hash"]
        assert proof["proof"], "证明路径不能为空"

        for step in proof["proof"]:
            assert set(step) >= {"side", "hash"}
            assert step["side"] in {"left", "right"}
            assert len(step["hash"]) == 64

    missing = client.get(f"/batches/{batch['batch_id']}/proof/9")
    assert missing.status_code == 400


def test_second_batch_continues_the_chain(center):
    """第二批：从上一批末摘要续链，序号与批次号递增才被接受。"""
    client = center["client"]
    first = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    assert post(client, first)["status"] == "accepted"

    records, _ = make_records(
        "WL-001", "water_level", start=4, count=2,
        previous=first["records"][-1]["record_hash"],
        batch_id="WL-001-000002",
        start_time="2026-10-09T10:00:10+08:00",
    )
    second = make_batch(
        "WL-001", "water_level", center["private_key"], center["public_key"],
        records=records,
    )

    assert post(client, second)["status"] == "accepted"
    assert client.get("/stats").json()["records"] == 5

    # 批次号回退的批次要被拒
    stale_records, _ = make_records(
        "WL-001", "water_level", start=6, count=1,
        previous=second["records"][-1]["record_hash"],
        batch_id="WL-001-000001",
        start_time="2026-10-09T10:00:20+08:00",
    )
    stale = make_batch(
        "WL-001", "water_level", center["private_key"], center["public_key"],
        records=stale_records,
    )
    assert post(client, stale)["reason_code"] in {"DUPLICATE_BATCH", "BATCH_CONTENT_CONFLICT", "INVALID_BATCH_SEQUENCE"}


def test_seven_device_types_can_all_register_and_submit(center):
    """七类设备都能登记并提交各自的合法批次。"""
    client = center["client"]

    for device_type, prefix in DEVICE_TYPES.items():
        device_id = f"{prefix}-001"
        private_key, public_key = generate_keypair()
        storage.register_device(
            device_id, device_type, public_key,
            longitude="108.500000", latitude="22.000000",
        )

        records, _ = make_records(
            device_id, device_type, start=1, count=2,
            batch_id=f"{device_id}-000001",
            start_time="2026-10-09T11:00:00+08:00",
        )
        batch = make_batch(
            device_id, device_type, private_key, public_key, records=records,
        )

        result = post(client, batch)
        assert result["status"] == "accepted", f"{device_id} 未通过：{result}"

    # 七类设备各一台（WL-001 与夹具登记的是同一台，就地更新）
    assert client.get("/stats").json()["devices"] == 7
    assert client.get("/stats").json()["records"] == 14


def test_devices_api_exposes_registration_table(center):
    """登记接口按公开登记表原样返回，便于队长核对指纹。"""
    client = center["client"]
    devices = client.get("/devices").json()

    assert len(devices) == 1
    entry = devices[0]
    assert entry["device_id"] == "WL-001"
    assert entry["device_type"] == "water_level"
    assert entry["public_key"] == center["public_key"]
    assert entry["longitude"] == "108.500000"
    assert entry["latitude"] == "22.000000"

    from common_crypto.sm import sm3_hex
    assert entry["fingerprint"] == sm3_hex(bytes.fromhex(center["public_key"]))


def test_health_and_docs_are_available(center):
    """健康检查与接口文档可用。"""
    client = center["client"]
    assert client.get("/health").json() == {"status": "ok", "service": "center_platform"}
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_database_keeps_raw_batch_for_replay(center):
    """原始批次留在库里，可追溯重放。"""
    client = center["client"]
    batch = make_batch("WL-001", "water_level", center["private_key"], center["public_key"])
    assert post(client, batch)["status"] == "accepted"

    connection = sqlite3.connect(center["db_path"])
    try:
        row = connection.execute(
            "SELECT raw_json, status FROM batches WHERE device_id = ? AND batch_id = ?",
            ("WL-001", batch["batch_id"]),
        ).fetchone()
        assert row is not None
        assert row[1] == "accepted"
        assert json.loads(row[0]) == batch
    finally:
        connection.close()
