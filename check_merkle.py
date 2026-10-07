from common_crypto.crypto_utils import sm3_hex
from common_crypto.merkle import (
    merkle_root,
    merkle_proof,
    verify_proof,
)

# 先用十条简单数据检查算法
hashes = [
    sm3_hex(f"record_{i}".encode("utf-8"))
    for i in range(1, 11)
]

root = merkle_root(hashes)
print("Merkle 根：", root)

# 检查十条记录各自的证明
for index, record_hash in enumerate(hashes):
    proof = merkle_proof(hashes, index)
    assert verify_proof(record_hash, proof, root)

print("十条记录的证明验证：通过")

# 查看第三条记录的证明路径
proof = merkle_proof(hashes, 2)
print("第三条记录的证明路径：", proof)

# 修改第三条数据后，原证明应当失败
changed_hash = sm3_hex(b"changed_record")
assert not verify_proof(changed_hash, proof, root)
print("修改记录检测：通过")

# 另外检查单条和奇数条的情况
for count in (1, 3):
    subset = hashes[:count]
    subset_root = merkle_root(subset)

    for index, record_hash in enumerate(subset):
        proof = merkle_proof(subset, index)
        assert verify_proof(record_hash, proof, subset_root)

print("单条和奇数条记录验证：通过")
print("Merkle 树验证完成")