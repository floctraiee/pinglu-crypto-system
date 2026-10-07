import json
from pathlib import Path

from common_crypto.sm import (
    generate_keypair,
    sign_message,
    verify_message,
)

ROOT = Path(__file__).resolve().parents[1]


def register_water_device():
    """保留原来的水位登记入口，供样例脚本调用。"""
    return register_device("WL-001", "water_level")

from common_crypto.sm import sm3_hex


DEVICE_TYPES = (
    ("water_level", "WL"),
    ("weather", "WE"),
    ("navigation_mark", "NM"),
    ("slope", "SL"),
    ("lock", "LK"),
    ("drone", "DR"),
    ("survey_boat", "SB"),
)


def register_device(
    device_id,
    device_type,
    longitude="108.500000",
    latitude="22.000000",
    keys_dir=None,
):
    """为指定设备创建或加载固定密钥。"""
    prefixes = dict(DEVICE_TYPES)

    if device_type not in prefixes:
        raise ValueError("不支持的设备类型")

    prefix = prefixes[device_type] + "-"
    number = device_id[len(prefix):]

    if (
        not device_id.startswith(prefix)
        or len(number) < 3
        or any(char not in "0123456789" for char in number)
        or int(number) < 1
    ):
        raise ValueError("设备 ID 与类型不匹配")

    if keys_dir is None:
        keys_dir = ROOT / "keys"

    keys_dir = Path(keys_dir)
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_path = keys_dir / f"{device_id}.json"

    if key_path.exists():
        saved = json.loads(key_path.read_text(encoding="utf-8"))
        private_key = saved["private_key"]
        public_key = saved["public_key"]
    else:
        private_key, public_key = generate_keypair()

        with key_path.open("x", encoding="utf-8") as file:
            json.dump(
                {
                    "private_key": private_key,
                    "public_key": public_key,
                },
                file,
                ensure_ascii=False,
                indent=2,
            )

    message = f"{device_id} keypair check".encode("utf-8")
    signature = sign_message(private_key, public_key, message)

    if not verify_message(public_key, message, signature):
        raise ValueError(f"{device_id} 的设备密钥不配套")

    registration = {
        "device_id": device_id,
        "device_type": device_type,
        "longitude": longitude,
        "latitude": latitude,
        "public_key": public_key,
    }

    return registration, private_key


def register_devices(count=7, keys_dir=None, output_path=None):
    """按七种类型轮流登记设备，并导出公钥文件。"""
    if type(count) is not int or count < 1:
        raise ValueError("设备数必须是正整数")

    registry = {}
    numbers = {}

    for index in range(count):
        device_type, prefix = DEVICE_TYPES[index % len(DEVICE_TYPES)]
        numbers[prefix] = numbers.get(prefix, 0) + 1
        device_id = f"{prefix}-{numbers[prefix]:03d}"

        registration, _ = register_device(
            device_id,
            device_type,
            keys_dir=keys_dir,
        )

        # 导出的对象只包含登记信息，不包含私钥
        registry[device_id] = registration

    if output_path is None:
        output_path = ROOT / "test_data" / "registry_public.json"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output_path)

    return registry


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=7)
    args = parser.parse_args()

    registry = register_devices(count=args.devices)

    for device_id, registration in registry.items():
        fingerprint = sm3_hex(
            bytes.fromhex(registration["public_key"])
        )
        print(
            f"{device_id} | {registration['device_type']} "
            f"| 公钥SM3指纹：{fingerprint}"
        )

    print(f"已登记 {len(registry)} 台设备")
    print("公钥文件：test_data/registry_public.json")