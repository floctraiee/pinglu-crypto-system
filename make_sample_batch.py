import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common_crypto.sm import generate_keypair
from common_crypto.hash_chain import ZERO_HASH, record_hash
from common_crypto.batch import build_batch, verify_batch

# 生成样例用的临时密钥，私钥不保存
private_key, public_key = generate_keypair()

registration = {
    "device_id": "water_001",
    "device_type": "water_level",
    "public_key": public_key,
}

records = []
previous = ZERO_HASH
start_time = datetime(2026, 10, 3, tzinfo=timezone.utc)

for sequence in range(1, 11):
    record = {
        "device_id": "water_001",
        "device_type": "water_level",
        "batch_id": "water_001_000001",
        "sequence": sequence,
        "timestamp": (
            start_time + timedelta(seconds=sequence - 1)
        ).isoformat(),
        "longitude": "108.500000",
        "latitude": "22.000000",
        "data": {
            "value": f"{3.20 + sequence * 0.01:.2f}",
            "unit": "m",
            "status": "normal",
        },
        "previous_hash": previous,
    }

    record["record_hash"] = record_hash(record)
    records.append(record)
    previous = record["record_hash"]

batch = build_batch(records, private_key, public_key)
result = verify_batch(batch, registration)

print("验证结果：", result)
assert result["valid"], "正常批次验证失败"

# 文件保存到项目根目录下的 test_data
output = Path(__file__).resolve().parent / "test_data"
output.mkdir(exist_ok=True)

files = {
    "normal_batch.json": batch,
    "registry_public.json": {
        registration["device_id"]: registration
    },
}

for filename, content in files.items():
    (output / filename).write_text(
        json.dumps(content, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

print("完整批次验证通过")
print("样例目录：", output)