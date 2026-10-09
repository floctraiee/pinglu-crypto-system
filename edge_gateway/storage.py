"""网关本地持久队列与可信链尾。

两张表：
    pending_batches  收到的每一批（原文 + 网关初判 + 转发状态 + 尝试次数）
    device_chain     每台设备网关侧的最后一条摘要与序号，用于批间衔接与重放识别

状态机：
    pending        初验通过，等待转发中心
    audit_pending  初验失败，原文待送中心留证（九步第 6 步）
    accepted       中心已确认（终态）
    rejected       中心已拒绝并已写审计（终态）
    duplicate      中心判定重复且内容一致（终态）
    conflict       中心判定同批次号但内容不同（终态，异常）

三条铁律：
  1. 收到 MQTT 批次后先保存原始 JSON，**事务提交成功才算网关已持有这批数据**（任务书 P0015）
  2. QoS 1 会重复送达，靠 (device_id, batch_id) 唯一约束挡住，不生成第二条待传任务
  3. 重启后一切从数据库恢复断点，不依赖内存计数（九步第 9 步）

查看当前队列：
    .venv\\Scripts\\python.exe -m edge_gateway.storage
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

STATUS_PENDING = "pending"
STATUS_AUDIT_PENDING = "audit_pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_DUPLICATE = "duplicate"
STATUS_CONFLICT = "conflict"

#: 还需要向中心发送的状态（终态之外的都不算完成）
OPEN_STATUSES = (STATUS_PENDING, STATUS_AUDIT_PENDING)

#: 中心明确回复后才允许进入的终态
FINAL_STATUSES = (
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_DUPLICATE,
    STATUS_CONFLICT,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id           TEXT    NOT NULL,
    batch_id            TEXT    NOT NULL,
    first_seq           INTEGER,
    last_seq            INTEGER,
    raw_json            TEXT    NOT NULL,
    edge_result         TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    received_at         TEXT    NOT NULL,
    last_error          TEXT,
    acked_at            TEXT,
    gateway_received_at TEXT,
    batch_no            INTEGER,
    center_status       TEXT,
    center_reason_code  TEXT,
    center_response     TEXT,
    UNIQUE(device_id, batch_id)
);

CREATE INDEX IF NOT EXISTS idx_pending_status
    ON pending_batches(status, device_id, batch_no);

CREATE TABLE IF NOT EXISTS device_chain (
    device_id     TEXT PRIMARY KEY,
    last_hash     TEXT    NOT NULL,
    last_sequence INTEGER NOT NULL,
    last_batch_no INTEGER NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- 连设备ID/批次号都取不到的消息（超长、JSON 解析失败、不是对象）。
-- 九步第 2 步要求"解析失败也存审计日志"，且绝不能让订阅程序崩溃。
CREATE TABLE IF NOT EXISTS gateway_audit (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_received_at TEXT    NOT NULL,
    topic               TEXT,
    device_id           TEXT,
    batch_id            TEXT,
    event               TEXT    NOT NULL,
    reason_code         TEXT,
    detail              TEXT,
    raw_text            TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_time
    ON gateway_audit(gateway_received_at);
"""


# ---------------------------------------------------------------- 基础

