"""把设备公钥登记文件导入中心 devices 表。

中心目前**没有** POST /devices 接口，也没有导入脚本；而 devices 表是空的，
任何批次都会被判 UNKNOWN_DEVICE。本脚本补上这一环。

在仓库根目录执行（必须与中心服务用同一个 center_platform/data/center.db）：
    .venv\\Scripts\\python.exe tools\\import_registry.py

只打印公钥指纹、用于与队长/队员A 核对（contract.md §7）：
    .venv\\Scripts\\python.exe tools\\import_registry.py --fingerprint-only

只看会导入什么，不写库：
    .venv\\Scripts\\python.exe tools\\import_registry.py --dry-run
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 允许从仓库根目录直接运行本文件
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_crypto.sm import sm3_hex  # noqa: E402
from center_platform import storage  # noqa: E402

DEFAULT_REGISTRY = ROOT / "test_data" / "registry_public.json"


def fingerprint(public_key):
    """公钥的 SM3 指纹，十六进制；格式不对时返回 None。"""
    if not isinstance(public_key, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", public_key):
        return None
    return sm3_hex(bytes.fromhex(public_key))


def load_registry(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} 不是非空的 设备ID->登记信息 对象")
    return data


def check_entry(device_id, item):
    """返回问题列表；空列表表示这条登记可用。"""
    problems = []
    if not isinstance(item, dict):
        return ["登记信息不是对象"]
    if item.get("device_id") != device_id:
        problems.append(f"device_id 字段（{item.get('device_id')!r}）与键名不一致")
    if not isinstance(item.get("device_type"), str) or not item["device_type"]:
        problems.append("device_type 缺失或不是字符串")
    if fingerprint(item.get("public_key")) is None:
        problems.append("public_key 不是 128 位十六进制字符串")
    return problems


def main():
    parser = argparse.ArgumentParser(description="导入设备公钥登记到中心")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY),
                        help=f"登记文件路径，默认 {DEFAULT_REGISTRY}")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写数据库")
    parser.add_argument("--fingerprint-only", action="store_true",
                        help="只打印公钥指纹，供三人核对，不写数据库")
    args = parser.parse_args()

    registry = load_registry(args.registry)

    print(f"登记文件: {args.registry}")
    print(f"设备数量: {len(registry)}")
    if not args.fingerprint_only:
        print(f"中心数据库: {storage.DB_PATH}")
    print()

    print(f"{'设备ID':<10} {'类型':<16} {'公钥前16位':<18} SM3 指纹")
    print("-" * 96)

    bad = {}
    for device_id, item in sorted(registry.items()):
        problems = check_entry(device_id, item)
        if problems:
            bad[device_id] = problems

        pub = item.get("public_key", "") if isinstance(item, dict) else ""
        fp = fingerprint(pub) or "(公钥格式错误)"
        kind = item.get("device_type", "?") if isinstance(item, dict) else "?"
        print(f"{device_id:<10} {kind:<16} {str(pub)[:16]:<18} {fp}")

    print()

    if bad:
        print("以下登记项有问题，未导入：")
        for device_id, problems in bad.items():
            print(f"  {device_id}: {'；'.join(problems)}")
        print()

    if args.fingerprint_only:
        print("以上为公钥 SM3 指纹，请与队长、队员A 逐条核对后确认（contract.md §7）。")
        return 1 if bad else 0

    if args.dry_run:
        print(f"[dry-run] 未写数据库。可导入 {len(registry) - len(bad)} 台。")
        return 1 if bad else 0

    if bad:
        print("存在问题的登记项，为安全起见本次不执行导入。请先修正。")
        return 1

    storage.init_db()
    for device_id, item in sorted(registry.items()):
        storage.register_device(
            device_id=device_id,
            device_type=item["device_type"],
            public_key=item["public_key"],
            fingerprint=fingerprint(item["public_key"]),
        )
        print(f"已登记 {device_id}")

    print()
    print(f"导入完成，共 {len(registry)} 台。中心 devices 表现有 {len(storage.list_devices())} 台。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
