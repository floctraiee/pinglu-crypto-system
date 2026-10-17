"""单机闭环演示：七台设备各生成一批合法批次，提交中心并复核证明路径。

用法：
    python demo_closed_loop.py                     # 七台各 10 条
    python demo_closed_loop.py --batch-size 5      # 自定义每批条数
    python demo_closed_loop.py --devices WL-001    # 只跑指定设备（可重复）
    python demo_closed_loop.py --url http://127.0.0.1:8000

做的事：
1. 读 test_data/registry_public.json 与 keys/<设备ID>.json，逐一验签确认配套；
2. 用 device_simulator 生成下一批（跨批次续链、批次号递增、状态存 simulator.db）；
3. POST /batches，打印中心判定；
4. 对入库批次调用证明接口，确认每条记录都能重新验证到签名根；
5. 打印 /stats 汇总。
"""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

from common_crypto.canonical import canonical_bytes, signed_header
from common_crypto.sm import sign_message, sm3_hex, verify_message
from device_simulator.batch import create_next_batch


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "test_data" / "registry_public.json"
KEYS_DIR = ROOT / "keys"
SIMULATOR_DB = ROOT / "simulator.db"


def load_registration(device_id):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    if device_id not in registry:
        raise ValueError(f"{device_id} 不在登记表里")

    registration = registry[device_id]
    key_path = KEYS_DIR / f"{device_id}.json"

    if not key_path.exists():
        raise ValueError(f"缺少密钥文件：{key_path}")

    saved = json.loads(key_path.read_text(encoding="utf-8"))

    if saved["public_key"] != registration["public_key"]:
        raise ValueError(f"{device_id} 的密钥文件公钥与登记表不一致")

    probe = f"{device_id} 提交前核对".encode("utf-8")
    if not verify_message(
        registration["public_key"],
        probe,
        sign_message(saved["private_key"], saved["public_key"], probe),
    ):
        raise ValueError(f"{device_id} 的私钥不能配出登记公钥")

    return registration, saved["private_key"]


def post_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--devices", nargs="*", default=None)
    args = parser.parse_args()

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    device_ids = args.devices or sorted(registry)

    summary = []

    for device_id in device_ids:
        registration, private_key = load_registration(device_id)

        batch = create_next_batch(
            registration,
            private_key,
            batch_size=args.batch_size,
            db_path=SIMULATOR_DB,
        )

        assert verify_message(
            registration["public_key"],
            canonical_bytes(signed_header(batch)),
            batch["signature"],
        ), f"{device_id} 生成的批次签名自检失败"

        status, result = post_json(f"{args.url}/batches", {"batch": batch})

        print(
            f"{device_id:<8} {batch['batch_id']:<20} "
            f"{batch['count']:>2} 条 序号 {batch['start_sequence']}~{batch['end_sequence']}  "
            f"HTTP {status}  {result.get('status')} / {result.get('reason_code')}"
        )

        item = {
            "device_id": device_id,
            "batch_id": batch["batch_id"],
            "count": batch["count"],
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "fingerprint": sm3_hex(bytes.fromhex(registration["public_key"])),
            "proof_ok": None,
        }

        if result.get("status") == "accepted":
            verified = 0

            for record in batch["records"]:
                proof = get_json(
                    f"{args.url}/batches/{batch['batch_id']}/proof/{record['sequence']}"
                )
                assert proof["verified"] is True, f"证明校验失败：{record['sequence']}"
                assert proof["root"] == batch["merkle_root"]
                verified += 1

            item["proof_ok"] = verified == batch["count"]
            print(f"         证明路径复核：{verified}/{batch['count']} 条 verified，签名根一致")

        summary.append(item)

    stats = get_json(f"{args.url}/stats")
    print()
    print("中心统计：", json.dumps(stats, ensure_ascii=False))

    output = ROOT / "test_data" / "closed_loop_result.json"
    output.write_text(
        json.dumps({"batches": summary, "stats": stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("闭环结果已写入：", output)

    accepted = [item for item in summary if item["status"] == "accepted"]
    print(f"本次 {len(accepted)}/{len(summary)} 批被中心接受")


if __name__ == "__main__":
    main()