def now_iso():
    """项目统一使用 +08:00 时区（contract.md §1）。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def batch_number(batch_id):
    """从 "设备ID-六位批次号" 取出整数批次号；格式不对时返回 None。"""
    if not isinstance(batch_id, str) or "-" not in batch_id:
        return None
    tail = batch_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def get_connection():
    path = Path(config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _rows(rows):
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- 写入

def save_received(batch, raw_text, edge_result, gateway_received_at=None):
    """收到一批就落库，并把网关侧链尾推进到这一批。

    返回 {"inserted": bool, "status": str}。
    inserted=False 表示这一批之前已经收到过（QoS 1 重传），不再生成第二条待传任务。
    """
    device_id = batch["device_id"]
    batch_id = batch["batch_id"]
    number = batch_number(batch_id)
    received_at = now_iso()

    if gateway_received_at is None:
        gateway_received_at = received_at

    status = STATUS_PENDING if edge_result.get("valid") else STATUS_AUDIT_PENDING

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        cursor = conn.execute(
            """
            INSERT INTO pending_batches
                (device_id, batch_id, first_seq, last_seq, raw_json, edge_result,
                 status, attempts, received_at, gateway_received_at, batch_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(device_id, batch_id) DO NOTHING
            """,
            (
                device_id,
                batch_id,
                batch.get("start_sequence"),
                batch.get("end_sequence"),
                raw_text,
                json.dumps(edge_result, ensure_ascii=False, sort_keys=True),
                status,
                received_at,
                gateway_received_at,
                number,
            ),
        )

        if cursor.rowcount == 0:
            row = conn.execute(
                "SELECT status FROM pending_batches WHERE device_id = ? AND batch_id = ?",
                (device_id, batch_id),
            ).fetchone()
            conn.commit()
            return {"inserted": False, "status": row["status"] if row else None}

        # 只有初次入库、且初验通过时才推进链尾，避免异常批次污染后续校验
        if status == STATUS_PENDING and number is not None:
            conn.execute(
                """
                INSERT INTO device_chain
                    (device_id, last_hash, last_sequence, last_batch_no, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    last_hash     = excluded.last_hash,
                    last_sequence = excluded.last_sequence,
                    last_batch_no = excluded.last_batch_no,
                    updated_at    = excluded.updated_at
                """,
                (
                    device_id,
                    batch["records"][-1]["record_hash"],
                    batch["end_sequence"],
                    number,
                    received_at,
                ),
            )

        conn.commit()
        return {"inserted": True, "status": status}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_result(device_id, batch_id, status, reason_code=None, response=None):
    """记下中心的回复并把该批置为终态。"""
    if status not in FINAL_STATUSES:
        raise ValueError(f"不是终态：{status}")

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE pending_batches
               SET status = ?, center_status = ?, center_reason_code = ?,
                   center_response = ?, acked_at = ?
             WHERE device_id = ? AND batch_id = ?
            """,
            (
                status,
                (response or {}).get("status"),
                reason_code,
                json.dumps(response, ensure_ascii=False, sort_keys=True) if response else None,
                now_iso(),
                device_id,
                batch_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def record_attempt(device_id, batch_id, error):
    """转发失败：保留 pending，累加尝试次数并记下最后一次错误。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE pending_batches
               SET attempts = attempts + 1, last_error = ?
             WHERE device_id = ? AND batch_id = ?
            """,
            (str(error)[:500], device_id, batch_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_audit(event, topic=None, device_id=None, batch_id=None,
               reason_code=None, detail=None, raw_text=None,
               gateway_received_at=None):
    """记录连设备ID/批次号都取不到的消息（九步第 2 步）。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO gateway_audit
                (gateway_received_at, topic, device_id, batch_id,
                 event, reason_code, detail, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gateway_received_at or now_iso(),
                topic,
                device_id,
                batch_id,
                event,
                reason_code,
                detail,
                (raw_text or "")[:4096],
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 读取

def get_batch(device_id, batch_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM pending_batches WHERE device_id = ? AND batch_id = ?",
            (device_id, batch_id),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_raw_batch(device_id, batch_id):
    """取回原始批次文本，供转发时按原文送中心。"""
    row = get_batch(device_id, batch_id)
    return row["raw_json"] if row else None


def get_edge_result(device_id, batch_id):
    row = get_batch(device_id, batch_id)
    if not row or not row["edge_result"]:
        return None
    return json.loads(row["edge_result"])


def pending_device_ids():
    """有未发送批次的设备，按设备 ID 排序。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT device_id FROM pending_batches
             WHERE status IN ({",".join("?" * len(OPEN_STATUSES))})
             ORDER BY device_id
            """,
            OPEN_STATUSES,
        ).fetchall()
    finally:
        conn.close()
    return [row["device_id"] for row in rows]


def next_pending(device_id):
    """该设备**最靠前**的一批正常待传批次。

    九步第 7 步要求"不能让后续批次抢在同设备前一批之前确认"，
    所以每台设备一次只取一批，按 batch_no 升序。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM pending_batches
             WHERE device_id = ? AND status = ?
             ORDER BY batch_no, id
             LIMIT 1
            """,
            (device_id, STATUS_PENDING),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_pending(device_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM pending_batches
             WHERE device_id = ? AND status IN (?, ?)
             ORDER BY batch_no, id
            """,
            (device_id, STATUS_PENDING, STATUS_AUDIT_PENDING),
        ).fetchall()
    finally:
        conn.close()
    return _rows(rows)


def list_audit_pending():
    """初验失败、还要送中心留证的原文（九步第 6 步）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM pending_batches
             WHERE status = ?
             ORDER BY device_id, batch_no, id
            """,
            (STATUS_AUDIT_PENDING,),
        ).fetchall()
    finally:
        conn.close()
    return _rows(rows)


def list_gateway_audits(limit=100):
    """网关本地审计（连设备ID都取不到的消息）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM gateway_audit
             ORDER BY id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return _rows(rows)


def list_unconfirmed():
    """所有还没拿到中心终态的批次（重启后据此恢复队列）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM pending_batches
             WHERE status IN ({",".join("?" * len(OPEN_STATUSES))})
             ORDER BY device_id, batch_no, id
            """,
            OPEN_STATUSES,
        ).fetchall()
    finally:
        conn.close()
    return _rows(rows)


def count_by_status():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM pending_batches GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    return {row["status"]: row["count"] for row in rows}


# ---------------------------------------------------------------- 链尾

def get_device_chain(device_id):
    """返回 (previous_hash, start_sequence)；没有历史时为 (64个0, 1)。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM device_chain WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"last_hash": "0" * 64, "last_sequence": 0, "last_batch_no": 0}

    return {
        "last_hash": row["last_hash"],
        "last_sequence": row["last_sequence"],
        "last_batch_no": row["last_batch_no"],
    }


def set_device_chain(device_id, last_hash, last_sequence, last_batch_no):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO device_chain
                (device_id, last_hash, last_sequence, last_batch_no, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                last_hash     = excluded.last_hash,
                last_sequence = excluded.last_sequence,
                last_batch_no = excluded.last_batch_no,
                updated_at    = excluded.updated_at
            """,
            (device_id, last_hash, last_sequence, last_batch_no, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"数据库: {config.DB_PATH}")
    counts = count_by_status()
    if not counts:
        print("暂无批次记录")
    for status, count in sorted(counts.items()):
        print(f"  {status:<14} {count}")
    print()
    for row in list_unconfirmed():
        print(f"  待处理 {row['device_id']} {row['batch_id']} "
              f"seq {row['first_seq']}~{row['last_seq']} "
              f"尝试 {row['attempts']} 次 状态 {row['status']}")
