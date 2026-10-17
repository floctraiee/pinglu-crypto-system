"""中心平台的联调上报数据读取层（只读，不写任何业务库）。

任务书三（网页与演示证据）要求：
- 离线页可从网关的只读状态接口或联调上报数据读取缓存数、断点和补传进度；
- 性能页的每个数字要能对应到 CSV 文件、测试日期和设备规模。

本模块只负责把这两类数据读出来交给接口层，不参与批次验证，也不改动
devices/batches/records/audits 四张业务表。
"""

import csv
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 网关（B 端）可选的只读状态落盘位置；存在时优先于模拟器数据库。
# 约定字段见 docs/gateway_status_contract.md，B 端按此写出即可被页面自动识别。
# 可用 PINGLU_GATEWAY_STATUS 指向别处（验证或异机部署时用）。
_GATEWAY_ENV = os.environ.get("PINGLU_GATEWAY_STATUS")

GATEWAY_STATUS_CANDIDATES = tuple(
    [Path(_GATEWAY_ENV)] if _GATEWAY_ENV else []
) + (
    ROOT / "edge_gateway" / "data" / "gateway_status.json",
    ROOT / "edge_gateway" / "gateway_status.json",
)

# B 端网关自己的缓存库（edge_gateway/storage.py 建的表）。
# 网关没有写 JSON 状态文件时，直接只读这张库——里面就有缓存数、断点和
# "网关收到 → 中心确认"的 latency_ms，正是任务书要的实时 P95。
SIMULATOR_DB = Path(
    os.environ.get("PINGLU_SIMULATOR_DB", str(ROOT / "simulator.db"))
)

GATEWAY_DB = Path(
    os.environ.get("PINGLU_GATEWAY_DB", str(ROOT / "edge_gateway" / "data" / "edge_cache.db"))
)

# 签名对照 CSV：优先在 docs/evidence 找，其次仓库根目录。
PERFORMANCE_GLOBS = (
    "docs/evidence/benchmark_signing*.csv",
    "docs/evidence/性能对照*.csv",
    "benchmark_signing*.csv",
    "signing_benchmark*.csv",
)


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 离线补传
# --------------------------------------------------------------------------

def _empty_offline(source, note, devices=None):
    return {
        "available": bool(devices),
        "source": source,
        "source_note": note,
        "collected_at": now_iso(),
        "summary": {
            "cached_batches": 0,
            "cached_records": 0,
            "resend_done": 0,
            "resend_pending": 0,
            "devices_with_backlog": 0,
            "progress_percent": 0.0,
        },
        "devices": devices or [],
    }


