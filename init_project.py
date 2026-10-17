"""一键初始化：让任何人克隆后能干净复现，并修掉历史遗留的 WL-001 冲突。

背景
----
中心用 `device_id + batch_id` 做唯一约束。仓库里曾经入库过一批模拟器生成的
`WL-001-000001`，与 `test_data/normal_batch.json` 里的同名批次**批次号相同、内容不同**，
于是之后每次提交该样例都被判 `BATCH_CONTENT_CONFLICT`，`audits` 里不断累积冲突记录。
本脚本把中心库清空到"干净起点"，并把模拟器链尾状态复位，使两边重新对齐。

做四件事（顺序固定，改动前先备份）
--------------------------------
1. 生成或校验七类设备固定密钥（keys/）与公开登记表（test_data/registry_public.json）；
2. 按登记表重建中心库 devices 表，并打印每台设备的公钥 SM3 指纹供逐台核对；
3. 清空中心库 batches / records，并把 simulator.db 的链尾与待发队列复位；
4. 保留 audits 作为验证证据（加 --reset-audits 才一并清空）。

用法
----
    python init_project.py                    # 正常初始化（幂等，可反复执行）
    python init_project.py --dry-run          # 只打印将要做什么，不改动任何文件
    python init_project.py --reset-audits     # 连 audits 一起清空
    python init_project.py --devices 7        # 登记台数（按七类循环）
    python init_project.py --center-db other.db --simulator-db other_sim.db
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

DEFAULT_KEYS_DIR = ROOT / "keys"
DEFAULT_REGISTRY = ROOT / "test_data" / "registry_public.json"
DEFAULT_CENTER_DB = ROOT / "center_platform" / "data" / "center.db"
DEFAULT_SIMULATOR_DB = ROOT / "simulator.db"

# 备份目录候选：优先放到中心库旁边，被文件权限挡住时依次退让。
BACKUP_DIR_CANDIDATES = (
    ROOT / "center_platform" / "data" / "init-backup",
    ROOT / "backup" / "init",
)


def stamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def say(message):
    print(message, flush=True)


# --------------------------------------------------------------------------
# 1. 密钥与登记表
# --------------------------------------------------------------------------

def prepare_registry(devices, keys_dir, registry_path, dry_run, regenerate=False):
    """生成或校验设备密钥与公开登记表；返回登记表内容。

    登记表是**共享且已提交**的权威文件（任务书：由 A 生成，B 与队长导入并核对指纹）。
    因此本地私钥对不上时，默认只报告、不改动，要求队长去向 A 要正确私钥；
    只有显式 --regenerate-mismatched 才重新生成身份并改写登记表。
    """
    from common_crypto.sm import generate_keypair, sm3_hex, sign_message, verify_message

    registry_path = Path(registry_path)
    keys_dir = Path(keys_dir)

    existing = {}

    if registry_path.exists():
        try:
            existing = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    if dry_run:
        say(f"[dry-run] 会生成/校验 {devices} 台设备密钥 → {keys_dir}")
        say(f"[dry-run] 会写出公开登记表 → {registry_path}")

        if existing:
            for device_id in sorted(existing):
                key_path = keys_dir / f"{device_id}.json"

                if not key_path.exists():
                    say(f"[dry-run]   {device_id}：缺少私钥，会重新生成并同步登记表")
                    continue

                try:
                    saved = json.loads(key_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    say(f"[dry-run]   {device_id}：私钥文件不可读，会重新生成")
                    continue

                if saved.get("public_key") != existing[device_id].get("public_key"):
                    say(f"[dry-run]   {device_id}：私钥与登记表公钥不一致，会重新生成并同步")

        return existing

    keys_dir.mkdir(parents=True, exist_ok=True)

    if not existing:
        # 全新仓库：按七类循环生成，并写出公开登记表。
        from device_simulator.register import register_devices

        registry = register_devices(devices, keys_dir, registry_path)
        say(f"已生成 {len(registry)} 台设备密钥与公开登记表")
    else:
        # 已有登记表：登记表是**共享且已提交**的权威文件（任务书：由 A 生成、
        # B 与队长导入并核对指纹）。因此本地私钥对不上时，默认**只报告、不改动**，
        # 由队长决定是"去向 A 要正确私钥"还是"轮换身份并通知所有人"。
        registry = dict(existing)
        mismatched = {}

        for device_id, item in sorted(registry.items()):
            key_path = keys_dir / f"{device_id}.json"
            saved = None

            if key_path.exists():
                try:
                    saved = json.loads(key_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    saved = None

            if not saved:
                mismatched[device_id] = "缺少私钥"
                continue

            if saved.get("public_key") != item.get("public_key"):
                mismatched[device_id] = "私钥公钥与登记表不一致"
                continue

            probe = f"{device_id} 初始化自检".encode("utf-8")

            try:
                healthy = verify_message(
                    saved["public_key"], probe,
                    sign_message(saved["private_key"], saved["public_key"], probe),
                )
            except (KeyError, ValueError, TypeError):
                healthy = False

            if not healthy:
                mismatched[device_id] = "私钥不自洽"

        if mismatched and not regenerate:
            say("")
            say("!" * 74)
            say("发现本地私钥与登记表不一致，已停止（未改动任何文件）：")
            for device_id, reason in mismatched.items():
                say(f"  {device_id:<9} {reason}")
            say("")
            say("登记表是共享且已提交的权威文件，本地私钥对不上时**不能私自重新生成**，")
            say("否则推送后会让 A、B 的批次签名验不过。请按下面之一处理：")
            say("  1) 向 A 索取与登记表匹配的设备私钥，放到 keys/<设备ID>.json（推荐）")
            say("  2) 确实要轮换身份时，加 --regenerate-mismatched 重新生成，")
            say("     并**通知 A 和 B 同步新的登记表与私钥**")
            say("!" * 74)
            raise SystemExit(3)

        repaired = []

        for device_id in mismatched:
            private_key, public_key = generate_keypair()
            probe = f"{device_id} 初始化自检".encode("utf-8")

            if not verify_message(
                public_key, probe, sign_message(private_key, public_key, probe)
            ):
                raise SystemExit(f"{device_id} 新生成的密钥无法自洽，已中止")

            (keys_dir / f"{device_id}.json").write_text(
                json.dumps(
                    {"private_key": private_key, "public_key": public_key},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            registry[device_id]["public_key"] = public_key
            repaired.append(device_id)

        if repaired:
            say(
                f"已修复 {len(repaired)} 台设备的密钥与登记表不一致："
                f"{', '.join(repaired)}"
            )
        else:
            say(f"已校验 {len(registry)} 台设备密钥与登记表一致")

        temporary = registry_path.with_suffix(".json.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(registry_path)

    # 收尾自检：登记表里每一台都必须有配套私钥。
    for device_id, item in registry.items():
        key_path = keys_dir / f"{device_id}.json"

        if not key_path.exists():
            raise SystemExit(f"{device_id} 缺少密钥文件：{key_path}")

        saved = json.loads(key_path.read_text(encoding="utf-8"))
        probe = f"{device_id} 初始化自检".encode("utf-8")

        if not verify_message(
            saved["public_key"], probe,
            sign_message(saved["private_key"], saved["public_key"], probe),
        ):
            raise SystemExit(f"{device_id} 的密钥不配套，登记表不可信")

        if saved["public_key"] != item["public_key"]:
            raise SystemExit(f"{device_id} 密钥文件与登记表公钥不一致")

    return registry


# --------------------------------------------------------------------------
# 2~4. 中心库与模拟器库
# --------------------------------------------------------------------------

def resolve_backup_dir(preferred=None):
    """选出第一个可写的备份目录；都不可写时返回 None。

    只写一个普通文件做探测，不新建子目录，避免在受限目录里失败。
    """
    candidates = ([Path(preferred)] if preferred else []) + list(BACKUP_DIR_CANDIDATES)

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue

    return None


def backup(path, dry_run, backup_dir=None):
    """改动前备份；返回备份路径或 None。"""
    if not path.exists():
        return None

    if dry_run:
        say(f"[dry-run] 会备份 {path.name}")
        return None

    target_dir = resolve_backup_dir(backup_dir)

    if target_dir is None:
        raise SystemExit(
            "找不到可写的备份目录，已中止以免无备份改动数据。"
            "请用 --backup-dir 指定一个可写路径。"
        )

    target = target_dir / f"{path.name}.bak-{stamp()}"
    shutil.copy2(path, target)
    say(f"已备份 {path.name} → {target}")
    return target


def table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def count(conn, table):
    if not table_exists(conn, table):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def reset_center_db(center_db, registry, reset_audits, dry_run):
    """按登记表重建 devices，清空 batches/records（可选 audits）。

    中心库不存在或还没有表结构时，先用中心自己的 init_db 建好，再写入登记表。
    """
    if not center_db.exists() and not dry_run:
        say(f"中心库不存在，先按中心 schema 创建：{center_db.relative_to(ROOT)}")
        from center_platform import storage

        original = storage.DB_PATH
        storage.DB_PATH = center_db
        try:
            center_db.parent.mkdir(parents=True, exist_ok=True)
            storage.init_db()
        finally:
            storage.DB_PATH = original

    if not center_db.exists():
        say(f"[dry-run] 中心库尚不存在，将由中心平台创建：{center_db.relative_to(ROOT)}")
        return

    conn = sqlite3.connect(str(center_db))
    conn.row_factory = sqlite3.Row

    try:
        if not table_exists(conn, "devices"):
            say("中心库还没有表结构，跳过（中心平台启动时会建表）")
            return
        before = {
            table: count(conn, table)
            for table in ("devices", "batches", "records", "audits")
        }
        say(
            "中心库改动前："
            + "，".join(f"{k}={v}" for k, v in before.items())
        )

        if dry_run:
            say("[dry-run] 会清空 batches / records")
            if reset_audits:
                say("[dry-run] 会清空 audits")
            say(f"[dry-run] 会按登记表重建 {len(registry)} 台 devices")
            return

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM records")
        conn.execute("DELETE FROM batches")

        if reset_audits:
            conn.execute("DELETE FROM audits")

        # devices 用 upsert 保持主键，避免删表后外键顺序问题。
        for device_id, item in registry.items():
            from common_crypto.sm import sm3_hex

            try:
                fingerprint = sm3_hex(bytes.fromhex(item["public_key"]))
            except (TypeError, ValueError):
                fingerprint = None

            conn.execute(
                """
                INSERT INTO devices
                    (device_id, device_type, public_key, fingerprint,
                     longitude, latitude, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_type = excluded.device_type,
                    public_key  = excluded.public_key,
                    fingerprint = excluded.fingerprint,
                    longitude   = excluded.longitude,
                    latitude    = excluded.latitude
                """,
                (
                    device_id,
                    item["device_type"],
                    item["public_key"],
                    fingerprint,
                    item.get("longitude"),
                    item.get("latitude"),
                    now_iso(),
                ),
            )

        # 登记表之外的旧登记项一并清掉，避免页面出现"历史登记项"。
        existing = [
            row["device_id"]
            for row in conn.execute("SELECT device_id FROM devices")
        ]
        extra = [device_id for device_id in existing if device_id not in registry]

        for device_id in extra:
            conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))

        conn.commit()

        after = {
            table: count(conn, table)
            for table in ("devices", "batches", "records", "audits")
        }
        say("中心库改动后：" + "，".join(f"{k}={v}" for k, v in after.items()))

        if extra:
            say(f"已移除登记表之外的旧登记项 {len(extra)} 条：{', '.join(sorted(extra))}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_simulator_db(simulator_db, registry, dry_run):
    """清空待发队列，并把登记表内设备的链尾状态复位到起点。"""
    if not simulator_db.exists():
        say(f"模拟器库不存在，跳过：{simulator_db.name}")
        return

    conn = sqlite3.connect(str(simulator_db))
    conn.row_factory = sqlite3.Row

    try:
        pending = count(conn, "generated_batches")
        states = count(conn, "simulator_state")
        say(f"模拟器库改动前：generated_batches={pending}，simulator_state={states}")

        if dry_run:
            say("[dry-run] 会清空 generated_batches，并复位 simulator_state 链尾")
            return

        conn.execute("BEGIN IMMEDIATE")

        if table_exists(conn, "generated_batches"):
            conn.execute("DELETE FROM generated_batches")

        reset_count = 0

        if table_exists(conn, "simulator_state"):
            for device_id, item in registry.items():
                row = conn.execute(
                    "SELECT state_json FROM simulator_state WHERE device_id = ?",
                    (device_id,),
                ).fetchone()

                if row is None:
                    continue

                try:
                    state = json.loads(row["state_json"])
                except (TypeError, ValueError):
                    state = {}

                # 复位到"尚未生成任何批次"，使下一次生成重新从 000001 / 序号 1 开始。
                state.update({
                    "last_sequence": 0,
                    "last_hash": "0" * 64,
                    "last_batch_number": 0,
                    "last_timestamp": None,
                    "public_key": item["public_key"],
                    "device_type": item["device_type"],
                })

                conn.execute(
                    "UPDATE simulator_state SET state_json = ? WHERE device_id = ?",
                    (json.dumps(state, ensure_ascii=False), device_id),
                )
                reset_count += 1

        conn.commit()
        say(
            f"模拟器库改动后：generated_batches="
            f"{count(conn, 'generated_batches')}，已复位 {reset_count} 台设备链尾"
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def print_fingerprints(registry):
    from common_crypto.sm import sm3_hex

    say("")
    say("公钥 SM3 指纹（B 与队长应逐台核对）：")
    for device_id in sorted(registry):
        item = registry[device_id]
        try:
            fingerprint = sm3_hex(bytes.fromhex(item["public_key"]))
        except (TypeError, ValueError):
            fingerprint = "（公钥格式不合法）"
        say(f"  {device_id:<10} {item['device_type']:<16} {fingerprint}")


def warn_if_registry_drifted(registry_path):
    """登记表是共享且已提交的文件。若工作区版本与 git HEAD 不同，必须大声警告。

    本地 `keys/` 与工作区登记表"彼此一致"并不能说明没问题——如果两者都是本地
    重新生成过的，它们会自洽但**和 A/B 的界面对不上**。推送后会让别人的批次
    签名验不过，而样例批次也会在本地验签失败。
    """
    import subprocess

    try:
        top = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return

    if top.returncode != 0:
        return  # 不在 git 仓库里，跳过

    try:
        relative = registry_path.resolve().relative_to(
            Path(top.stdout.strip()).resolve()
        ).as_posix()
    except (ValueError, OSError):
        return

    try:
        shows = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{relative}"],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return

    if shows.returncode != 0:
        return  # 还没被跟踪，无需比对

    try:
        committed = json.loads(shows.stdout)
        current = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    drifted = []

    for device_id in sorted(set(committed) | set(current)):
        old = (committed.get(device_id) or {}).get("public_key")
        new = (current.get(device_id) or {}).get("public_key")

        if old != new:
            drifted.append(device_id)

    if not drifted:
        return

    say("")
    say("!" * 74)
    say("警告：工作区的公开登记表与 git HEAD 不一致：")
    for device_id in drifted:
        old = (committed.get(device_id) or {}).get("public_key") or "（无）"
        new = (current.get(device_id) or {}).get("public_key") or "（无）"
        say(f"  {device_id:<9} HEAD={old[:16]}…  工作区={new[:16]}…")
    say("")
    say("登记表是共享的权威文件。本地重新生成过密钥会让：")
    say("  · 已提交的样例批次在本地验签失败（INVALID_SIGNATURE）；")
    say("  · 推送后 A、B 的批次签名验不过。")
    say("建议：git checkout -- test_data/registry_public.json 回到共享版本，")
    say("      再向 A 索取与登记表匹配的设备私钥放到 keys/。")
    say("!" * 74)
    say("")


def main():
    parser = argparse.ArgumentParser(
        description="一键初始化：密钥/登记表、中心库、模拟器链尾",
    )
    parser.add_argument("--devices", type=int, default=7, help="登记台数，按七类循环（默认 7）")
    parser.add_argument("--keys-dir", type=Path, default=DEFAULT_KEYS_DIR)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--center-db", type=Path, default=DEFAULT_CENTER_DB)
    parser.add_argument("--simulator-db", type=Path, default=DEFAULT_SIMULATOR_DB)
    parser.add_argument("--reset-audits", action="store_true", help="连 audits 一起清空")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不改动任何文件")
    parser.add_argument("--backup-dir", type=Path, default=None, help="备份目录（默认自动选择）")
    parser.add_argument("--regenerate-mismatched", action="store_true",
                        help="本地私钥与登记表不一致时重新生成身份并改写登记表（会改变已提交的共享登记表，须通知 A/B）")
    args = parser.parse_args()

    if args.devices < 1:
        raise SystemExit("--devices 必须是正整数")

    say("=" * 68)
    say("平陆运河国密可信采集系统 · 一键初始化")
    say(f"时间：{now_iso()}")
    if args.dry_run:
        say("模式：dry-run（不改动任何文件）")
    say("=" * 68)

    warn_if_registry_drifted(args.registry)

    registry = prepare_registry(
        args.devices, args.keys_dir, args.registry, args.dry_run,
        regenerate=args.regenerate_mismatched,
    )

    if not registry:
        raise SystemExit("登记表为空，无法继续；请先去掉 --dry-run 生成密钥与登记表")

    say("")
    backup(args.center_db, args.dry_run, args.backup_dir)
    backup(args.simulator_db, args.dry_run, args.backup_dir)

    reset_center_db(args.center_db, registry, args.reset_audits, args.dry_run)
    say("")
    reset_simulator_db(args.simulator_db, registry, args.dry_run)

    if registry:
        print_fingerprints(registry)

    say("")
    say("=" * 68)
    if args.dry_run:
        say("dry-run 结束，未改动任何文件。去掉 --dry-run 即真正执行。")
    else:
        say("初始化完成。下一步：")
        say("  1) 起中心：.venv\\Scripts\\python.exe -m uvicorn center_platform.main:app --port 8000")
        say("  2) 生成数据：.venv\\Scripts\\python.exe 验证脚本_搭建页面数据.py center_platform/data/demo.db")
        say("  3) 打开页面：http://127.0.0.1:8000/")
    say("=" * 68)


if __name__ == "__main__":
    main()
