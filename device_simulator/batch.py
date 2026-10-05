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


import json
import sqlite3
from datetime import timezone
from pathlib import Path

from common_crypto.hash_chain import ZERO_HASH
from .generator import make_water_records


def create_next_batch(
    registration,
    private_key,
    batch_size=10,
    db_path=None,
):
    """生成下一批，并在同一事务中保存批次和设备状态。"""
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size 必须是正整数")

    if db_path is None:
        db_path = (
            Path(__file__).resolve().parents[1] / "simulator.db"
        )

    device_id = registration["device_id"]
    conn = sqlite3.connect(str(db_path), timeout=30)

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS simulator_state (
                device_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS generated_batches (
                device_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (device_id, batch_id)
            )
        """)

        conn.commit()
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT state_json FROM simulator_state WHERE device_id = ?",
            (device_id,),
        ).fetchone()

        if row is None:
            state = {
                "last_sequence": 0,
                "last_hash": ZERO_HASH,
                "last_batch_number": 0,
                "last_timestamp": None,
                "public_key": registration["public_key"],
                "device_type": registration["device_type"],
            }
        else:
            state = json.loads(row[0])

        if (
            state["public_key"] != registration["public_key"]
            or state["device_type"] != registration["device_type"]
        ):
            raise ValueError("设备身份与保存状态不一致")

        batch_number = state["last_batch_number"] + 1
        batch_id = f"{device_id}-{batch_number:06d}"

        if state["last_timestamp"] is None:
            start_time = datetime.now(
                timezone(timedelta(hours=8))
            ).replace(microsecond=0)
        else:
            start_time = (
                datetime.fromisoformat(state["last_timestamp"])
                + timedelta(seconds=1)
            )

        records = make_water_records(
            registration=registration,
            batch_id=batch_id,
            start_sequence=state["last_sequence"] + 1,
            previous_hash=state["last_hash"],
            start_time=start_time,
            count=batch_size,
        )

        batch = build_batch(
            records,
            private_key,
            registration["public_key"],
        )

        # 检查签名与登记公钥是否配套
        from common_crypto.sm import verify_message

        if not verify_message(
            registration["public_key"],
            canonical_bytes(signed_header(batch)),
            batch["signature"],
        ):
            raise ValueError("批次签名与登记公钥不匹配")

        conn.execute(
            """
            INSERT INTO generated_batches
                (device_id, batch_id, raw_json)
            VALUES (?, ?, ?)
            """,
            (
                device_id,
                batch_id,
                json.dumps(batch, ensure_ascii=False, allow_nan=False),
            ),
        )

        state.update({
            "last_sequence": records[-1]["sequence"],
            "last_hash": records[-1]["record_hash"],
            "last_batch_number": batch_number,
            "last_timestamp": records[-1]["timestamp"],
        })

        conn.execute(
            """
            INSERT INTO simulator_state (device_id, state_json)
            VALUES (?, ?)
            ON CONFLICT(device_id)
            DO UPDATE SET state_json = excluded.state_json
            """,
            (
                device_id,
                json.dumps(state, ensure_ascii=False),
            ),
        )

        conn.commit()
        return batch

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    from .register import register_water_device

    registration, private_key = register_water_device()

    for _ in range(2):
        batch = create_next_batch(registration, private_key)

        print(
            f"批次：{batch['batch_id']}，"
            f"序号：{batch['start_sequence']}～"
            f"{batch['end_sequence']}，"
            f"数量：{batch['count']}"
        )

    print("批次与设备状态已保存")