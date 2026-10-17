"""一键恢复：把中心库还原成"测试污染之前"的状态。

背景（为什么会有残留）
----------------------
`center_platform.storage` 在 **import 时**就按 `DB_PATH` 建库并执行 `init_db()`。
早期版本的 `验证脚本_中心功能冒烟.py` 是先 `import storage`、再改
`storage.DB_PATH`，于是模块级那次 `init_db()` 已经落在正式库
`center_platform/data/center.db` 上，导致正式库被写入：

- `WL-001` / `WL-001-000001` 一个 accepted 批次 + 10 条记录；
- 4 条 2026-10-14 的审计（3 条 DUPLICATE_BATCH、1 条 ZZ-999 UNKNOWN_DEVICE）；
- `WL-900` 的公钥被测试密钥覆盖。

脚本本身已修好（现在在 import 之前设 `PINGLU_CENTER_DB`，临时库放 `_tmp/`），
`验证脚本_中心功能冒烟.py` 现在会自检路径、跑到非临时库就直接拒绝运行。

恢复来源
--------
`center_platform/data/cleanup-wl001/center.db.bak-20261010-231829`
是污染发生前（10-10 23:18）的最后一份完整备份，它是本脚本的恢复基准。

默认只做**忠实还原**：用基准库替换当前库。另外可选 `--prune-devices`，
顺手把历史遗留的 `WL-900` 从登记表/密钥/中心库里去掉，变成"七类各一台"。

用法
----
    python 验证脚本_一键恢复.py --dry-run               # 只打印将要做什么
    python 验证脚本_一键恢复.py                         # 忠实还原（先备份当前库）
    python 验证脚本_一键恢复.py --prune-devices         # 额外清掉 WL-900
    python 验证脚本_一键恢复.py --reset-simulator       # 同时复位 simulator.db
    python 验证脚本_一键恢复.py --reference <别的备份>  # 指定恢复基准
"""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CENTER_DB = ROOT / "center_platform" / "data" / "center.db"
SIMULATOR_DB = ROOT / "simulator.db"
KEYS_DIR = ROOT / "keys"
REGISTRY = ROOT / "test_data" / "registry_public.json"
DEFAULT_REFERENCE = (
    ROOT / "center_platform" / "data" / "cleanup-wl001"
    / "center.db.bak-20261010-231829"
)
ZERO_HASH = "0" * 64

OFFICIAL_DEVICES = (
    ("WL-001", "water_level"),
    ("WE-001", "weather"),
    ("NM-001", "navigation_mark"),
    ("SL-001", "slope"),
    ("LK-001", "lock"),
    ("DR-001", "drone"),
    ("SB-001", "survey_boat"),
)


def now_stamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def counts_of(path):
    if not Path(path).exists():
        return None

    connection = sqlite3.connect(str(path))

    try:
        result = {}
        for table in ("devices", "batches", "records", "audits"):
            try:
                result[table] = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            except sqlite3.Error:
                result[table] = "-"
        return result
    finally:
        connection.close()


def backup(path):
    if not Path(path).exists():
        return None

    for directory in (ROOT / "backup" / "restore", Path(path).parent):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{Path(path).name}.pre-restore-{now_stamp()}"
            shutil.copy2(path, target)
            return target
        except OSError:
            continue

    raise SystemExit("找不到可写的备份目录，已中止以免无备份改动数据。")


