# 异常注入对照实验结果

样本总数：正常 100 份，异常 900 份

| 类别 | 样本 | 检出 | 定位正确 | 实际 reason_code |
|---|---|---|---|---|
| batch_count | 100 | 100 | 100 | INVALID_BATCH_RANGE×100 |
| forged_device | 100 | 100 | 100 | UNKNOWN_DEVICE×100 |
| hash_chain | 100 | 100 | 100 | INVALID_HASH_CHAIN×100 |
| insert_record | 100 | 100 | 100 | INVALID_BATCH_RANGE×100 |
| merkle_root | 100 | 100 | 100 | INVALID_MERKLE_ROOT×100 |
| record_payload | 100 | 100 | 100 | INVALID_RECORD_HASH×100 |
| reorder_records | 100 | 100 | 100 | INVALID_SEQUENCE×100 |
| signature | 100 | 100 | 100 | INVALID_SIGNATURE×100 |
| structure | 100 | 100 | 100 | INVALID_BATCH_STRUCTURE×100 |

| 指标 | 实测 | 目标 | 结论 |
|---|---|---|---|
| 检测率 | 100.00% | >= 98% | 达标 |
| 定位准确率 | 100.00% | >= 98% | 达标 |
| 误报率 | 0.00% | <= 2% | 达标 |
