import json
from pathlib import Path

from common_crypto.sm import (
    generate_keypair,
    sign_message,
    verify_message,
)

ROOT = Path(__file__).resolve().parents[1]


def register_water_device():
    """创建或加载 WL-001 的固定密钥，返回登记信息和私钥。"""
    keys_dir = ROOT / "keys"
    keys_dir.mkdir(exist_ok=True)

    key_path = keys_dir / "WL-001.json"

    if key_path.exists():
        saved = json.loads(key_path.read_text(encoding="utf-8"))
        private_key = saved["private_key"]
        public_key = saved["public_key"]
    else:
        private_key, public_key = generate_keypair()

        # x 模式避免覆盖已经存在的密钥文件
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

    # 检查保存的私钥与公钥是否配套
    message = b"WL-001 keypair check"
    signature = sign_message(private_key, public_key, message)

    if not verify_message(public_key, message, signature):
        raise ValueError("设备密钥不配套，请检查 keys/WL-001.json")

    registration = {
        "device_id": "WL-001",
        "device_type": "water_level",
        "longitude": "108.500000",
        "latitude": "22.000000",
        "public_key": public_key,
    }

    return registration, private_key