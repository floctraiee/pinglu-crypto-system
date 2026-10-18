# -*- coding: utf-8 -*-
"""把网关的只读状态写成 JSON，供中心离线页读取（B1）。

为什么需要这个文件
------------------
中心 `/offline-status` 优先读「B 端网关只读状态文件」，找不到才降级去读
模拟器的 `simulator.db`。降级来源只能反映"模拟器发了多少"，反映不了
"网关缓存了多少、断点在哪、补传进度如何"——这三个正是任务书要看的东西。

约定
----
字段定义见 `docs/gateway_status_contract.md`，与
`center_platform/telemetry.py::_read_gateway_status` 一一对应。

写入用「临时文件 + 原子替换」，中心任何时刻读到都是完整 JSON，不会读到半个。
"""
import json
from pathlib import Path

from . import config, storage

#: 设备 ID 前缀 → 设备类型（与 device_simulator/register.py 的 DEVICE_TYPES 对齐）
DEVICE_TYPES = {
    "WL": "water_level",
    "WE": "weather",
    "NM": "navigation_mark",
    "SL": "slope",
    "LK": "lock",
    "DR": "drone",
    "SB": "survey_boat",
}

#: 视为"已补传完成"的终态
_DONE_STATUSES = ("accepted", "duplicate")

#: 仍需补传的状态
_PENDING_STATUSES = ("pending", "audit_pending")


def _device_type(device_id):
    prefix = device_id.split("-", 1)[0] if device_id else ""
    return DEVICE_TYPES.get(prefix)


def collect():
    """汇总每台设备的缓存与补传进度，返回可直接落盘的字典。"""
    conn = storage.get_connection()

    done_list = ",".join("'%s'" % s for s in _DONE_STATUSES)
    pending_list = ",".join("'%s'" % s for s in _PENDING_STATUSES)

    rows = conn.execute(
        f"""
        SELECT device_id,
               COUNT(*)                                              AS cached_batches,
               SUM(COALESCE(last_seq, 0) - COALESCE(first_seq, 0) + 1) AS cached_records,
               SUM(CASE WHEN status IN ({done_list}) THEN 1 ELSE 0 END)     AS resend_done,
               SUM(CASE WHEN status IN ({pending_list}) THEN 1 ELSE 0 END)  AS resend_pending,
               MAX(gateway_received_at)                              AS last_recv,
               MAX(acked_at)                                         AS last_ack
        FROM pending_batches
        GROUP BY device_id
        ORDER BY device_id
        """
    ).fetchall()

    chains = {
        row["device_id"]: row["last_sequence"]
        for row in conn.execute("SELECT device_id, last_sequence FROM device_chain")
    }

    devices = []

    for row in rows:
        device_id = row["device_id"]
        pending = int(row["resend_pending"] or 0)

        devices.append({
            "device_id": device_id,
            "device_type": _device_type(device_id),
            "cached_batches": int(row["cached_batches"] or 0),
            "cached_records": int(row["cached_records"] or 0),
            "resend_done": int(row["resend_done"] or 0),
            "resend_pending": pending,
            # 网关侧可信链尾：中心据此判断"补到哪一条"
            "break_sequence": chains.get(device_id),
            "last_gateway_received_at": row["last_recv"],
            "last_center_ack_at": row["last_ack"],
            "note": "待补传 %d 批" % pending if pending else "已全部确认",
        })

    return {
        "generated_at": storage.now_iso(),
        "source": "edge_gateway.status",
        "queue": storage.count_by_status(),
        "latency": storage.latency_stats(),
        "devices": devices,
    }


def write(path=None):
    """把状态写成 JSON（原子替换）。返回写出的字典，便于调用方打日志。"""
    target = Path(path or config.STATUS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = collect()
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)          # 同目录 rename，POSIX/Windows 都是原子替换
    return payload


if __name__ == "__main__":
    storage.init_db()
    result = write()
    print("已写出", config.STATUS_PATH)
    print("设备数  :", len(result["devices"]))
    print("队列    :", json.dumps(result["queue"], ensure_ascii=False))
    print("延迟    :", json.dumps(result["latency"], ensure_ascii=False))
