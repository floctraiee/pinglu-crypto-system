"""把公开登记表里的设备原样登记到中心平台数据库。

用法：
    python register_all_devices.py                    # 使用默认登记表和默认数据库
    python register_all_devices.py --registry test_data/registry_public.json
    python register_all_devices.py --db center_platform/data/center.db
    python register_all_devices.py --fingerprints     # 附带打印公钥 SM3 指纹供核对
    python register_all_devices.py --keep-extra       # 保留登记表之外的旧登记项

约定：
- 只读公开登记表，文件中不含私钥。
- device_id、device_type、public_key、longitude、latitude 全部按原样登记，不改名、不改值。
- 重复执行安全：已登记设备按登记表内容更新，不会产生重复行。
- 默认清掉登记表之外的设备登记项（连同它的批次与记录），使中心库只保留登记表里的设备；
  审计表是验证证据，不做删除。需要保留旧登记项时加 --keep-extra。
"""

import argparse
import json
import sqlite3
from pathlib import Path

from common_crypto.sm import sm3_hex


ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "test_data" / "registry_public.json"
DEFAULT_DB = ROOT / "center_platform" / "data" / "center.db"

REGISTRATION_FIELDS = (
    "device_id",
    "device_type",
    "longitude",
    "latitude",
    "public_key",
)


def load_registry(path):
    """读取公开登记表，并逐台检查字段完整、类型正确。"""
    registry = json.loads(Path(path).read_text(encoding="utf-8"))

    if not isinstance(registry, dict) or not registry:
        raise ValueError("登记表必须是 device_id -> 登记信息的非空对象")

    for device_id, registration in registry.items():
        if not isinstance(registration, dict):
            raise ValueError(f"{device_id}：登记信息必须是对象")

        missing = [
            field for field in REGISTRATION_FIELDS
            if field not in registration
        ]

        if missing:
            raise ValueError(f"{device_id}：缺少字段 {missing}")

        for field in REGISTRATION_FIELDS:
            if not isinstance(registration[field], str):
                raise ValueError(f"{device_id}：{field} 必须是字符串")

        if registration["device_id"] != device_id:
            raise ValueError(f"{device_id}：device_id 与登记表键名不一致")

        public_key = registration["public_key"]

        if len(public_key) != 128:
            raise ValueError(f"{device_id}：公钥不是 64 字节")

        try:
            bytes.fromhex(public_key)
        except ValueError as exc:
            raise ValueError(f"{device_id}：公钥不是合法十六进制") from exc

    return registry


def ensure_coordinate_columns(connection):
    """旧库没有经纬度列时补上，保证登记信息不丢。"""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(devices)")
    }

    added = []

    for name in ("longitude", "latitude"):
        if name not in columns:
            connection.execute(f"ALTER TABLE devices ADD COLUMN {name} TEXT")
            added.append(name)

    return added


def check_db_schema(connection):
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'devices'"
    ).fetchone()

    if row is None:
        raise RuntimeError("中心数据库里没有 devices 表，请先启动中心平台初始化")


def find_extra_devices(connection, registry):
    """找出中心库里不属于本次登记表的设备。"""
    registered = {row[0] for row in connection.execute("SELECT device_id FROM devices")}
    known = set(registry)

    return sorted(registered - known)


def remove_extra_devices(connection, extra_devices):
    """删除非登记表设备的登记项，并连带删除它的批次与记录（审计证据保留）。"""
    removed = {}

    for device_id in extra_devices:
        counts = {}

        for table in ("batches", "records"):
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE device_id = ?",
                (device_id,),
            ).fetchone()[0]

        for table in ("batches", "records", "devices"):
            connection.execute(
                f"DELETE FROM {table} WHERE device_id = ?",
                (device_id,),
            )

        removed[device_id] = counts

    connection.commit()
    return removed


def register_devices(connection, registry):
    """按登记表原样登记，已存在的设备就地更新。"""
    for device_id, registration in sorted(registry.items()):
        connection.execute(
            """
            INSERT INTO devices
            (
                device_id,
                device_type,
                public_key,
                fingerprint,
                longitude,
                latitude,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_type = excluded.device_type,
                public_key = excluded.public_key,
                fingerprint = excluded.fingerprint,
                longitude = excluded.longitude,
                latitude = excluded.latitude
            """,
            (
                device_id,
                registration["device_type"],
                registration["public_key"],
                sm3_hex(bytes.fromhex(registration["public_key"])),
                registration["longitude"],
                registration["latitude"],
                "1970-01-01T00:00:00+08:00",
            ),
        )

    connection.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--fingerprints", action="store_true")
    parser.add_argument(
        "--keep-extra",
        action="store_true",
        help="保留登记表之外的设备登记项（默认会清掉，只留登记表里的设备）",
    )
    args = parser.parse_args()

    registry = load_registry(args.registry)

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row

    try:
        check_db_schema(connection)
        added = ensure_coordinate_columns(connection)
        register_devices(connection, registry)

        removed = {}

        if not args.keep_extra:
            extra = find_extra_devices(connection, registry)
            removed = remove_extra_devices(connection, extra)

        rows = connection.execute(
            """
            SELECT device_id, device_type, longitude, latitude, public_key
            FROM devices
            ORDER BY device_id
            """
        ).fetchall()
    finally:
        connection.close()

    if added:
        print("已为中心数据库补充字段：", added)

    print("登记表：", args.registry)
    print("数据库：", args.db)
    print(f"本次登记 {len(registry)} 台设备，中心库现有 {len(rows)} 台：")

    for row in rows:
        suffix = ""

        if args.fingerprints:
            suffix = " 指纹 " + sm3_hex(bytes.fromhex(row["public_key"]))

        print(
            f"  {row['device_id']:<10} {row['device_type']:<16} "
            f"({row['longitude']}, {row['latitude']}){suffix}"
        )

    if removed:
        print("已清掉不在登记表里的设备（含其批次与记录，审计证据保留）：")

        for device_id, counts in removed.items():
            print(
                f"  {device_id:<10} 批次 {counts['batches']} 个，"
                f"记录 {counts['records']} 条"
            )


if __name__ == "__main__":
    main()
