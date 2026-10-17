"""核对 keys/ 里的设备私钥与 test_data/registry_public.json 的登记公钥是否配套。

用法：
    python check_device_keys.py            # 逐台签名验签 + 打印 SM3 指纹
    python check_device_keys.py --json OUT # 顺便导出核对结果 JSON

只做核对，不修改任何文件；私钥不出现在输出里。
"""

import argparse
import json
from pathlib import Path

from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.sm import sign_message, sm3_hex, verify_message


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "test_data" / "registry_public.json"
KEYS_DIR = ROOT / "keys"


def check_one(device_id, registration):
    """返回一台设备的核对结果。"""
    result = {
        "device_id": device_id,
        "device_type": registration["device_type"],
        "registered_public_key": registration["public_key"],
        "registered_fingerprint": sm3_hex(bytes.fromhex(registration["public_key"])),
    }

    key_path = KEYS_DIR / f"{device_id}.json"

    if not key_path.exists():
        result.update(status="缺密钥文件", ok=False)
        return result

    saved = json.loads(key_path.read_text(encoding="utf-8"))

    if set(saved) != {"private_key", "public_key"}:
        result.update(status="密钥文件字段不是 private_key/public_key", ok=False)
        return result

    result["key_file_public_key"] = saved["public_key"]
    result["key_file_fingerprint"] = sm3_hex(bytes.fromhex(saved["public_key"]))

    if saved["public_key"] != registration["public_key"]:
        result.update(status="密钥文件公钥与登记表不一致", ok=False)
        return result

    # 用私钥真实签一次，再用登记公钥验签，确认这套密钥能用
    message = f"{device_id} 密钥核对".encode("utf-8")
    signature = sign_message(saved["private_key"], saved["public_key"], message)

    if not verify_message(registration["public_key"], message, signature):
        result.update(status="签名验签失败，密钥不配套", ok=False)
        return result

    result.update(status="配套，可签名", ok=True)
    return result


def check_all():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return [check_one(device_id, registry[device_id]) for device_id in sorted(registry)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None, help="把核对结果写到指定 JSON 文件")
    args = parser.parse_args()

    results = check_all()

    print(f"登记表：{REGISTRY_PATH}")
    print(f"密钥目录：{KEYS_DIR}")
    print()

    for item in results:
        mark = "通过" if item["ok"] else "不通过"
        print(f"[{mark}] {item['device_id']:<8} {item['device_type']:<16}")
        print(f"        登记公钥指纹 SM3：{item.get('registered_fingerprint')}")
        print(f"        密钥文件指纹 SM3：{item.get('key_file_fingerprint', '（无）')}")
        print(f"        结论：{item['status']}")

    passed = sum(1 for item in results if item["ok"])
    print()
    print(f"核对结果：{passed}/{len(results)} 台配套")

    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已写入：{args.json}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
