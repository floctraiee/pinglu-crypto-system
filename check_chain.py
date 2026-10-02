from copy import deepcopy
from common_crypto.hash_chain import (
    ZERO_HASH,
    record_hash,
    verify_chain,
)

records = []
previous = ZERO_HASH

# 生成同一台设备的三条模拟记录
for sequence in range(1, 4):
    record = {
        "device_id": "water_001",
        "device_type": "water_level",
        "timestamp": f"2026-10-03T00:00:0{sequence}+08:00",
        "longitude": "108.500000",
        "latitude": "22.000000",
        "sequence": sequence,
        "batch_id": "water_001_000001",
        "data": {
            "value": "3.25",
            "unit": "m",
            "status": "normal",
        },
        "previous_hash": previous,
    }

    record["record_hash"] = record_hash(record)
    records.append(record)
    previous = record["record_hash"]

# 检查正常记录
assert verify_chain(records) == []
print("正常摘要链：通过")

# 修改第二条记录的水位值
changed = deepcopy(records)
changed[1]["data"]["value"] = "9.99"

errors = verify_chain(changed)
assert any(error["sequence"] == 2 for error in errors)
print("修改记录检测：通过")
print("异常详情：", errors)

# 删除中间一条记录
deleted = deepcopy(records)
del deleted[1]
assert verify_chain(deleted)
print("中间记录删除检测：通过")

# 调换两条记录的顺序
reordered = deepcopy(records)
reordered[0], reordered[1] = reordered[1], reordered[0]
assert verify_chain(reordered)
print("记录乱序检测：通过")

print("摘要链验证完成")