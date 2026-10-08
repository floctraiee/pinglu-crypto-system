import sqlite3, json, os
from datetime import datetime
from .config import DB_PATH

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS pending_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        first_seq INTEGER NOT NULL,
        last_seq INTEGER NOT NULL,
        raw_json TEXT NOT NULL,
        edge_result TEXT,
        status TEXT DEFAULT 'pending',
        attempts INTEGER DEFAULT 0,
        received_at TEXT NOT NULL,
        last_error TEXT,
        acked_at TEXT,
        UNIQUE(device_id, batch_id)
    );
    """
    with get_connection() as conn:
        conn.execute(create_table_sql)
        conn.commit()
    print(f"[Storage] 数据库初始化完成，路径: {DB_PATH}")

def save_batch(device_id, batch_id, first_seq, last_seq, raw_json, edge_result):
    received_at = datetime.now().isoformat()
    edge_result_str = json.dumps(edge_result, ensure_ascii=False)
    insert_sql = """
    INSERT OR IGNORE INTO pending_batches 
    (device_id, batch_id, first_seq, last_seq, raw_json, edge_result, received_at)
    VALUES (?, ?, ?, ?, ?, ?, ?);
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(insert_sql, (device_id, batch_id, first_seq, last_seq, raw_json, edge_result_str, received_at))
        conn.commit()
        return cursor.rowcount > 0  # 返回True表示新批次，False表示重复批次

def get_pending_batches():
    query_sql = "SELECT * FROM pending_batches WHERE status = 'pending' ORDER BY device_id ASC, first_seq ASC;"
    with get_connection() as conn:
        rows = conn.execute(query_sql).fetchall()
        return [dict(row) for row in rows]

def mark_acked(device_id, batch_id):
    acked_at = datetime.now().isoformat()
    update_sql = "UPDATE pending_batches SET status = 'acked', acked_at = ?, last_error = NULL WHERE device_id = ? AND batch_id = ?;"
    with get_connection() as conn:
        conn.execute(update_sql, (acked_at, device_id, batch_id))
        conn.commit()

def mark_error(device_id, batch_id, error_msg):
    update_sql = "UPDATE pending_batches SET attempts = attempts + 1, last_error = ? WHERE device_id = ? AND batch_id = ?;"
    with get_connection() as conn:
        conn.execute(update_sql, (error_msg, device_id, batch_id))
        conn.commit()

if __name__ == "__main__":
    init_db()
    print(f"当前待传批次数量: {len(get_pending_batches())}")