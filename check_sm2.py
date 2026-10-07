import secrets
from gmssl import sm2

# 生成一对临时测试密钥
n = int(sm2.default_ecc_table["n"], 16)
d = secrets.randbelow(n - 2) + 1

signer = sm2.CryptSM2(
    private_key=f"{d:064x}",
    public_key=""
)
public_key = signer._kg(d, sm2.default_ecc_table["g"])
signer.public_key = public_key

# 验签方只需要公钥
verifier = sm2.CryptSM2(private_key="", public_key="")
verifier.public_key = public_key

# 对模拟数据签名
message = b"device_001|batch_001|water_level=3.25"

signature = None
while signature is None:
    k = secrets.randbelow(n - 1) + 1
    signature = signer.sign_with_sm3(message, f"{k:064x}")

# 检查原始数据
original_ok = verifier.verify_with_sm3(signature, message)

# 修改水位值，检查原签名是否还能通过
changed = b"device_001|batch_001|water_level=9.99"
changed_ok = verifier.verify_with_sm3(signature, changed)

print("原始数据验签：", original_ok)
print("修改后数据验签：", changed_ok)

assert original_ok, "原始数据验签失败"
assert not changed_ok, "未能发现数据被修改"
print("SM2 验证通过")