def _read_gateway_status(path):
    """读取 B 端网关写出的只读状态文件。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    devices = payload.get("devices")

    if not isinstance(devices, list):
        return None

    normalized = []

    for item in devices:
        if not isinstance(item, dict):
            continue

        device_id = item.get("device_id")

        if not isinstance(device_id, str) or not device_id:
            continue

        normalized.append({
            "device_id": device_id,
            "device_type": item.get("device_type"),
            "cached_batches": int(item.get("cached_batches") or 0),
            "cached_records": int(item.get("cached_records") or 0),
            "resend_done": int(item.get("resend_done") or 0),
            "resend_pending": int(item.get("resend_pending") or 0),
            "break_sequence": item.get("break_sequence"),
            "last_gateway_received_at": item.get("last_gateway_received_at"),
            "last_center_ack_at": item.get("last_center_ack_at"),
            "note": item.get("note"),
        })

    result = _empty_offline(
        "gateway_status_file",
        f"来自 B 端网关只读状态文件：{path.relative_to(ROOT)}",
        normalized,
    )
    result["collected_at"] = payload.get("generated_at") or now_iso()
    result["summary"] = _summarize(normalized)
    return result


def _read_simulator_db(path):
    """从模拟器待发队列读出缓存数与补传进度（联调上报数据的降级来源）。"""
    if not path.exists():
        return None

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        if "generated_batches" not in tables:
            return None

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(generated_batches)")
        }

        # 旧库没有 mqtt_status 列；此时"待发"只能按 status 判断。
        status_column = "mqtt_status" if "mqtt_status" in columns else "status"
        pending_value = "pending"

        states = {}

        if "simulator_state" in tables:
            for row in conn.execute(
                "SELECT device_id, state_json FROM simulator_state"
            ):
                try:
                    state = json.loads(row["state_json"])
                except (TypeError, ValueError):
                    state = {}

                states[row["device_id"]] = {
                    "device_type": state.get("device_type"),
                    "last_sequence": state.get("last_sequence"),
                    "last_hash": state.get("last_hash"),
                    "last_batch_number": state.get("last_batch_number"),
                    "last_timestamp": state.get("last_timestamp"),
                }

        rows = conn.execute(
            f"""
            SELECT device_id, batch_id, raw_json, {status_column} AS mqtt_status
            FROM generated_batches
            ORDER BY device_id, batch_id
            """
        ).fetchall()
    finally:
        conn.close()

    by_device = {}

    for row in rows:
        entry = by_device.setdefault(row["device_id"], {
            "pending_batches": 0,
            "confirmed_batches": 0,
            "pending_records": 0,
            "pending_sequences": [],
        })

        if row["mqtt_status"] == pending_value:
            entry["pending_batches"] += 1

            # 待发批次需要真实序号区间，这里才解析原始批次（只解析待发项）。
            try:
                batch = json.loads(row["raw_json"])
                start = batch["start_sequence"]
                end = batch["end_sequence"]
                count = batch["count"]
            except (TypeError, ValueError, KeyError):
                continue

            entry["pending_records"] += count
            entry["pending_sequences"].append([start, end])
        else:
            entry["confirmed_batches"] += 1

    devices = []

    for device_id in sorted(set(by_device) | set(states)):
        counts = by_device.get(device_id) or {
            "pending_batches": 0, "confirmed_batches": 0,
            "pending_records": 0, "pending_sequences": [],
        }
        state = states.get(device_id, {})
        ranges = counts["pending_sequences"]

        # 断点：待发区间的最早起点；没有积压时回落到已生成的最后序号。
        break_sequence = ranges[0][0] if ranges else state.get("last_sequence")

        devices.append({
            "device_id": device_id,
            "device_type": state.get("device_type"),
            "cached_batches": counts["pending_batches"],
            "cached_records": counts["pending_records"],
            "resend_done": counts["confirmed_batches"],
            "resend_pending": counts["pending_batches"],
            "break_sequence": break_sequence,
            "pending_ranges": [
                f"{start}~{end}" if start != end else str(start)
                for start, end in ranges
            ],
            "last_gateway_received_at": state.get("last_timestamp"),
            "last_center_ack_at": None,
            "note": "来自模拟器待发队列（联调上报数据）",
            "device_last_batch_number": state.get("last_batch_number"),
        })

    result = _empty_offline(
        "simulator_db",
        (
            "未发现 B 端网关只读状态文件，降级为模拟器待发队列（联调上报数据）。"
            "网关就绪后自动切换到网关状态。"
        ),
        devices,
    )
    result["source_path"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    result["summary"] = _summarize(devices)
    return result


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def _read_gateway_db(path):
    """只读 B 端网关自己的缓存库（edge_gateway/storage.py 的 pending_batches）。

    这个来源比模拟器队列更权威：它记录网关真实收到的批次、初验结论、
    待传/已确认状态，以及 latency_ms（网关收到 → 中心给出终态）。
    """
    if not path.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None

    conn.row_factory = sqlite3.Row

    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        if "pending_batches" not in tables:
            return None

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(pending_batches)")
        }

        def column(name, fallback="NULL"):
            return name if name in columns else f"{fallback} AS {name}"

        rows = conn.execute(
            f"""
            SELECT device_id,
                   status,
                   first_seq,
                   last_seq,
                   {column("received_at")},
                   {column("gateway_received_at")},
                   {column("latency_ms")},
                   {column("last_error")},
                   {column("center_reason_code")},
                   {column("batch_no")}
            FROM pending_batches
            ORDER BY device_id, batch_no
            """
        ).fetchall()

        audit_count = 0

        if "gateway_audit" in tables:
            audit_count = conn.execute(
                "SELECT COUNT(*) FROM gateway_audit"
            ).fetchone()[0]
    finally:
        conn.close()

    # 终态 = 中心已明确回复；其余都算还没落定（含待传与初验失败待复核）
    final_statuses = {"accepted", "rejected", "duplicate", "conflict"}

    by_device = {}
    latencies = []

    for row in rows:
        entry = by_device.setdefault(row["device_id"], {
            "cached": 0, "confirmed": 0, "pending": 0, "rejected": 0,
            "ranges": [], "last_received": None, "last_error": None,
        })

        status = row["status"] or "pending"

        if status in final_statuses:
            entry["confirmed"] += 1
            if status in ("rejected", "conflict"):
                entry["rejected"] += 1
        else:
            entry["pending"] += 1
            entry["cached"] += 1

        if row["first_seq"] is not None and row["last_seq"] is not None:
            if status not in final_statuses:
                entry["ranges"].append(
                    f"{row['first_seq']}~{row['last_seq']}"
                    if row["first_seq"] != row["last_seq"]
                    else str(row["first_seq"])
                )

        stamp = row["gateway_received_at"] or row["received_at"]

        if stamp and (entry["last_received"] is None or stamp > entry["last_received"]):
            entry["last_received"] = stamp

        if row["last_error"]:
            entry["last_error"] = row["last_error"]

        latency = row["latency_ms"]

        # 实时 P95 只统计拿到中心终态的批次；离线补传计入会污染指标，
        # 这里按网关同样口径：只看有 latency 的实时批次。
        if latency is not None and status in final_statuses:
            latencies.append(float(latency))

    devices = []

    for device_id in sorted(by_device):
        item = by_device[device_id]
        first_range = item["ranges"][0].split("~")[0] if item["ranges"] else None

        devices.append({
            "device_id": device_id,
            "device_type": None,
            "cached_batches": item["cached"],
            "cached_records": None,
            "resend_done": item["confirmed"],
            "resend_pending": item["pending"],
            "break_sequence": first_range,
            "pending_ranges": item["ranges"],
            "last_gateway_received_at": item["last_received"],
            "last_center_ack_at": None,
            "note": "来自网关缓存库 pending_batches"
                    + (f"；最近错误 {item['last_error']}" if item["last_error"] else ""),
        })

    result = _empty_offline(
        "gateway_db",
        f"来自 B 端网关缓存库（只读）：{path.name}",
        devices,
    )
    result["source_path"] = str(path)
    result["summary"] = _summarize(devices)

    # 缓存记录数：网关库里没有按批次的记录条数，用序号区间估算不可靠，
    # 因此这里留空而不是编造。
    result["summary"]["cached_records"] = None
    result["summary"]["gateway_audit_events"] = audit_count
    result["latency_ms"] = {
        "count": len(latencies),
        "p50": round(_percentile(latencies, 0.50), 2) if latencies else None,
        "p95": round(_percentile(latencies, 0.95), 2) if latencies else None,
        "p99": round(_percentile(latencies, 0.99), 2) if latencies else None,
        "max": round(max(latencies), 2) if latencies else None,
        "note": "网关收到批次 → 中心给出终态；口径与网关 latency_stats 一致",
    }

    return result


def _summarize(devices):
    cached_batches = sum(item["cached_batches"] for item in devices)
    # 只有模拟器队列知道每批的记录条数；网关库没有这一列，此时如实留空。
    known_records = [item["cached_records"] for item in devices]
    cached_records = (
        sum(known_records) if all(value is not None for value in known_records) else None
    )
    done = sum(item["resend_done"] for item in devices)
    pending = sum(item["resend_pending"] for item in devices)
    total = done + pending

    return {
        "cached_batches": cached_batches,
        "cached_records": cached_records,
        "resend_done": done,
        "resend_pending": pending,
        "devices_with_backlog": sum(1 for item in devices if item["cached_batches"] > 0),
        "progress_percent": round(done * 100.0 / total, 1) if total else 0.0,
    }


def gateway_edge_verdicts():
    """读出网关对每批的初验结论，供审计页「边缘判定」列使用。

    网关 `pending_batches.edge_result` 存的是它初验的结果
    （valid / reason_code / affected_sequences）。中心复核与网关初验是**两个独立判断**，
    页面上并排列出，才能体现"中心不采信网关结论，独立重验"。

    返回 {(device_id, batch_id): {...}}；网关库不存在或没有该列时返回空字典。
    """
    path = GATEWAY_DB

    if not path.exists():
        return {}

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}

    conn.row_factory = sqlite3.Row

    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        if "pending_batches" not in tables:
            return {}

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(pending_batches)")
        }

        if "edge_result" not in columns:
            return {}

        rows = conn.execute(
            """
            SELECT device_id, batch_id, edge_result
            FROM pending_batches
            WHERE edge_result IS NOT NULL AND edge_result <> ''
            """
        ).fetchall()
    finally:
        conn.close()

    verdicts = {}

    for row in rows:
        try:
            payload = json.loads(row["edge_result"])
        except (TypeError, ValueError):
            continue

        if not isinstance(payload, dict):
            continue

        verdicts[(row["device_id"], row["batch_id"])] = {
            "valid": payload.get("valid"),
            "reason_code": payload.get("reason_code"),
            "affected_sequences": payload.get("affected_sequences") or [],
        }

    return verdicts


def collect_offline_status():
    """离线补传面板的数据入口。

    优先级：网关只读状态文件 → 网关缓存库 → 模拟器待发队列。
    前两个是 B 端网关的真实状态；模拟器队列只在网关未运行时兜底。
    """
    for path in GATEWAY_STATUS_CANDIDATES:
        if path.exists():
            try:
                result = _read_gateway_status(path)
            except (OSError, ValueError, KeyError):
                result = None

            if result:
                return result

    try:
        result = _read_gateway_db(GATEWAY_DB)
    except sqlite3.Error:
        result = None

    if result:
        return result

    try:
        result = _read_simulator_db(SIMULATOR_DB)
    except sqlite3.Error:
        result = None

    if result:
        return result

    return _empty_offline(
        "none",
        (
            "既没有 B 端网关只读状态文件，也没有可读的网关缓存库或模拟器待发队列。"
            "启动网关后这里会显示缓存数、断点和补传进度。"
        ),
    )


# --------------------------------------------------------------------------
# 性能对照（签名次数 / 耗时 / 报文大小 / P95）
# --------------------------------------------------------------------------

PERFORMANCE_COLUMNS = (
    "batch_size",
    "batch_count",
    "sign_calls",
    "sign_ms_total",
    "sign_ms_avg",
    "sign_ms_p95",
    "sign_ms_min",
    "sign_ms_max",
    "verify_calls",
    "verify_ms_total",
    "verify_ms_avg",
    "verify_ms_p95",
    "verify_ms_min",
    "verify_ms_max",
    "payload_bytes",
    "records",
    "tested_at",
    "device_scale",
    "note",
)

INT_COLUMNS = {"batch_size", "batch_count", "sign_calls", "verify_calls", "payload_bytes", "records"}
FLOAT_COLUMNS = {
    "sign_ms_total", "sign_ms_avg", "sign_ms_p95", "sign_ms_min", "sign_ms_max",
    "verify_ms_total", "verify_ms_avg", "verify_ms_p95",
    "verify_ms_min", "verify_ms_max",
}


def _coerce(row):
    result = {}

    for column in PERFORMANCE_COLUMNS:
        value = row.get(column)

        if value is None or value == "":
            result[column] = None
            continue

        try:
            if column in INT_COLUMNS:
                result[column] = int(float(value))
            elif column in FLOAT_COLUMNS:
                result[column] = float(value)
            else:
                result[column] = str(value)
        except (TypeError, ValueError):
            result[column] = str(value)

    return result


def find_performance_csv():
    for pattern in PERFORMANCE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file():
                return path

    return None


def collect_performance():
    """读签名对照 CSV；文件不存在时如实返回缺失原因，不编造数字。"""
    path = find_performance_csv()

    if path is None:
        return {
            "available": False,
            "reason": "MISSING_CSV",
            "message": (
                "尚未生成签名对照 CSV。请先运行 "
                "`python tests/benchmark_signing.py`，"
                "并把输出放到 docs/evidence/ 或仓库根目录。"
            ),
            "expected_file": "docs/evidence/benchmark_signing_<日期>.csv",
            "expected_columns": list(PERFORMANCE_COLUMNS),
            "rows": [],
        }

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [_coerce(row) for row in reader]
            columns = reader.fieldnames or []
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return {
            "available": False,
            "reason": "UNREADABLE_CSV",
            "message": f"签名对照 CSV 读取失败：{exc}",
            "expected_file": str(path.relative_to(ROOT)),
            "expected_columns": list(PERFORMANCE_COLUMNS),
            "rows": [],
        }

    rows.sort(key=lambda row: (row.get("batch_size") or 0, row.get("tested_at") or ""))

    tested_at = next(
        (row["tested_at"] for row in rows if row.get("tested_at")),
        None,
    )
    device_scale = next(
        (row["device_scale"] for row in rows if row.get("device_scale")),
        None,
    )

    return {
        "available": bool(rows),
        "reason": None if rows else "EMPTY_CSV",
        "message": None if rows else "签名对照 CSV 里没有数据行。",
        "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "file_name": path.name,
        "file_mtime": datetime.fromtimestamp(
            path.stat().st_mtime, timezone(timedelta(hours=8))
        ).isoformat(timespec="seconds"),
        "columns": columns,
        "tested_at": tested_at,
        "device_scale": device_scale,
        "rows": rows,
    }
