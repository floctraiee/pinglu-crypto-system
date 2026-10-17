"""清理中心库里 WL-001 的脏批次，并把 A 端该设备复位到可重发的状态。

背景
----
中心库里已受理的 WL-001-000001（2026-10-09 由设备模拟器生成）与
test_data/normal_batch.json 里的 WL-001-000001 批次号相同、内容不同，
因此样例每次提交都被判 BATCH_CONTENT_CONFLICT，audits 里不断累积冲突记录，
复查、复用该批次都会撞上同一份脏数据。

本脚本只做三件事（顺序固定，先备份后改动）：

1. 备份 center.db 和 simulator.db 到 center_platform/data/
   （文件名带时间戳，并附一份改动前的证据 JSON）；
2. 删掉 center.db 里 WL-001 的 batches 与 records；
   设备登记项 devices 保留（它是 A 的可信公钥，不属于脏数据）；
   audits 保留（冲突审计是证据，按合同必须留存）；
3. 复位 simulator.db 里 WL-001 的链尾状态与待发批次，
   使下一次生成重新从 WL-001-000001、序号 1、previous_hash=全 0 开始。

用法：
    python cleanup_wl001_conflict.py                # 执行清理
    python cleanup_wl001_conflict.py --dry-run      # 只打印将要删除的内容
    python cleanup_wl001_conflict.py --device SL-001
"""

import argparse
import json
import shutil
import sqlite3
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CENTER_DB = ROOT / "center_platform" / "data" / "center.db"
SIMULATOR_DB = ROOT / "simulator.db"
ZERO_HASH = "0" * 64


def rmtree(path):
    """删除目录树；遇到只读文件先去掉只读位，Windows 上才能删。"""
    def on_error(func, target, exc_info):
        try:
            Path(target).chmod(stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onerror=on_error)


def now_stamp():
    """与中心 now_iso 同一时区习惯：带 +08:00。"""
    return datetime.now(timezone(timedelta(hours=8)))


def connect(path, readonly=False):
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def center_snapshot(conn, device_id):
    """改动前先抓取该设备在中心库里的全部相关内容，作为证据留存。"""
    batches = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM batches WHERE device_id = ? ORDER BY batch_id",
            (device_id,),
        )
    ]
    records = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM records WHERE device_id = ? ORDER BY sequence",
            (device_id,),
        )
    ]
    audits = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM audits WHERE device_id = ? ORDER BY id",
            (device_id,),
        )
    ]
    device = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    return {
        "batches": batches,
        "records": records,
        "audits": audits,
        "device": dict(device) if device else None,
    }