def prune_devices(dry_run):
    """把登记表/密钥/中心库收敛为七类各一台（去掉 WL-900 这类历史遗留项）。"""
    from common_crypto.sm import generate_keypair, sign_message, sm3_hex, verify_message

    registry = {}

    if REGISTRY.exists():
        try:
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            registry = {}

    keys_dir = KEYS_DIR
    keys_dir.mkdir(parents=True, exist_ok=True)

    result = {}

    for device_id, device_type in OFFICIAL_DEVICES:
        key_path = keys_dir / f"{device_id}.json"
        saved = None

        if key_path.exists():
            try:
                saved = json.loads(key_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None

        healthy = False

        if saved:
            probe = f"{device_id} restore check".encode("utf-8")
            try:
                healthy = verify_message(
                    saved["public_key"], probe,
                    sign_message(saved["private_key"], saved["public_key"], probe),
                )
            except (KeyError, ValueError, TypeError):
                healthy = False

        if not healthy:
            if dry_run:
                print(f"  [dry-run] 会重建 {device_id} 密钥")
                continue

            private_key, public_key = generate_keypair()
            saved = {"private_key": private_key, "public_key": public_key}
            key_path.write_text(
                json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  已重建 {device_id} 密钥")

        result[device_id] = {
            "device_id": device_id,
            "device_type": device_type,
            "longitude": "108.500000",
            "latitude": "22.000000",
            "public_key": saved["public_key"],
        }

    if dry_run:
        print("  [dry-run] 会写回登记表：", ", ".join(result) or "（缺密钥，先修密钥）")
        return

    if len(result) != len(OFFICIAL_DEVICES):
        raise SystemExit("有设备密钥缺失，已中止；请先 python init_project.py")

    temporary = REGISTRY.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(REGISTRY)
    print(f"  已写回登记表（{len(result)} 台）：{REGISTRY}")

    connection = sqlite3.connect(str(CENTER_DB))

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM devices")

        for device_id, item in result.items():
            try:
                fingerprint = sm3_hex(bytes.fromhex(item["public_key"]))
            except (TypeError, ValueError):
                fingerprint = None

            connection.execute(
                """
                INSERT INTO devices
                    (device_id, device_type, public_key, fingerprint,
                     longitude, latitude, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id, item["device_type"], item["public_key"], fingerprint,
                    item["longitude"], item["latitude"], now_iso(),
                ),
            )
        connection.commit()
    finally:
        connection.close()

    print(f"  中心库 devices 已收敛为 {len(result)} 台")


def reset_simulator(dry_run):
    if not SIMULATOR_DB.exists():
        print("  simulator.db 不存在，跳过")
        return

    if dry_run:
        print("  [dry-run] 会清空 generated_batches 并复位链尾")
        return

    connection = sqlite3.connect(str(SIMULATOR_DB))
    connection.row_factory = sqlite3.Row

    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        connection.execute("BEGIN IMMEDIATE")

        if "generated_batches" in tables:
            connection.execute("DELETE FROM generated_batches")

        if "simulator_state" in tables:
            for device_id, device_type in OFFICIAL_DEVICES:
                row = connection.execute(
                    "SELECT state_json FROM simulator_state WHERE device_id = ?",
                    (device_id,),
                ).fetchone()

                if row is None:
                    continue

                try:
                    state = json.loads(row["state_json"])
                except (TypeError, ValueError):
                    state = {}

                state.update({
                    "last_sequence": 0,
                    "last_hash": ZERO_HASH,
                    "last_batch_number": 0,
                    "last_timestamp": None,
                    "device_type": device_type,
                })
                connection.execute(
                    "UPDATE simulator_state SET state_json = ? WHERE device_id = ?",
                    (json.dumps(state, ensure_ascii=False), device_id),
                )

        connection.commit()
        print("  simulator.db 已复位")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="把中心库还原成测试污染之前的状态")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE,
                        help="恢复基准库")
    parser.add_argument("--prune-devices", action="store_true",
                        help="额外把登记表/密钥/中心库收敛为七类各一台（去掉 WL-900）")
    parser.add_argument("--reset-simulator", action="store_true",
                        help="同时复位 simulator.db 的链尾与待发队列")
    args = parser.parse_args()

    print("=" * 74)
    print("一键恢复")
    print(f"时间 {now_iso()}" + ("（dry-run，不改动任何文件）" if args.dry_run else ""))
    print("=" * 74)

    if not CENTER_DB.exists():
        raise SystemExit(f"中心库不存在：{CENTER_DB}")

    if not args.reference.exists():
        raise SystemExit(
            f"恢复基准库不存在：{args.reference}\n"
            "请用 --reference 指定一份可用的中心库备份。"
        )

    print("\n[1/4] 现状与恢复基准")
    print(f"  当前库 {CENTER_DB.name}: {counts_of(CENTER_DB)}")
    print(f"  基准库 {args.reference.name}: {counts_of(args.reference)}")
    print(f"  基准库路径：{args.reference}")

    print("\n[2/4] 备份当前库并还原")
    if args.dry_run:
        print("  [dry-run] 会备份当前 center.db，然后用基准库覆盖它")
    else:
        saved = backup(CENTER_DB)
        print(f"  已备份 → {saved}")
        shutil.copy2(args.reference, CENTER_DB)
        print(f"  已还原：{counts_of(CENTER_DB)}")
        simulator_backup = backup(SIMULATOR_DB) if args.reset_simulator else None
        if simulator_backup:
            print(f"  已备份 → {simulator_backup}")

    print("\n[3/4] 设备集处理")
    if args.prune_devices:
        prune_devices(args.dry_run)
    else:
        print("  保持基准库的设备集不变（要收敛为七类各一台请加 --prune-devices）")

    print("\n[4/4] simulator.db")
    if args.reset_simulator:
        reset_simulator(args.dry_run)
    else:
        print("  未改动（需要复位请加 --reset-simulator）")

    print("")
    print("=" * 74)

    if args.dry_run:
        print("dry-run 结束，未改动任何文件。去掉 --dry-run 即真正执行。")
    else:
        print("恢复完成。建议接着执行：")
        print("  python 验证脚本_中心功能冒烟.py                 # 应 14/14 且不动正式库")
        print("  python init_project.py                          # 校验密钥/登记表一致")
    print("=" * 74)

    return 0


if __name__ == "__main__":
    sys.exit(main())
