"""生成 N 台设备的临时登记表、密钥与中心库，用于 100 台规模的实测。

为什么不直接用仓库里的 keys/ 和 test_data/：
- 正式登记表 test_data/registry_public.json 只有 8 台（七类各一台 + 一个历史遗留项）；
- keys/ 与 test_data/ 在受限环境下可能是只读的。

因此本脚本把**额外的合成设备**写到工作区内的临时目录，并按七类循环命名
（WL-001… 之类已存在时顺延编号），产出：
    <out>/keys/<device_id>.json          私钥（临时目录，不会提交）
    <out>/registry_public.json           合并后的公开登记表（不含私钥）
    <out>/synthetic_center.db            只登记 devices 的干净中心库

用法：
    python 验证脚本_建百台环境.py --devices 100
    python 验证脚本_建百台环境.py --devices 100 --out _tmp/stress100
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from common_crypto.sm import generate_keypair, sign_message, verify_message  # noqa: E402

# 与 device_simulator/register.py 保持一致的七类顺序
DEVICE_TYPES = (
    ("water_level", "WL"),
    ("weather", "WE"),
    ("navigation_mark", "NM"),
    ("slope", "SL"),
    ("lock", "LK"),
    ("drone", "DR"),
    ("survey_boat", "SB"),
)

BASE_REGISTRY = ROOT / "test_data" / "registry_public.json"


def main():
    parser = argparse.ArgumentParser(description="生成 N 台设备的临时登记表与中心库")
    parser.add_argument("--devices", type=int, default=100)
    parser.add_argument("--out", type=Path, default=ROOT / "_tmp" / "stress100")
    parser.add_argument("--fresh", action="store_true",
                        help="清掉输出目录后重建（默认保留已生成的密钥）")
    parser.add_argument("--skip", default="WL-900",
                        help="逗号分隔，排除正式登记表里不可用的历史设备")
    args = parser.parse_args()

    out = args.out if args.out.is_absolute() else ROOT / args.out
    keys_dir = out / "keys"
    registry_path = out / "registry_public.json"

    if args.fresh and out.exists():
        shutil.rmtree(out, ignore_errors=True)

    keys_dir.mkdir(parents=True, exist_ok=True)

    skip = {item.strip() for item in args.skip.split(",") if item.strip()}

    # ---- 先并入正式登记表里的可用设备 ----
    registry = {}
    copied = 0

    if BASE_REGISTRY.exists():
        base = json.loads(BASE_REGISTRY.read_text(encoding="utf-8"))

        for device_id, item in sorted(base.items()):
            if device_id in skip:
                continue

            # 正式密钥可能与本脚本目录不同步；这里统一复制一份到临时目录，
            # 保证"登记表 ↔ 私钥"在临时环境内一定自洽。
            source = ROOT / "keys" / f"{device_id}.json"

            if not source.exists():
                continue

            saved = json.loads(source.read_text(encoding="utf-8"))
            probe = f"{device_id} env check".encode("utf-8")

            if saved.get("public_key") != item.get("public_key"):
                print(f"  跳过 {device_id}：正式密钥与登记表不一致")
                continue

            if not verify_message(
                saved["public_key"], probe,
                sign_message(saved["private_key"], saved["public_key"], probe),
            ):
                print(f"  跳过 {device_id}：正式密钥不自洽")
                continue

            (keys_dir / f"{device_id}.json").write_text(
                json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            registry[device_id] = item
            copied += 1

    print(f"已并入正式设备 {copied} 台")

    # ---- 按七类循环补齐到目标台数 ----
    numbers = {}

    for prefix in (prefix for _, prefix in DEVICE_TYPES):
        numbers[prefix] = 0

    for device_id in registry:
        prefix = device_id.split("-")[0]
        if prefix in numbers:
            try:
                numbers[prefix] = max(numbers[prefix], int(device_id.split("-")[1]))
            except (IndexError, ValueError):
                pass

    index = 0
    generated = 0

    while len(registry) < args.devices:
        device_type, prefix = DEVICE_TYPES[index % len(DEVICE_TYPES)]
        numbers[prefix] += 1
        device_id = f"{prefix}-{numbers[prefix]:03d}"
        index += 1

        if device_id in registry:
            continue

        key_path = keys_dir / f"{device_id}.json"

        if key_path.exists():
            saved = json.loads(key_path.read_text(encoding="utf-8"))
        else:
            private_key, public_key = generate_keypair()
            saved = {"private_key": private_key, "public_key": public_key}
            key_path.write_text(
                json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        probe = f"{device_id} env check".encode("utf-8")

        if not verify_message(
            saved["public_key"], probe,
            sign_message(saved["private_key"], saved["public_key"], probe),
        ):
            raise SystemExit(f"{device_id} 生成的密钥不自洽")

        registry[device_id] = {
            "device_id": device_id,
            "device_type": device_type,
            "longitude": "108.500000",
            "latitude": "22.000000",
            "public_key": saved["public_key"],
        }
        generated += 1

        if generated % 20 == 0:
            print(f"  已生成 {generated} 台合成设备…")

    print(f"已生成 {generated} 台合成设备，登记表合计 {len(registry)} 台")

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- 建一个只登记 devices 的干净中心库 ----
    center_db = out / "synthetic_center.db"

    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(center_db) + suffix)
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass

    from center_platform import storage  # noqa: E402

    storage.DB_PATH = center_db
    storage.init_db()

    for device_id, item in registry.items():
        storage.register_device(
            device_id, item["device_type"], item["public_key"],
            longitude=item.get("longitude"), latitude=item.get("latitude"),
        )

    stats = storage.get_stats()

    print("")
    print(f"临时登记表：{registry_path}")
    print(f"临时私钥目录：{keys_dir}")
    print(f"临时中心库：{center_db}")
    print(f"登记设备 {stats['devices']} 台，批次 {stats['batches']}，记录 {stats['records']}")
    print("")
    print("启动中心（换库即可，不动正式库）：")
    print(f'  $env:PINGLU_CENTER_DB="{center_db}"')
    print(f'  $env:PINGLU_SIMULATOR_DB="{out / "none.db"}"')
    print('  python -m uvicorn center_platform.main:app --host 127.0.0.1 --port 8150')
    print("跑压力（100 台 × 30 分钟）：")
    print(f'  python tests/stress_center.py --devices {args.devices} --minutes 30 --rate 1 \\')
    print(f'    --batch-size 20 --registry "{registry_path}" --keys-dir "{keys_dir}" \\')
    print("    --url http://127.0.0.1:8150 --label \"100台30分钟\"")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