def simulator_snapshot(conn, device_id):
    batches = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM generated_batches WHERE device_id = ? ORDER BY batch_id",
            (device_id,),
        )
    ]
    state_row = conn.execute(
        "SELECT state_json FROM simulator_state WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    state = json.loads(state_row["state_json"]) if state_row else None
    return {"generated_batches": batches, "simulator_state": state}


def integrity(conn):
    return conn.execute("PRAGMA integrity_check").fetchone()[0]


def table_counts(conn, tables, device_id):
    counts = {}
    for table in tables:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        counts[table] = row["n"]
    return counts


def backup(paths, out_dir, stamp):
    """先复制文件再改动；复制后校验完整性和字节数。

    out_dir 必须是调用方已经建好的目录：某些受限运行环境（例如带文件
    沙箱的自动化会话）只允许在会话开始时就存在的路径下写入，新建目录
    会被拒绝，因此这里不自行创建目录，而是提前给出明确错误。
    """
    if not out_dir.is_dir():
        raise RuntimeError(
            f"备份目录不存在，请先创建：{out_dir}"
        )
    saved = []

    for path in paths:
        target = out_dir / f"{path.name}.bak-{stamp}"
        shutil.copy2(path, target)

        original = connect(path, readonly=True)
        copy = connect(target, readonly=True)
        try:
            checks = {
                "original": integrity(original),
                "copy": integrity(copy),
            }
        finally:
            original.close()
            copy.close()

        same_size = path.stat().st_size == target.stat().st_size

        if checks["copy"] != "ok" or checks["original"] != "ok" or not same_size:
            raise RuntimeError(
                f"备份校验失败：{target}（原始 {checks['original']}，"
                f"副本 {checks['copy']}，字节数一致={same_size}）"
            )

        saved.append(
            {
                "source": str(path),
                "backup": str(target),
                "bytes": target.stat().st_size,
                "integrity_check": checks["copy"],
            }
        )
        print(f"  备份 OK  {target}（{target.stat().st_size} 字节，完整性 {checks['copy']}）")

    return saved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="WL-001")
    parser.add_argument("--center-db", type=Path, default=CENTER_DB)
    parser.add_argument("--simulator-db", type=Path, default=SIMULATOR_DB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="备份与证据的存放目录；默认放在 center_platform/data/ 下",
    )
    parser.add_argument(
        "--remove-tmp",
        action="store_true",
        help="顺带清掉工作区里本次会话留下的 _tmp 临时目录（pytest 临时文件）",
    )
    parser.add_argument(
        "--purge-device",
        action="store_true",
        help="连设备登记项一起删掉（用于清掉中心里不该存在的设备，"
             "例如密钥对不上的 WL-900）；默认保留登记项",
    )
    parser.add_argument(
        "--keep-audits",
        action="store_true",
        help="配合 --purge-device：保留该设备的审计记录，默认一起删掉",
    )
    args = parser.parse_args()

    device_id = args.device
    center_db = args.center_db
    simulator_db = args.simulator_db

    if args.remove_tmp:
        tmp_dir = ROOT / "_tmp"
        print(f"清理临时目录：{tmp_dir}")
        if not tmp_dir.exists():
            print("  不存在，跳过。")
        else:
            tmp_dir.chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            rmtree(tmp_dir)
            print("  已删除。" if not tmp_dir.exists() else "  仍存在，删除未完成。")
            return 0 if not tmp_dir.exists() else 1
        return 0

    for path in (center_db, simulator_db):
        if not path.exists():
            print(f"找不到文件：{path}", file=sys.stderr)
            return 1

    stamp = now_stamp().strftime("%Y%m%d-%H%M%S")
    out_dir = (
        args.out_dir
        if args.out_dir is not None
        else center_db.parent / f"cleanup-{device_id.lower().replace('-', '')}-{stamp}"
    )
    # 受限环境只能写会话开始时就存在的路径；无法在目标位置建目录时退回
    # 工作区根目录暂存，脚本结束前会给出需要移动过去的提示。
    # dry-run 不写任何东西，也不建目录。
    staged = False
    if not args.dry_run and not out_dir.is_dir():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"无法在 {out_dir} 建目录（{exc}），改为在工作区根目录暂存。")
            staged = True
            out_dir = ROOT / f"cleanup-{device_id.lower().replace('-', '')}-{stamp}"
            if not out_dir.is_dir():
                try:
                    out_dir.mkdir(parents=True, exist_ok=True)
                except OSError as inner:
                    print(f"暂存目录同样不可写：{inner}", file=sys.stderr)
                    return 1
            print(f"暂存目录：{out_dir}")

    center = connect(center_db)
    simulator = connect(simulator_db)

    try:
        if integrity(center) != "ok" or integrity(simulator) != "ok":
            print("改动前完整性检查未通过，已停止。", file=sys.stderr)
            return 1

        device_row = center.execute(
            "SELECT device_id, device_type, fingerprint, longitude, latitude "
            "FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()

        if device_row is None:
            print(f"中心库没有登记设备 {device_id}，无需清理。")
            return 0

        center_before = center_snapshot(center, device_id)
        simulator_before = simulator_snapshot(simulator, device_id)

        print(f"设备 {device_id}（{device_row['device_type']}）")
        print("模式：", "删除设备登记项（--purge-device）" if args.purge_device else "保留设备登记项")
        print("改动前：")
        print(
            f"  中心库  批次 {len(center_before['batches'])} 个，"
            f"记录 {len(center_before['records'])} 条，"
            f"审计 {len(center_before['audits'])} 条，"
            f"设备登记 {'1 项' if device_row else '无'}"
        )
        print(
            f"  模拟器  待发批次 {len(simulator_before['generated_batches'])} 个，"
            f"链尾状态 {'有' if simulator_before['simulator_state'] else '无'}"
        )
        for batch in center_before["batches"]:
            print(f"    中心批次 {batch['batch_id']} status={batch['status']}")
        for audit in center_before["audits"]:
            print(
                f"    审计 {audit['id']}: {audit['status']} / {audit['reason_code']}"
                f" @ {audit['created_at']}"
            )
        if args.purge_device and args.keep_audits:
            print("  审计：保留")
        elif args.purge_device:
            print("  审计：一并删除")
        else:
            print("  审计：保留为证据")

        if args.dry_run:
            print("\ndry-run：未做任何改动，未生成备份。")
            return 0

        evidence = {
            "device_id": device_id,
            "created_at": now_stamp().isoformat(timespec="seconds"),
            "reason": (
                "该设备的批次/登记与中心可信状态不符（批次号冲突或签名对不上），"
                "按队长要求清理"
            ),
            "mode": "purge-device" if args.purge_device else "clean-batches-only",
            "center_db": str(center_db),
            "simulator_db": str(simulator_db),
            "deletes": {
                "center_platform/data/center.db": (
                    ["batches WHERE device_id", "records WHERE device_id"]
                    + (["devices WHERE device_id"] if args.purge_device else [])
                    + (
                        ["audits WHERE device_id"]
                        if args.purge_device and not args.keep_audits
                        else []
                    )
                ),
                "simulator.db": [
                    "generated_batches WHERE device_id",
                    "simulator_state WHERE device_id（复位，不是删除）",
                ],
            },
            "keeps": {
                "center.devices": (
                    "已删除（--purge-device）" if args.purge_device else "保留"
                ),
                "center.audits": (
                    "保留" if (not args.purge_device or args.keep_audits) else "已删除"
                ),
                "simulator_state.public_key/device_type": "复位后仍按当前密钥重生成",
            },
            "center_before": center_before,
            "simulator_before": simulator_before,
        }

        print(f"\n备份目录：{out_dir}")
        saved = backup([center_db, simulator_db], out_dir, stamp)
        (out_dir / f"evidence-before-{device_id}-cleanup-{stamp}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"  证据文件 evidence-before-{device_id}-cleanup-{stamp}.json")

        # 中心：删该设备的批次与记录；--purge-device 时连登记项（和审计）一起删。
        try:
            center.execute("BEGIN IMMEDIATE")
            removed_records = center.execute(
                "DELETE FROM records WHERE device_id = ?", (device_id,)
            ).rowcount
            removed_batches = center.execute(
                "DELETE FROM batches WHERE device_id = ?", (device_id,)
            ).rowcount
            removed_device = 0
            removed_audits = 0
            if args.purge_device:
                removed_device = center.execute(
                    "DELETE FROM devices WHERE device_id = ?", (device_id,)
                ).rowcount
                if not args.keep_audits:
                    removed_audits = center.execute(
                        "DELETE FROM audits WHERE device_id = ?", (device_id,)
                    ).rowcount
            center.commit()
        except Exception:
            center.rollback()
            raise

        # 模拟器：待发批次与链尾状态一起复位，否则下一批会从序号 11 续链。
        state = simulator_before["simulator_state"]
        try:
            simulator.execute("BEGIN IMMEDIATE")
            removed_pending = simulator.execute(
                "DELETE FROM generated_batches WHERE device_id = ?", (device_id,)
            ).rowcount
            if state is None:
                removed_state = 0
            else:
                reset_state = {
                    "last_sequence": 0,
                    "last_hash": ZERO_HASH,
                    "last_batch_number": 0,
                    "last_timestamp": None,
                    "public_key": state["public_key"],
                    "device_type": state["device_type"],
                }
                removed_state = simulator.execute(
                    "UPDATE simulator_state SET state_json = ? WHERE device_id = ?",
                    (json.dumps(reset_state, ensure_ascii=False, sort_keys=True), device_id),
                ).rowcount
            simulator.commit()
        except Exception:
            simulator.rollback()
            raise

        print(
            f"\n已删除：中心批次 {removed_batches} 个、记录 {removed_records} 条"
            + (f"、设备登记 {removed_device} 项" if args.purge_device else "")
            + (f"、审计 {removed_audits} 条" if removed_audits else "")
            + f"；模拟器待发批次 {removed_pending} 个，复位链尾 {removed_state} 项。"
        )

        # 复核：改动后重新读取，只有读出来的结果才算数。
        ok = True
        if integrity(center) != "ok" or integrity(simulator) != "ok":
            print("改动后完整性检查未通过。", file=sys.stderr)
            return 1

        center_after = table_counts(
            center, ("batches", "records", "devices", "audits"), device_id
        )
        if center_after["batches"] or center_after["records"]:
            ok = False
        expected_devices = 0 if args.purge_device else 1
        if center_after["devices"] != expected_devices:
            ok = False
        if args.purge_device and not args.keep_audits and center_after["audits"]:
            ok = False
        if not args.purge_device and center_after["audits"] != len(center_before["audits"]):
            ok = False

        sim_after = table_counts(simulator, ("generated_batches",), device_id)
        state_after_row = simulator.execute(
            "SELECT state_json FROM simulator_state WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        state_after = json.loads(state_after_row["state_json"]) if state_after_row else None

        print("\n改动后复核：")
        print(
            f"  中心库  批次 {center_after['batches']} 个，"
            f"记录 {center_after['records']} 条，"
            f"审计 {center_after['audits']} 条，"
            f"设备登记 {center_after['devices']} 项"
        )
        print(f"  模拟器  待发批次 {sim_after['generated_batches']} 个")

        if state_after is not None:
            print(
                f"  链尾已复位为 last_sequence={state_after['last_sequence']}，"
                f"last_batch_number={state_after['last_batch_number']}，"
                f"last_hash={'全 0' if state_after['last_hash'] == ZERO_HASH else '非全 0'}"
            )
            if state_after["last_sequence"] != 0 or state_after["last_batch_number"] != 0:
                ok = False
            if state_after["last_hash"] != ZERO_HASH:
                ok = False
            if (
                state_after["public_key"] != state["public_key"]
                or state_after["device_type"] != state["device_type"]
            ):
                print("  复位后公钥或类型与复位前不一致。", file=sys.stderr)
                ok = False

        center.close()
        simulator.close()

        (out_dir / f"result-after-{device_id}-cleanup-{stamp}.json").write_text(
            json.dumps(
                {
                    "device_id": device_id,
                    "created_at": now_stamp().isoformat(timespec="seconds"),
                    "backups": saved,
                    "removed": {
                        "center_batches": removed_batches,
                        "center_records": removed_records,
                        "center_device": removed_device,
                        "center_audits": removed_audits,
                        "simulator_pending": removed_pending,
                        "simulator_state_reset": removed_state,
                    },
                    "center_after": center_after,
                    "simulator_after": sim_after,
                    "simulator_state_after": state_after,
                    "verified": ok,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        if not ok:
            print("\n复核未通过，请用备份目录里的文件回退。", file=sys.stderr)
            return 1

        print("\n清理完成，复核通过。")
        print("下一步：启动中心后由 A 重发该设备，从 WL-001-000001、序号 1 重新开始。")
        if staged:
            print(
                "  注意：备份暂存在工作区根目录，请移到 "
                f"{center_db.parent} 下：{out_dir.name}"
            )
        print(
            "  回退（覆盖回去即可）："
            + str(out_dir / f"{center_db.name}.bak-{stamp}")
            + "  ->  "
            + str(center_db)
        )
        print(
            "  同时回退模拟器状态："
            + str(out_dir / f"{simulator_db.name}.bak-{stamp}")
            + "  ->  "
            + str(simulator_db)
        )
        return 0
    finally:
        try:
            center.close()
        except Exception:
            pass
        try:
            simulator.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
