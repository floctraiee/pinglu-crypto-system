from common_crypto.crypto_utils import (
    sm3_hex,
    generate_keypair,
    sign_message,
    verify_message,
)

# 检查 SM3
assert sm3_hex(b"abc") == (
    "66c7f0f462eeedd9d1f2d46bdc10e4e2"
    "4167c4875cf2f7a2297da02b8f4ba8e0"
)

# 检查签名及验签
private_key, public_key = generate_keypair()
message = b"device_001|batch_001|water_level=3.25"

signature = sign_message(message, private_key, public_key)

assert verify_message(message, signature, public_key)
assert not verify_message(b"changed data", signature, public_key)

# 检查其他设备的公钥不能通过验签
_, other_public_key = generate_keypair()
assert not verify_message(message, signature, other_public_key)

print("SM3 摘要：通过")
print("SM2 正常验签：通过")
print("修改数据检测：通过")
print("错误公钥检测：通过")
print("公共密码模块验证通过")