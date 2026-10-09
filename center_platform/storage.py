import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

from common_crypto.canonical import canonical_bytes, signed_header


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "center.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            device_type TEXT NOT NULL,
            public_key TEXT NOT NULL,
            fingerprint TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            header_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            signature TEXT NOT NULL,
            status TEXT NOT NULL,
            received_at TEXT NOT NULL,
            UNIQUE(device_id, batch_id),
            FOREIGN KEY(device_id) REFERENCES devices(device_id)
        );

        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            record_hash TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            record_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(device_id, batch_id, sequence),
            FOREIGN KEY(device_id) REFERENCES devices(device_id)
        );

        CREATE TABLE IF NOT EXISTS audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            batch_id TEXT,
            status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            affected_sequences TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_records_device_sequence
        ON records(device_id, sequence);

        CREATE INDEX IF NOT EXISTS idx_audits_batch
        ON audits(device_id, batch_id);
        """
    )

    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def register_device(device_id, device_type, public_key, fingerprint=None):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO devices
        (device_id, device_type, public_key, fingerprint, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            device_type = excluded.device_type,
            public_key = excluded.public_key,
            fingerprint = excluded.fingerprint
        """,
        (
            device_id,
            device_type,
            public_key,
            fingerprint,
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()


def get_device(device_id, connection=None):
    conn = connection if connection is not None else get_connection()

    row = conn.execute(
        """
        SELECT device_id, device_type, public_key, fingerprint, created_at
        FROM devices
        WHERE device_id = ?
        """,
        (device_id,),
    ).fetchone()

    if connection is None:
        conn.close()

    return dict(row) if row else None


def list_devices():
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT device_id, device_type, public_key, fingerprint, created_at
        FROM devices
        ORDER BY device_id
        """
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def get_batch(device_id, batch_id, connection=None):
    conn = connection if connection is not None else get_connection()

    row = conn.execute(
        """
        SELECT *
        FROM batches
        WHERE device_id = ? AND batch_id = ?
        """,
        (device_id, batch_id),
    ).fetchone()

    if connection is None:
        conn.close()

    if not row:
        return None

    result = dict(row)
    result["header"] = json.loads(result.pop("header_json"))
    result["raw"] = json.loads(result.pop("raw_json"))

    return result


def batch_exists(device_id, batch_id):
    conn = get_connection()

    row = conn.execute(
        """
        SELECT id
        FROM batches
        WHERE device_id = ? AND batch_id = ?
        """,
        (device_id, batch_id),
    ).fetchone()

    conn.close()

    return row is not None


def insert_valid_batch(batch, received_at=None, connection=None):
    header = signed_header(batch)
    records = batch["records"]

    device_id = header["device_id"]
    batch_id = header["batch_id"]

    if received_at is None:
        received_at = now_iso()

    conn = connection if connection is not None else get_connection()

    try:
        if connection is None:
            conn.execute("BEGIN IMMEDIATE")

        conn.execute(
            """
            INSERT INTO batches
            (
                device_id,
                batch_id,
                header_json,
                raw_json,
                signature,
                status,
                received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                batch_id,
                json.dumps(
                    header,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    batch,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                batch["signature"],
                "accepted",
                received_at,
            ),
        )

        for record in records:
            conn.execute(
                """
                INSERT INTO records
                (
                    device_id,
                    batch_id,
                    sequence,
                    record_hash,
                    previous_hash,
                    record_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    batch_id,
                    record["sequence"],
                    record["record_hash"],
                    record["previous_hash"],
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    received_at,
                ),
            )

        if connection is None:
            conn.commit()

    except Exception:
        if connection is None:
            conn.rollback()
        raise

    finally:
        if connection is None:
            conn.close()


def insert_audit(
    device_id,
    batch_id,
    status,
    reason_code,
    affected_sequences=None,
    raw_batch=None,
    connection=None,
):
    conn = connection if connection is not None else get_connection()

    if affected_sequences is None:
        affected_sequences = []

    raw_json = None

    if raw_batch is not None:
        raw_json = json.dumps(
            raw_batch,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    conn.execute(
        """
        INSERT INTO audits
        (
            device_id,
            batch_id,
            status,
            reason_code,
            affected_sequences,
            raw_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            batch_id,
            status,
            reason_code,
            json.dumps(affected_sequences, ensure_ascii=False),
            raw_json,
            now_iso(),
        ),
    )

    if connection is None:
        conn.commit()
    if connection is None:
        conn.close()


def list_batches():
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            device_id,
            batch_id,
            header_json,
            signature,
            status,
            received_at
        FROM batches
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    result = []

    for row in rows:
        item = dict(row)
        item["header"] = json.loads(item.pop("header_json"))
        result.append(item)

    return result


def list_records(device_id=None, batch_id=None):
    conn = get_connection()

    sql = """
        SELECT
            device_id,
            batch_id,
            sequence,
            record_hash,
            previous_hash,
            record_json,
            created_at
        FROM records
        WHERE 1 = 1
    """

    params = []

    if device_id is not None:
        sql += " AND device_id = ?"
        params.append(device_id)

    if batch_id is not None:
        sql += " AND batch_id = ?"
        params.append(batch_id)

    sql += " ORDER BY device_id, sequence"

    rows = conn.execute(sql, params).fetchall()

    conn.close()

    result = []

    for row in rows:
        item = dict(row)
        item["record"] = json.loads(item.pop("record_json"))
        result.append(item)

    return result


def list_audits():
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            id,
            device_id,
            batch_id,
            status,
            reason_code,
            affected_sequences,
            raw_json,
            created_at
        FROM audits
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    result = []

    for row in rows:
        item = dict(row)

        if item["affected_sequences"]:
            item["affected_sequences"] = json.loads(
                item["affected_sequences"]
            )
        else:
            item["affected_sequences"] = []

        if item["raw_json"]:
            item["raw"] = json.loads(item.pop("raw_json"))
        else:
            item.pop("raw_json")

        result.append(item)

    return result


def get_last_record(device_id, connection=None):
    conn = connection if connection is not None else get_connection()

    row = conn.execute(
        """
        SELECT *
        FROM records
        WHERE device_id = ?
        ORDER BY sequence DESC
        LIMIT 1
        """,
        (device_id,),
    ).fetchone()

    if connection is None:
        conn.close()

    if not row:
        return None

    result = dict(row)
    result["record"] = json.loads(result.pop("record_json"))

    return result


def get_stats():
    conn = get_connection()

    devices = conn.execute(
        "SELECT COUNT(*) AS count FROM devices"
    ).fetchone()["count"]

    batches = conn.execute(
        "SELECT COUNT(*) AS count FROM batches"
    ).fetchone()["count"]

    records = conn.execute(
        "SELECT COUNT(*) AS count FROM records"
    ).fetchone()["count"]

    accepted = conn.execute(
        "SELECT COUNT(*) AS count FROM batches WHERE status = 'accepted'"
    ).fetchone()["count"]

    audits = conn.execute(
        "SELECT COUNT(*) AS count FROM audits"
    ).fetchone()["count"]

    conn.close()

    return {
        "devices": devices,
        "batches": batches,
        "records": records,
        "accepted_batches": accepted,
        "audits": audits,
    }



def process_batch(batch, validator):
    """同一写事务内完成查重、读可信链尾、验证及持久化，提交后才能返回。"""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        device_id = batch.get("device_id") if isinstance(batch, dict) else None
        batch_id = batch.get("batch_id") if isinstance(batch, dict) else None
        if not isinstance(device_id, str):
            device_id = None
        if not isinstance(batch_id, str):
            batch_id = None

        def finish(status, reason, affected=None, **extra):
            response = {
                "status": status, "reason_code": reason, "device_id": device_id,
                "batch_id": batch_id, "affected_sequences": affected or [], **extra,
            }
            if status != "accepted":
                insert_audit(device_id, batch_id, status, reason, affected, batch, connection=conn)
            conn.commit()
            return response

        if not device_id or not batch_id:
            return finish("rejected", "INVALID_BATCH_STRUCTURE")
        registration = get_device(device_id, connection=conn)
        if not registration:
            return finish("rejected", "UNKNOWN_DEVICE")
        existing = get_batch(device_id, batch_id, connection=conn)
        if existing:
            try:
                same_content = canonical_bytes(existing["raw"]) == canonical_bytes(batch)
            except (TypeError, ValueError):
                return finish("rejected", "INVALID_BATCH_STRUCTURE")
            if same_content:
                return finish("duplicate", "DUPLICATE_BATCH")
            return finish("rejected", "BATCH_CONTENT_CONFLICT")

        last_record = get_last_record(device_id, connection=conn)
        previous_hash = last_record["record_hash"] if last_record else "0" * 64
        start_sequence = last_record["sequence"] + 1 if last_record else 1
        validation = validator(batch, registration, previous_hash, start_sequence)
        if not validation["valid"]:
            return finish(
                "rejected", validation["reason_code"], validation["affected_sequences"],
                errors=validation["errors"], record_errors=validation["record_errors"],
            )
        # 数值递增与跨批次时间也只能以已接受的历史为基准。
        if last_record:
            try:
                previous_number = int(last_record["batch_id"].rsplit("-", 1)[1])
                previous_time = datetime.fromisoformat(last_record["record"]["timestamp"])
            except (KeyError, ValueError, TypeError) as exc:
                raise RuntimeError("中心已有记录不是最终合同格式，请使用独立测试数据库") from exc
            if int(batch_id.rsplit("-", 1)[1]) <= previous_number:
                return finish("rejected", "INVALID_BATCH_SEQUENCE")
            if datetime.fromisoformat(batch["start_time"]) <= previous_time:
                return finish("rejected", "INVALID_TIMESTAMP")
        insert_valid_batch(batch, connection=conn)
        return finish("accepted", "OK")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 保留原来的导入初始化，兼容已有设备登记脚本；startup 重复调用也安全。
init_db()
